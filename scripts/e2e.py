"""Prueba de punta a punta contra el sistema levantado con Docker Compose. Reinicia contenedores de verdad.

Uso: python scripts/e2e.py [--url http://localhost:8000]
Comprueba tres escenarios y guarda el resultado en screenshots/e2e.txt:
  1. Aprobación humana que sobrevive a un reinicio de la API.
  2. Conversación que continúa después de reiniciar la API y Redis.
  3. Trabajos en curso que se recuperan tras un corte brusco de la API.
"""

import argparse
import asyncio
import subprocess
import time
from pathlib import Path

import httpx

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "screenshots" / "e2e.txt"
SOLICITUD = (
    "Según la documentación de Atlas, ¿cuántos minutos de caída por mes permite el objetivo de "
    "disponibilidad? Si en lo que va del mes hubo 12 minutos de caída, ¿qué porcentaje del presupuesto "
    "de error se consumió y corresponde suspender los despliegues?"
)
CRITICA = SOLICITUD + " Notificar el resultado al canal #atlas-despliegues."
SEGUIMIENTO = "¿Y si en lugar de 12 hubieran sido 18 minutos?"
lineas: list[str] = []


def informar(texto: str) -> None:
    print(texto, flush=True)
    lineas.append(texto)


def verificar(condicion: bool, descripcion: str) -> None:
    informar(f"  {'OK ' if condicion else 'ERROR'} {descripcion}")
    if not condicion:
        raise SystemExit(f"Falló: {descripcion}")


def compose(*argumentos: str) -> None:
    informar(f"  $ docker compose {' '.join(argumentos)}")
    subprocess.run(["docker", "compose", *argumentos], cwd=RAIZ, check=True, capture_output=True)


async def esperar_api(cliente: httpx.AsyncClient, limite_s: float = 120) -> None:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        try:
            if (await cliente.get("/health")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(1)
    raise SystemExit("La API no volvió a responder")


async def esperar_estado(cliente: httpx.AsyncClient, job_id: str, *estados: str, limite_s: float = 60) -> dict:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        job = (await cliente.get(f"/tasks/{job_id}")).json()
        if job["status"] in estados:
            return job
        await asyncio.sleep(0.2)
    raise SystemExit(f"El trabajo {job_id} no llegó a {estados}; quedó en {job['status']}")


async def crear(cliente: httpx.AsyncClient, solicitud: str, conversation_id: str | None = None) -> dict:
    respuesta = await cliente.post("/tasks", json={"solicitud": solicitud, "conversation_id": conversation_id})
    respuesta.raise_for_status()
    return respuesta.json()


async def aprobacion_con_reinicio(cliente: httpx.AsyncClient) -> None:
    informar("\n1. Aprobación humana que sobrevive a un reinicio de la API")
    job = await crear(cliente, CRITICA)
    pausado = await esperar_estado(cliente, job["job_id"], "WAITING_APPROVAL")
    verificar(pausado["status"] == "WAITING_APPROVAL", f"la tarea crítica se pausó: {pausado['aprobacion_pendiente']['motivo']}")

    compose("restart", "api")
    await esperar_api(cliente)
    sigue = (await cliente.get(f"/tasks/{job['job_id']}")).json()
    verificar(sigue["status"] == "WAITING_APPROVAL", "tras el reinicio sigue esperando aprobación")

    respuesta = await cliente.post(f"/tasks/{job['job_id']}/approve", json={"aprobado": True, "comentario": "Revisado"})
    verificar(respuesta.status_code == 202, "la aprobación fue aceptada")
    final = await esperar_estado(cliente, job["job_id"], "DONE", "FAILED")
    verificar(final["status"] == "DONE", "el grafo continuó desde el punto de pausa y terminó")
    verificar(final["resultado"]["acciones"][0]["canal"] == "#atlas-despliegues", "la acción se ejecutó después de aprobar")


async def conversacion_con_reinicio(cliente: httpx.AsyncClient) -> None:
    informar("\n2. Conversación que continúa después de reiniciar la API y Redis")
    job = await crear(cliente, SOLICITUD)
    primero = await esperar_estado(cliente, job["job_id"], "DONE", "FAILED")
    mensajes_antes = primero["resultado"]["mensajes_en_conversacion"]
    verificar(primero["status"] == "DONE", f"primer turno terminado, {mensajes_antes} mensajes en la conversación")

    compose("restart", "redis", "api")
    await esperar_api(cliente)
    conversacion = await cliente.get(f"/conversations/{job['conversation_id']}")
    verificar(conversacion.status_code == 200, "la conversación sigue en Redis tras el reinicio")

    siguiente = await crear(cliente, SEGUIMIENTO, job["conversation_id"])
    segundo = await esperar_estado(cliente, siguiente["job_id"], "DONE", "FAILED")
    verificar(segundo["status"] == "DONE" and segundo["turno"] == 2, "el segundo turno terminó")
    verificar(segundo["resultado"]["mensajes_en_conversacion"] > mensajes_antes,
              f"el hilo conserva el turno anterior: {segundo['resultado']['mensajes_en_conversacion']} mensajes")
    turnos = (await cliente.get(f"/conversations/{job['conversation_id']}")).json()["turnos"]
    verificar([t["turno"] for t in turnos] == [1, 2], "el historial tiene los dos turnos")


async def corte_brusco(cliente: httpx.AsyncClient) -> None:
    informar("\n3. Trabajos en curso que se recuperan tras un corte brusco de la API")
    trabajos = [await crear(cliente, SOLICITUD) for _ in range(3)]
    compose("kill", "api")
    compose("start", "api")
    await esperar_api(cliente)
    finales = [await esperar_estado(cliente, t["job_id"], "DONE", "FAILED") for t in trabajos]
    verificar(all(f["status"] == "DONE" for f in finales), "los 3 trabajos terminaron tras el corte")


async def main(url: str) -> None:
    async with httpx.AsyncClient(base_url=url, timeout=30) as cliente:
        await esperar_api(cliente)
        await aprobacion_con_reinicio(cliente)
        await conversacion_con_reinicio(cliente)
        await corte_brusco(cliente)
    informar("\nTodos los escenarios pasaron.")
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text("\n".join(lineas) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    asyncio.run(main(parser.parse_args().url))
