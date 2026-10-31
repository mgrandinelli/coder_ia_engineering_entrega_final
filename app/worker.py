"""Trabajos en segundo plano. Todo vive en Redis, así un reinicio de la API no pierde nada:

- `job:<id>`: estado de cada trabajo.
- `conv:<id>:jobs`: trabajos de una conversación, en orden.
- `conv:<id>:activo`: trabajo en curso de una conversación (uno por vez).
- `cola:trabajos`: trabajos por ejecutar. `cola:procesando`: los que un worker tiene tomados.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.graph import RECURSION_LIMIT, build_graph
from app.observability import MetricsCallback, traced

log = logging.getLogger("intelligence.worker")

JOB_TTL_S = 7 * 24 * 3600  # los trabajos se conservan una semana en Redis
COLA = "cola:trabajos"
PROCESANDO = "cola:procesando"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"


class ConversacionInexistente(Exception):
    pass


class ConversacionOcupada(Exception):
    """La conversación ya tiene un trabajo en curso: dos a la vez mezclarían sus mensajes."""


def _texto(valor: bytes | str) -> str:
    return valor.decode() if isinstance(valor, bytes) else valor


class JobStore:
    """Estado de los trabajos y de las conversaciones en Redis."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    @staticmethod
    def _clave(job_id: str) -> str:
        return f"job:{job_id}"

    async def create(self, solicitud: str, conversation_id: str | None = None) -> dict:
        """Registra un trabajo. Sin conversation_id empieza una conversación nueva."""
        job_id = uuid.uuid4().hex
        nueva = conversation_id is None
        conversation_id = conversation_id or uuid.uuid4().hex
        lista = f"conv:{conversation_id}:jobs"
        if not nueva and not await self.redis.exists(lista):
            raise ConversacionInexistente(conversation_id)
        if not await self.redis.set(f"conv:{conversation_id}:activo", job_id, nx=True, ex=JOB_TTL_S):
            raise ConversacionOcupada(conversation_id)

        turno = await self.redis.rpush(lista, job_id)
        await self.redis.expire(lista, JOB_TTL_S)
        ahora = datetime.now(UTC).isoformat()
        job = {"job_id": job_id, "conversation_id": conversation_id, "turno": turno, "status": JobStatus.PENDING,
               "solicitud": solicitud, "created_at": ahora, "updated_at": ahora}
        await self.redis.hset(self._clave(job_id), mapping=job)
        await self.redis.expire(self._clave(job_id), JOB_TTL_S)
        return job

    async def update(self, job_id: str, status: JobStatus, **campos: Any) -> None:
        valores = {k: json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for k, v in campos.items()}
        await self.redis.hset(self._clave(job_id), mapping={
            "status": status, "updated_at": datetime.now(UTC).isoformat(), **valores})

    async def get(self, job_id: str) -> dict | None:
        datos = await self.redis.hgetall(self._clave(job_id))
        if not datos:
            return None
        datos = {_texto(k): _texto(v) for k, v in datos.items()}
        datos["turno"] = int(datos["turno"])
        for campo in ("resultado", "aprobacion_pendiente", "metricas"):
            if campo in datos:
                datos[campo] = json.loads(datos[campo])
        return datos

    async def liberar(self, conversation_id: str) -> None:
        await self.redis.delete(f"conv:{conversation_id}:activo")

    async def conversacion(self, conversation_id: str) -> list[dict] | None:
        ids = await self.redis.lrange(f"conv:{conversation_id}:jobs", 0, -1)
        return [await self.get(_texto(i)) for i in ids] or None


def _sumar(previo: dict, actual: dict) -> dict:
    """Suma las métricas de los tramos de un trabajo (antes y después de una pausa para aprobación)."""
    return {k: round(previo.get(k, 0) + v, 3) if isinstance(v, float) else previo.get(k, 0) + v
            for k, v in actual.items()}


