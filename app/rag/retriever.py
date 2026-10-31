"""Recuperación híbrida: BM25 (términos exactos) + búsqueda vectorial en ChromaDB, fusionadas con RRF."""

import logging
import re
from typing import Any

from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from app.config import Settings

log = logging.getLogger("intelligence.rag")

PESOS = (0.5, 0.5)  # (léxico, semántico): con pesos distintos, RRF deja el top-k casi entero a una sola parte


def tokenizar(texto: str) -> list[str]:
    return re.findall(r"[\w-]+", texto.lower())


class ChromaVectorRetriever(BaseRetriever):
    """Búsqueda por similitud en una colección de ChromaDB con el cliente asíncrono."""

    collection: Any
    embeddings: Embeddings
    k: int = 5

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun
    ) -> list[Document]:
        vector = await self.embeddings.aembed_query(query)
        resultado = await self.collection.query(query_embeddings=[vector], n_results=self.k,
                                                include=["documents", "metadatas", "distances"])
        return [
            Document(page_content=texto, metadata={**metadatos, "similitud": round(1 - distancia, 4)}, id=id_)
            for id_, texto, metadatos, distancia in zip(resultado["ids"][0], resultado["documents"][0],
                                                       resultado["metadatas"][0], resultado["distances"][0])
        ]

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        raise NotImplementedError("El cliente de ChromaDB es asíncrono: usar ainvoke")


def build_hybrid_retriever(fragmentos: list[Document], vectorial: BaseRetriever, k: int = 5) -> EnsembleRetriever:
    bm25 = BM25Retriever.from_documents(fragmentos, preprocess_func=tokenizar, k=k)
    return EnsembleRetriever(retrievers=[bm25, vectorial], weights=list(PESOS))


def build_embeddings(settings: Settings) -> Embeddings:
    if settings.embeddings_provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=settings.embeddings_model)
    from langchain_community.embeddings import FastEmbedEmbeddings

    return FastEmbedEmbeddings(model_name=settings.embeddings_model, cache_dir=settings.model_cache_dir)


_retriever: BaseRetriever | None = None


def set_retriever(retriever: BaseRetriever) -> None:
    global _retriever
    _retriever = retriever


def get_retriever() -> BaseRetriever:
    if _retriever is None:
        raise RuntimeError("El recuperador no está inicializado: se crea al arrancar la API")
    return _retriever


async def setup_rag(settings: Settings) -> BaseRetriever:
    """Conecta con ChromaDB, indexa la documentación si hace falta y devuelve el recuperador híbrido."""
    import chromadb

    from app.rag.ingest import cargar_documentos, fragmentar, ingest

    cliente = await chromadb.AsyncHttpClient(host=settings.chroma_host, port=settings.chroma_port)
    coleccion = await cliente.get_or_create_collection(settings.collection_name, metadata={"hnsw:space": "cosine"})
    embeddings = build_embeddings(settings)
    fragmentos = fragmentar(cargar_documentos())
    await ingest(coleccion, embeddings, fragmentos)
    log.info("RAG listo: colección '%s', embeddings %s", settings.collection_name, settings.embeddings_model)
    vectorial = ChromaVectorRetriever(collection=coleccion, embeddings=embeddings, k=settings.top_k)
    return build_hybrid_retriever(fragmentos, vectorial, k=settings.top_k)
