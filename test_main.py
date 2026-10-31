"""Tests de la entrega final. No usan la red, claves, Redis, ChromaDB ni Phoenix reales: Redis se reemplaza
por fakeredis, el checkpointer por uno en memoria, la base vectorial por un almacén en memoria y los modelos por guiones.

Ejecutar con: pytest -v
"""

import asyncio
import os
import time

os.environ["PHOENIX_ENABLED"] = "false"
os.environ.pop("OPENAI_API_KEY", None)
os.environ["LLM_PROVIDER"] = "openai"

import fakeredis
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.vectorstores import InMemoryVectorStore
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError

import app.worker as worker_mod
from app import observability
from app.graph import build_graph
from app.hitl import evaluar_criticidad
from app.llm import PROVEEDORES, SOLICITUD_CRITICA, SOLICITUD_DEMO, SOLICITUD_SEGUIMIENTO, build_llm, guiones
from app.agents.analyst_agent import PresupuestoInput, presupuesto_de_error
from app.agents.research_agent import BusquedaInput, buscar_documentacion
from app.config import Settings
from app.main import create_app
from app.rag import ingest as ingesta
from app.rag.retriever import ChromaVectorRetriever, build_hybrid_retriever, set_retriever
from app.state import historial_previo, ultima_solicitud
from app.worker import COLA, PROCESANDO, JobStatus, JobStore, Worker

FINALES = {"DONE", "FAILED", "WAITING_APPROVAL"}
SETTINGS = Settings(phoenix_enabled=False)
FRAGMENTOS = ingesta.fragmentar(ingesta.cargar_documentos())


def recuperador_de_prueba():
    """Híbrido real (BM25 + vectorial) con un almacén en memoria en lugar de ChromaDB."""
    almacen = InMemoryVectorStore(DeterministicFakeEmbedding(size=32))
    almacen.add_documents(FRAGMENTOS)
    return build_hybrid_retriever(FRAGMENTOS, almacen.as_retriever(search_kwargs={"k": 5}), k=5)


@pytest.fixture
def client():
    app = create_app(redis=fakeredis.aioredis.FakeRedis(), checkpointer=InMemorySaver(),
                     retriever=recuperador_de_prueba(), settings=SETTINGS)
    with TestClient(app) as cliente:
        yield cliente


def esperar(client: TestClient, job_id: str, estados=FINALES, limite_s: float = 10) -> dict:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        job = client.get(f"/tasks/{job_id}").json()
        if job["status"] in estados:
            return job
        time.sleep(0.05)
    raise AssertionError(f"el trabajo {job_id} no llegó a {estados}")


def crear(client: TestClient, solicitud: str = SOLICITUD_DEMO) -> str:
    respuesta = client.post("/tasks", json={"solicitud": solicitud})
    assert respuesta.status_code == 202
    return respuesta.json()["job_id"]


# --- Orquestación asíncrona y API ---


def test_post_encola_y_devuelve_job_id_sin_esperar_al_grafo(client, monkeypatch):
    construir = worker_mod.build_graph

    def grafo_lento(checkpointer, seguimiento=False, avance=None):
        grafo = construir(checkpointer)
        original = grafo.ainvoke

        async def lento(*args, **kwargs):
            await asyncio.sleep(1)
            return await original(*args, **kwargs)

        grafo.ainvoke = lento
        return grafo

    monkeypatch.setattr(worker_mod, "build_graph", grafo_lento)
    inicio = time.perf_counter()
    respuesta = client.post("/tasks", json={"solicitud": SOLICITUD_DEMO})
    demora = time.perf_counter() - inicio

    assert respuesta.status_code == 202 and demora < 0.5
    assert respuesta.json()["status"] == "PENDING"
    assert client.get(f"/tasks/{respuesta.json()['job_id']}").json()["status"] in ("PENDING", "RUNNING")