class Worker:
    """N tareas asíncronas que consumen la cola de Redis. Cada conversación es un hilo de LangGraph
    (thread_id = conversation_id), con su estado en el checkpointer de Redis."""

    def __init__(self, store: JobStore, checkpointer: BaseCheckpointSaver, concurrencia: int = 5) -> None:
        self.store = store
        self.redis = store.redis
        self.checkpointer = checkpointer
        self.concurrencia = concurrencia
        self._tareas: list[asyncio.Task] = []

    async def recuperar(self) -> int:
        """Al arrancar: lo que quedó tomado por un worker antes de un reinicio vuelve al frente de la cola."""
        pendientes = await self.redis.lrange(PROCESANDO, 0, -1)
        for crudo in pendientes:
            await self.redis.lrem(PROCESANDO, 1, crudo)
            await self.redis.lpush(COLA, json.dumps({**json.loads(crudo), "recuperado": True}))
        if pendientes:
            log.warning("Se recuperaron %d trabajos interrumpidos por un reinicio", len(pendientes))
        return len(pendientes)

    def start(self) -> None:
        self._tareas = [asyncio.create_task(self._consumir(), name=f"worker-{i}") for i in range(self.concurrencia)]

    async def stop(self) -> None:
        for tarea in self._tareas:
            tarea.cancel()
        await asyncio.gather(*self._tareas, return_exceptions=True)

    async def submit(self, job_id: str) -> None:
        await self.redis.rpush(COLA, json.dumps({"job_id": job_id}))

    async def resume(self, job_id: str, decision: dict) -> None:
        await self.store.update(job_id, JobStatus.RUNNING)
        await self.redis.rpush(COLA, json.dumps({"job_id": job_id, "decision": decision}))

    async def _consumir(self) -> None:
        while True:
            try:
                crudo = await self.redis.blmove(COLA, PROCESANDO, 1, "LEFT", "RIGHT")
                if crudo is None:
                    await asyncio.sleep(0.05)  # cede el control aunque el cliente no bloquee esperando
                    continue
                trabajo = json.loads(crudo)
                await self._run(trabajo["job_id"], trabajo)
                await self.redis.lrem(PROCESANDO, 1, crudo)
            except RedisConnectionError:
                log.warning("Redis no responde; se reintenta en 1 s")  # por ejemplo, durante un reinicio
                await asyncio.sleep(1)

    async def _preparar(self, job: dict, trabajo: dict, config: dict) -> tuple[Any, Any]:
        """Devuelve el grafo y su entrada: una solicitud nueva, una decisión humana o nada (sigue desde el checkpoint)."""
        seguimiento = job["turno"] > 1
        graph = build_graph(self.checkpointer, seguimiento=seguimiento)
        if "decision" in trabajo:
            return graph, Command(resume=trabajo["decision"])
        if trabajo.get("recuperado"):
            guardado = await graph.aget_state(config)
            if guardado.next:
                log.warning("job=%s continúa desde su último checkpoint", job["job_id"])
                return build_graph(self.checkpointer, seguimiento=seguimiento, avance=guardado.values), None
        return graph, {"messages": [HumanMessage(content=job["solicitud"])]}

    @traced("job")
    async def _run(self, job_id: str, trabajo: dict) -> None:
        job = await self.store.get(job_id)
        metricas = MetricsCallback(job_id)
        inicio = time.perf_counter()
        previo = job.get("metricas") or {}  # tramo anterior a una pausa
        await self.store.update(job_id, JobStatus.RUNNING)
        try:
            config = {"configurable": {"thread_id": job["conversation_id"]}, "recursion_limit": RECURSION_LIMIT,
                      "callbacks": [metricas], "metadata": {"job_id": job_id}}
            graph, entrada = await self._preparar(job, trabajo, config)
            estado = await graph.ainvoke(entrada, config)
        except RedisConnectionError:
            raise  # no es una falla del trabajo: sigue en cola:procesando y se recupera
        except Exception as exc:
            # Sin esto, el cliente quedaría consultando un trabajo que nunca termina.
            log.exception("job=%s falló", job_id)
            await self.store.update(job_id, JobStatus.FAILED, error=f"{type(exc).__name__}: {exc}",
                                    metricas=_sumar(previo, metricas.resumen()))
            await self.store.liberar(job["conversation_id"])
            return

        acumuladas = _sumar(previo, {**metricas.resumen(), "duracion_s": round(time.perf_counter() - inicio, 3)})
        duracion = acumuladas["duracion_s"]
        if "__interrupt__" in estado:
            pausa = estado["__interrupt__"][0].value
            log.info("job=%s esperando aprobación humana: %s", job_id, pausa["motivo"])
            await self.store.update(job_id, JobStatus.WAITING_APPROVAL, aprobacion_pendiente=pausa,
                                    metricas=acumuladas)
            return

        resultado = {
            "respuesta": estado["messages"][-1].content,
            "contributions": estado["contributions"],
            "acciones": estado.get("actions", []),
            "aprobacion": estado.get("approval"),
            "mensajes_en_conversacion": len(estado["messages"]),
        }
        await self.store.update(job_id, JobStatus.DONE, resultado=resultado, metricas=acumuladas)
        await self.store.liberar(job["conversation_id"])
        log.info("job=%s terminado en %.3fs", job_id, duracion)
