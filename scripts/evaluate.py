"""Evaluación de la recuperación contra ChromaDB real: Recall@k, Precision@k y MRR sobre golden_set.json.

Uso (con ChromaDB levantado): python scripts/evaluate.py
Compara el recuperador híbrido con cada una de sus partes y guarda el resultado en screenshots/evaluacion.json.
"""

import asyncio
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from langchain_core.documents import Document  # noqa: E402
from pydantic import BaseModel, Field, TypeAdapter  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.rag.retriever import setup_rag  # noqa: E402

GOLDEN_SET = RAIZ / "golden_set.json"
SALIDA = RAIZ / "screenshots" / "evaluacion.json"


class GoldenItem(BaseModel):
    pregunta: str = Field(min_length=1)
    documento_id_esperado: str = Field(min_length=1)


def cargar_golden_set(ruta: Path = GOLDEN_SET) -> list[GoldenItem]:
    return TypeAdapter(list[GoldenItem]).validate_python(json.loads(ruta.read_text(encoding="utf-8")))


def recall_at_k(documentos: list[Document], esperado: str, k: int) -> float:
    return float(any(d.metadata["documento_id"] == esperado for d in documentos[:k]))


def precision_at_k(documentos: list[Document], esperado: str, k: int) -> float:
    return sum(d.metadata["documento_id"] == esperado for d in documentos[:k]) / k


def reciprocal_rank(documentos: list[Document], esperado: str, k: int) -> float:
    for posicion, documento in enumerate(documentos[:k], start=1):
        if documento.metadata["documento_id"] == esperado:
            return 1 / posicion
    return 0.0


async def evaluar(recuperador, golden: list[GoldenItem], k: int) -> dict:
    fallos, recalls, precisiones, rangos = [], [], [], []
    for item in golden:
        documentos = (await recuperador.ainvoke(item.pregunta))[:k]
        recall = recall_at_k(documentos, item.documento_id_esperado, k)
        recalls.append(recall)
        precisiones.append(precision_at_k(documentos, item.documento_id_esperado, k))
        rangos.append(reciprocal_rank(documentos, item.documento_id_esperado, k))
        if not recall:
            fallos.append(item.pregunta)
    n = len(golden)
    return {"recall": round(sum(recalls) / n, 3), "precision": round(sum(precisiones) / n, 3),
            "mrr": round(sum(rangos) / n, 3), "fallos": fallos}


async def main() -> dict:
    settings = get_settings()
    golden = cargar_golden_set()
    hibrido = await setup_rag(settings)
    bm25, vectorial = hibrido.retrievers
    k = settings.top_k
    resultados = {
        "Híbrido (BM25 + ChromaDB)": await evaluar(hibrido, golden, k),
        "Solo léxico (BM25)": await evaluar(bm25, golden, k),
        "Solo semántico (ChromaDB)": await evaluar(vectorial, golden, k),
    }
    print(f"\n{len(golden)} preguntas | k={k} | embeddings {settings.embeddings_model}\n")
    print(f"{'Recuperador':<30}{f'Recall@{k}':>10}{f'Precision@{k}':>14}{'MRR':>8}")
    for nombre, r in resultados.items():
        print(f"{nombre:<30}{r['recall']:>10.3f}{r['precision']:>14.3f}{r['mrr']:>8.3f}")
    for nombre, r in resultados.items():
        for pregunta in r["fallos"]:
            print(f"Sin acierto en {nombre}: {pregunta}")
    SALIDA.parent.mkdir(exist_ok=True)
    SALIDA.write_text(json.dumps({"k": k, "preguntas": len(golden), "modelo": settings.embeddings_model,
                                  "resultados": resultados}, ensure_ascii=False, indent=2), encoding="utf-8")
    return resultados


if __name__ == "__main__":
    asyncio.run(main())