def test_una_tarea_comun_termina_en_done_con_resultado_y_metricas(client):
    job = esperar(client, crear(client))

    assert job["status"] == "DONE"
    assert "21,6 minutos" in job["resultado"]["respuesta"]
    assert [c["agent"] for c in job["resultado"]["contributions"]] == [
        "research_agent", "analyst_agent", "analyst_agent", "synthesizer"]
    assert job["metricas"]["llamadas_modelo"] >= 1 and job["metricas"]["duracion_s"] >= 0


def test_cinco_peticiones_concurrentes_terminan_todas(client):
    ids = [crear(client) for _ in range(5)]

    assert [esperar(client, i)["status"] for i in ids] == ["DONE"] * 5


def test_trabajo_inexistente_devuelve_404(client):
    assert client.get("/tasks/no-existe").status_code == 404
    assert client.post("/tasks/no-existe/approve", json={"aprobado": True}).status_code == 404


def test_solicitud_vacia_se_rechaza(client):
    assert client.post("/tasks", json={"solicitud": ""}).status_code == 422


# --- Persistencia con Redis ---


def test_job_store_guarda_y_recupera_el_estado():
    async def probar():
        redis = fakeredis.aioredis.FakeRedis()
        store = JobStore(redis)
        job_id = (await store.create("pregunta"))["job_id"]
        await store.update(job_id, JobStatus.DONE, resultado={"respuesta": "ok"}, metricas={"tokens_entrada": 3})
        return await store.get(job_id), await redis.ttl(f"job:{job_id}")

    job, ttl = asyncio.run(probar())

    assert job["status"] == "DONE" and job["resultado"] == {"respuesta": "ok"} and job["metricas"]["tokens_entrada"] == 3
    assert ttl > 0


def test_si_el_agente_falla_el_trabajo_queda_en_failed(client, monkeypatch):
    def grafo_roto(checkpointer, seguimiento=False, avance=None):
        raise ConnectionError("sin red hacia el proveedor")

    monkeypatch.setattr(worker_mod, "build_graph", grafo_roto)
    job = esperar(client, crear(client))

    assert job["status"] == "FAILED"
    assert job["error"] == "ConnectionError: sin red hacia el proveedor"


def test_una_salida_inesperada_del_modelo_termina_en_failed(client, monkeypatch):
    # Caso de borde: el supervisor se queda sin decisiones (equivale a una salida que no se pudo interpretar).
    def sin_decisiones(checkpointer, seguimiento=False, avance=None):
        modelos = guiones()
        modelos["supervisor"].decisiones = iter(())
        return build_graph(checkpointer, modelos)

    monkeypatch.setattr(worker_mod, "build_graph", sin_decisiones)
    job = esperar(client, crear(client))

    assert job["status"] == "FAILED"


# --- Human-in-the-loop ---


@pytest.mark.parametrize("solicitud, critica", [
    (SOLICITUD_DEMO, False),
    (SOLICITUD_CRITICA, True),
    ("Suspender los despliegues.", True),
    ("¿Corresponde suspender los despliegues?", False),
])
def test_evaluar_criticidad(solicitud, critica):
    assert (evaluar_criticidad(solicitud) is not None) == critica


def test_una_tarea_critica_se_pausa_hasta_la_aprobacion(client):
    job_id = crear(client, SOLICITUD_CRITICA)
    pausado = esperar(client, job_id)

    assert pausado["status"] == "WAITING_APPROVAL"
    assert pausado["aprobacion_pendiente"]["motivo"] == "Enviar una notificación a un canal del equipo"

    assert client.post(f"/tasks/{job_id}/approve", json={"aprobado": True, "comentario": "ok"}).status_code == 202
    final = esperar(client, job_id, {"DONE", "FAILED"})

    assert final["status"] == "DONE"
    assert final["resultado"]["acciones"][0]["canal"] == "#atlas-despliegues"
    assert "Se notificó el resultado en #atlas-despliegues" in final["resultado"]["respuesta"]


