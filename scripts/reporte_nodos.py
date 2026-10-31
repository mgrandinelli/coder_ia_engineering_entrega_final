"""Lee las trazas de Phoenix y resume, por nodo del grafo, la latencia y los tokens.

Uso: python scripts/reporte_nodos.py [--phoenix http://localhost:6006]
Guarda el resultado en screenshots/latencia_por_nodo.json.
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx

SALIDA = Path(__file__).resolve().parent.parent / "screenshots" / "latencia_por_nodo.json"
PROYECTO = "atlas-intelligence"
NODOS = ("supervisor", "research_agent", "analyst_agent", "aprobacion_humana", "synthesizer")
PRECIO_ENTRADA, PRECIO_SALIDA = 0.15 / 1_000_000, 0.60 / 1_000_000  # gpt-4o-mini, USD por token


def percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    posicion = (len(ordenados) - 1) * p
    inferior = int(posicion)
    superior = min(inferior + 1, len(ordenados) - 1)
    return ordenados[inferior] + (ordenados[superior] - ordenados[inferior]) * (posicion - inferior)


def duracion(span: dict) -> float:
    return (datetime.fromisoformat(span["end_time"]) - datetime.fromisoformat(span["start_time"])).total_seconds()


def main(phoenix: str) -> dict:
    spans = httpx.get(f"{phoenix}/v1/projects/{PROYECTO}/spans", params={"limit": 1000}, timeout=30).json()["data"]
    latencias, entrada, salida = defaultdict(list), defaultdict(int), defaultdict(int)
    nombres = {span["context"]["span_id"]: span["name"] for span in spans}
    for span in spans:
        # Solo el span del nodo en el grafo principal: los agentes tienen además su propio subgrafo con el mismo nombre.
        if span["name"] in NODOS and nombres.get(span["parent_id"]) == "LangGraph":
            latencias[span["name"]].append(duracion(span))
        if span["span_kind"] == "LLM":
            nodo = span["attributes"].get("metadata.langgraph_checkpoint_ns", "").split(":")[0] \
                or span["attributes"].get("metadata.langgraph_node", "?")
            entrada[nodo] += span["attributes"].get("llm.token_count.prompt", 0)
            salida[nodo] += span["attributes"].get("llm.token_count.completion", 0)

    total = sum(entrada.values()) + sum(salida.values())
    filas = {}
    print(f"{'Nodo':<20}{'veces':>6}{'p50 (s)':>9}{'p95 (s)':>9}{'tokens':>8}{'% tokens':>10}{'costo USD':>11}")
    for nodo in NODOS:
        valores = latencias.get(nodo, [0.0])
        tokens = entrada[nodo] + salida[nodo]
        filas[nodo] = {
            "veces": len(latencias.get(nodo, [])), "p50_s": round(percentil(valores, 0.5), 3),
            "p95_s": round(percentil(valores, 0.95), 3), "tokens": tokens,
            "porcentaje_tokens": round(100 * tokens / total, 1) if total else 0,
            "costo_usd": round(entrada[nodo] * PRECIO_ENTRADA + salida[nodo] * PRECIO_SALIDA, 6),
        }
        f = filas[nodo]
        print(f"{nodo:<20}{f['veces']:>6}{f['p50_s']:>9.3f}{f['p95_s']:>9.3f}{tokens:>8}"
              f"{f['porcentaje_tokens']:>9.1f}%{f['costo_usd']:>11.6f}")
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps(filas, ensure_ascii=False, indent=2), encoding="utf-8")
    return filas


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--phoenix", default="http://localhost:6006")
    main(parser.parse_args().phoenix)
