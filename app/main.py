"""API REST del sistema: encola tareas, informa su estado y recibe aprobaciones humanas.

Uso: uvicorn app.main:app --port 8000
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, status
from langchain_core.retrievers import BaseRetriever
from langgraph.checkpoint.base import BaseCheckpointSaver
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.llm import SOLICITUD_DEMO, modo_demo
from app.observability import init_tracing
from app.rag.retriever import set_retriever, setup_rag
from app.worker import ConversacionInexistente, ConversacionOcupada, JobStatus, JobStore, Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


class TaskIn(BaseModel):
    solicitud: str = Field(default=SOLICITUD_DEMO, min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, description="Para continuar una conversación existente.")


class TaskCreated(BaseModel):
    job_id: str
    conversation_id: str
    status: JobStatus


class Approval(BaseModel):
    aprobado: bool = True
    comentario: str = ""


class TaskOut(BaseModel):
    job_id: str
    conversation_id: str
    turno: int
    status: JobStatus
    solicitud: str
    created_at: str
    updated_at: str
    resultado: dict[str, Any] | None = None
    aprobacion_pendiente: dict[str, Any] | None = None
    metricas: dict[str, Any] | None = None
    error: str | None = None


class ConversationOut(BaseModel):
    conversation_id: str
    turnos: list[TaskOut]


def _es_sondeo(scope: dict) -> bool:
    """Las consultas de estado (GET /tasks/{id}) y /health no generan trazas: son sondeos repetitivos
    que taparían en el dashboard las trazas de las ejecuciones."""
    return scope.get("method") == "GET" and (scope.get("path", "").startswith("/tasks/") or scope.get("path") == "/health")


def create_app(
    redis: Redis | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    retriever: BaseRetriever | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Crea la aplicación. En los tests se inyectan Redis falso, checkpointer en memoria y un recuperador."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_tracing(settings)
        set_retriever(retriever or await setup_rag(settings))  # indexa la documentación si hace falta
        cliente = redis or Redis.from_url(settings.redis_url)
        if checkpointer is None:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver

            async with AsyncRedisSaver.from_conn_string(settings.redis_url) as saver:
                await saver.asetup()  # crea los índices de RediSearch que usa el checkpointer
                async with _arrancar(app, cliente, saver):
                    yield
        else:
            async with _arrancar(app, cliente, checkpointer):
                yield

    @asynccontextmanager
    async def _arrancar(app: FastAPI, cliente: Redis, saver: BaseCheckpointSaver):
        app.state.store = JobStore(cliente)
        app.state.worker = Worker(app.state.store, saver, settings.worker_concurrency)
        await app.state.worker.recuperar()
        app.state.worker.start()
        logging.getLogger("intelligence").info("API lista: %d workers, modo demostración=%s",
                                               settings.worker_concurrency, modo_demo())
        try:
            yield
        finally:
            await app.state.worker.stop()

    app = FastAPI(title="Atlas Intelligence System", version="1.0.0", lifespan=lifespan,
                  telemetry={"exclude": _es_sondeo})

    @app.post("/tasks", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
    async def crear_tarea(tarea: TaskIn) -> TaskCreated:
        """Registra la tarea en Redis, la encola y devuelve el job_id sin esperar a que termine."""
        try:
            job = await app.state.store.create(tarea.solicitud, tarea.conversation_id)
        except ConversacionInexistente:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe la conversación {tarea.conversation_id}")
        except ConversacionOcupada:
            raise HTTPException(status.HTTP_409_CONFLICT, "La conversación ya tiene un trabajo en curso")
        await app.state.worker.submit(job["job_id"])
        return TaskCreated(job_id=job["job_id"], conversation_id=job["conversation_id"], status=JobStatus.PENDING)

    @app.get("/tasks/{job_id}", response_model=TaskOut)
    async def consultar_tarea(job_id: str) -> TaskOut:
        """Estado actual del trabajo: PENDING, RUNNING, WAITING_APPROVAL, DONE o FAILED."""
        job = await app.state.store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el trabajo {job_id}")
        return TaskOut(**job)

    @app.post("/tasks/{job_id}/approve", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
    async def aprobar_tarea(job_id: str, decision: Approval) -> TaskCreated:
        """Decisión del operador sobre una tarea crítica: reanuda el grafo desde el punto de pausa."""
        job = await app.state.store.get(job_id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe el trabajo {job_id}")
        if job["status"] != JobStatus.WAITING_APPROVAL:
            raise HTTPException(status.HTTP_409_CONFLICT, f"El trabajo está en {job['status']}, no espera aprobación")
        await app.state.worker.resume(job_id, decision.model_dump())
        return TaskCreated(job_id=job_id, conversation_id=job["conversation_id"], status=JobStatus.RUNNING)

    @app.get("/conversations/{conversation_id}", response_model=ConversationOut)
    async def consultar_conversacion(conversation_id: str) -> ConversationOut:
        """Todos los turnos de una conversación, en orden."""
        turnos = await app.state.store.conversacion(conversation_id)
        if turnos is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe la conversación {conversation_id}")
        return ConversationOut(conversation_id=conversation_id, turnos=[TaskOut(**t) for t in turnos])

    @app.get("/health")
    async def salud() -> dict:
        await app.state.store.redis.ping()
        return {"status": "ok", "modo_demo": modo_demo()}

    return app


app = create_app()