def test_si_el_operador_rechaza_no_se_ejecuta_la_accion(client):
    job_id = crear(client, SOLICITUD_CRITICA)
    esperar(client, job_id)

    client.post(f"/tasks/{job_id}/approve", json={"aprobado": False, "comentario": "no notificar"})
    final = esperar(client, job_id, {"DONE", "FAILED"})

    assert final["resultado"]["acciones"] == []
    assert "el operador la rechazó" in final["resultado"]["respuesta"]


def test_no_se_puede_aprobar_una_tarea_que_no_espera_aprobacion(client):
    job_id = crear(client)
    esperar(client, job_id)

    assert client.post(f"/tasks/{job_id}/approve", json={"aprobado": True}).status_code == 409


def test_el_grafo_pasa_por_la_aprobacion_humana_antes_de_cerrar():
    aristas = {(a.source, a.target) for a in build_graph(InMemorySaver(), guiones()).get_graph().edges}

    assert ("supervisor", "aprobacion_humana") in aristas
    assert ("aprobacion_humana", "synthesizer") in aristas
    assert ("supervisor", "synthesizer") not in aristas


# --- Observabilidad ---


def test_metrics_callback_registra_latencia_y_tokens():
    metricas = observability.MetricsCallback("job-1")
    mensaje = AIMessage(content="x", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})

    async def probar():
        from uuid import uuid4
        run_id = uuid4()
        await metricas.on_chat_model_start({}, [[]], run_id=run_id)
        await metricas.on_llm_end(LLMResult(generations=[[ChatGeneration(message=mensaje)]]), run_id=run_id)

    asyncio.run(probar())

    assert metricas.resumen()["tokens_entrada"] == 120 and metricas.resumen()["tokens_salida"] == 30
    assert metricas.resumen()["llamadas_modelo"] == 1


def test_sin_phoenix_la_instrumentacion_queda_desactivada():
    assert observability.init_tracing(SETTINGS) is False


def test_las_claves_no_estan_en_el_repositorio():
    from pathlib import Path

    base = Path(__file__).parent
    assert "OPENAI_API_KEY=\n" in (base / ".env.example").read_text()
    assert "REDIS_URL=" in (base / ".env.example").read_text()
    assert ".env" in (base / ".gitignore").read_text().split()


# --- RAG: ingesta y recuperación híbrida ---


class ColeccionFalsa:
    """Imita la parte de la colección asíncrona de ChromaDB que usan la ingesta y el recuperador."""

    def __init__(self, existentes: list[str] | None = None):
        self.existentes, self.upserts, self.borrados, self.consultas = existentes or [], [], [], []

    async def get(self, include):
        return {"ids": self.existentes}

    async def upsert(self, ids, embeddings, documents, metadatas):
        self.upserts.append(ids)

    async def delete(self, ids):
        self.borrados.extend(ids)

    async def query(self, query_embeddings, n_results, include):
        self.consultas.append(n_results)
        return {"ids": [["api#2#x"]], "documents": [["texto"]], "metadatas": [[{"fuente": "api.md"}]],
                "distances": [[0.25]]}


def test_cada_fragmento_cumple_el_esquema_de_metadatos():
    assert len(FRAGMENTOS) >= 19
    for fragmento in FRAGMENTOS:
        ingesta.ChunkMetadata(**fragmento.metadata)


def test_la_ingesta_no_reindexa_si_nada_cambio():
    coleccion = ColeccionFalsa(existentes=[f.id for f in FRAGMENTOS])

    asyncio.run(ingesta.ingest(coleccion, DeterministicFakeEmbedding(size=8), FRAGMENTOS))

    assert coleccion.upserts == [] and coleccion.borrados == []


def test_la_ingesta_sincroniza_lo_nuevo_y_lo_obsoleto():
    # Un fragmento quedó viejo en la colección y falta el primero de los actuales.
    coleccion = ColeccionFalsa(existentes=[f.id for f in FRAGMENTOS[1:]] + ["viejo#1#abc"])

    total = asyncio.run(ingesta.ingest(coleccion, DeterministicFakeEmbedding(size=8), FRAGMENTOS))

    assert total == len(FRAGMENTOS)
    assert coleccion.upserts == [[FRAGMENTOS[0].id]]
    assert coleccion.borrados == ["viejo#1#abc"]


def test_el_recuperador_de_chroma_es_asincrono_y_devuelve_similitud():
    coleccion = ColeccionFalsa()
    recuperador = ChromaVectorRetriever(collection=coleccion, embeddings=DeterministicFakeEmbedding(size=8), k=3)

    documentos = asyncio.run(recuperador.ainvoke("consulta"))

    assert documentos[0].metadata == {"fuente": "api.md", "similitud": 0.75}
    assert coleccion.consultas == [3]
    with pytest.raises(NotImplementedError):
        recuperador.invoke("consulta")


def test_la_busqueda_hibrida_encuentra_un_codigo_exacto():
    documentos = asyncio.run(recuperador_de_prueba().ainvoke("ATL-4012"))

    assert any("ATL-4012" in d.page_content for d in documentos)


def test_la_herramienta_de_busqueda_devuelve_fragmentos_con_fuente():
    set_retriever(recuperador_de_prueba())

    resultado = asyncio.run(buscar_documentacion.ainvoke({"consulta": "objetivo de disponibilidad presupuesto de error"}))

    assert "[observabilidad.md · Objetivos de nivel de servicio]" in resultado


# --- Validación con Pydantic ---


@pytest.mark.parametrize("modelo, datos", [
    (BusquedaInput, {"consulta": "ab"}),
    (PresupuestoInput, {"slo_porcentaje": 100, "ventana_dias": 30, "minutos_de_caida": 1}),
    (PresupuestoInput, {"slo_porcentaje": 99.9, "ventana_dias": 0, "minutos_de_caida": 1}),
    (PresupuestoInput, {"slo_porcentaje": 99.9, "ventana_dias": 30, "minutos_de_caida": -1}),
])
def test_las_entradas_de_las_herramientas_se_validan(modelo, datos):
    with pytest.raises(ValidationError):
        modelo(**datos)


def test_la_salida_del_presupuesto_de_error_es_un_modelo_validado():
    resultado = presupuesto_de_error.invoke({"slo_porcentaje": 99.95, "ventana_dias": 30, "minutos_de_caida": 12})

    assert resultado == {"minutos_permitidos": 21.6, "minutos_consumidos": 12.0,
                         "porcentaje_consumido": 55.56, "minutos_restantes": 9.6}


def test_la_configuracion_se_valida_y_separa_colecciones_por_modelo():
    with pytest.raises(ValidationError):
        Settings(top_k=0)
    assert Settings(embeddings_provider="local").collection_name == "atlas_docs_local"
    assert Settings(embeddings_provider="openai").collection_name == "atlas_docs_openai"


# --- Conversaciones ---


def seguir(client: TestClient, conversation_id: str, solicitud: str = SOLICITUD_SEGUIMIENTO):
    return client.post("/tasks", json={"solicitud": solicitud, "conversation_id": conversation_id})


def test_una_conversacion_continua_en_el_mismo_hilo(client):
    primero = esperar(client, crear(client))
    respuesta = seguir(client, primero["conversation_id"])
    segundo = esperar(client, respuesta.json()["job_id"])

    assert respuesta.json()["conversation_id"] == primero["conversation_id"]
    assert segundo["status"] == "DONE" and segundo["turno"] == 2
    assert "83,33 %" in segundo["resultado"]["respuesta"]
    # Cada turno tiene sus propios aportes: los del anterior no se arrastran.
    assert [c["agent"] for c in segundo["resultado"]["contributions"]] == ["analyst_agent", "synthesizer"]


def test_el_historial_de_la_conversacion_se_consulta_por_api(client):
    primero = esperar(client, crear(client))
    esperar(client, seguir(client, primero["conversation_id"]).json()["job_id"])

    turnos = client.get(f"/conversations/{primero['conversation_id']}").json()["turnos"]

    assert [t["turno"] for t in turnos] == [1, 2]
    assert turnos[1]["solicitud"] == SOLICITUD_SEGUIMIENTO


def test_el_segundo_turno_recibe_la_conversacion_previa():
    from langchain_core.messages import HumanMessage

    mensajes = [HumanMessage(content="Primera"), AIMessage(content="Delego", name="supervisor"),
                AIMessage(content="Respuesta", name="synthesizer"), HumanMessage(content="Segunda")]

    assert ultima_solicitud(mensajes) == "Segunda"
    assert historial_previo(mensajes) == "Usuario: Primera\nAsistente: Respuesta"
    assert historial_previo(mensajes[:1]) == ""


def test_conversacion_inexistente_devuelve_404(client):
    assert seguir(client, "no-existe").status_code == 404
    assert client.get("/conversations/no-existe").status_code == 404


def test_una_conversacion_no_acepta_dos_trabajos_a_la_vez(client):
    primero = crear(client, SOLICITUD_CRITICA)
    pausado = esperar(client, primero)

    assert seguir(client, pausado["conversation_id"]).status_code == 409  # sigue en espera de aprobación

    client.post(f"/tasks/{primero}/approve", json={"aprobado": True})
    esperar(client, primero, {"DONE", "FAILED"})
    assert seguir(client, pausado["conversation_id"]).status_code == 202


# --- Recuperación tras un reinicio ---


def test_lo_que_estaba_en_curso_vuelve_a_la_cola_al_arrancar():
    async def probar():
        redis = fakeredis.aioredis.FakeRedis()
        await redis.rpush(PROCESANDO, '{"job_id": "a"}')
        await redis.rpush(COLA, '{"job_id": "b"}')
        recuperados = await Worker(JobStore(redis), InMemorySaver()).recuperar()
        return recuperados, await redis.lrange(COLA, 0, -1), await redis.llen(PROCESANDO)

    recuperados, cola, procesando = asyncio.run(probar())

    assert recuperados == 1 and procesando == 0
    assert cola == [b'{"job_id": "a", "recuperado": true}', b'{"job_id": "b"}']  # primero lo interrumpido


def test_un_trabajo_interrumpido_termina_al_recuperarse():
    # Redis y checkpointer sobreviven; se simula un worker que tomó el trabajo y murió antes de empezar.
    redis, saver = fakeredis.aioredis.FakeRedis(), InMemorySaver()

    async def preparar():
        store = JobStore(redis)
        job = await store.create(SOLICITUD_DEMO)
        await redis.rpush(PROCESANDO, f'{{"job_id": "{job["job_id"]}"}}')
        return job["job_id"]

    job_id = asyncio.run(preparar())
    app = create_app(redis=redis, checkpointer=saver, retriever=recuperador_de_prueba(), settings=SETTINGS)
    with TestClient(app) as cliente:
        assert esperar(cliente, job_id)["status"] == "DONE"


def test_el_guion_continua_desde_el_avance_de_un_trabajo_recuperado():
    avance = {"steps": 2, "contributions": [{"agent": "research_agent"}, {"agent": "analyst_agent"}]}

    modelos = guiones(avance=avance)

    assert next(modelos["supervisor"].decisiones).instruction.startswith("Refinar")
    assert list(modelos["research_agent"].messages) == []
    assert next(modelos["analyst_agent"].messages).tool_calls[0]["name"] == "calcular"


# --- Registro de proveedores ---


def test_sumar_un_proveedor_es_agregar_una_entrada(monkeypatch):
    from app.llm import Proveedor

    creados = []
    monkeypatch.setitem(PROVEEDORES, "nuevo", Proveedor("NUEVO_KEY", "NUEVO_MODEL", "modelo-x", creados.append))
    monkeypatch.setenv("LLM_PROVIDER", "nuevo")

    build_llm()

    assert creados == ["modelo-x"]


def test_un_proveedor_desconocido_se_rechaza(monkeypatch):
    from app.llm import proveedor_actual

    monkeypatch.setenv("LLM_PROVIDER", "otro")

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        proveedor_actual()
