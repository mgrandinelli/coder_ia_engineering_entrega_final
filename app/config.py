"""Configuración del sistema, leída del entorno y validada con Pydantic. Nada de esto va escrito en el código."""

import os
from functools import lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

EMBEDDINGS = {  # modelo y dimensión de cada proveedor de embeddings
    "openai": ("text-embedding-3-small", 1536),
    "local": ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", 384),
}


class Settings(BaseModel):
    redis_url: str = "redis://localhost:6380"
    chroma_host: str = "localhost"
    chroma_port: int = Field(default=8001, gt=0, lt=65536)
    chroma_collection: str = "atlas_docs"
    embeddings_provider: Literal["openai", "local"] = "local"
    model_cache_dir: str | None = None
    top_k: int = Field(default=5, ge=1, le=10)
    worker_concurrency: int = Field(default=5, ge=1, le=50)
    phoenix_enabled: bool = True
    phoenix_endpoint: str = "http://localhost:6006/v1/traces"
    phoenix_project: str = "atlas-intelligence"

    @property
    def embeddings_model(self) -> str:
        return EMBEDDINGS[self.embeddings_provider][0]

    @property
    def collection_name(self) -> str:
        # Una colección por modelo de embeddings: nunca se consulta con un modelo lo indexado con otro.
        sufijo = "openai" if self.embeddings_provider == "openai" else "local"
        return f"{self.chroma_collection}_{sufijo}"


def _env(nombre: str) -> str | None:
    valor = os.getenv(nombre)
    return valor.strip() if valor and valor.strip() else None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    proveedor = _env("EMBEDDINGS_PROVIDER") or ("openai" if _env("OPENAI_API_KEY") else "local")
    valores = {
        "redis_url": _env("REDIS_URL"),
        "chroma_host": _env("CHROMA_HOST"),
        "chroma_port": _env("CHROMA_PORT"),
        "chroma_collection": _env("CHROMA_COLLECTION"),
        "embeddings_provider": proveedor,
        "model_cache_dir": _env("MODEL_CACHE_DIR"),
        "top_k": _env("TOP_K"),
        "worker_concurrency": _env("WORKER_CONCURRENCY"),
        "phoenix_enabled": _env("PHOENIX_ENABLED"),
        "phoenix_endpoint": _env("PHOENIX_COLLECTOR_ENDPOINT"),
        "phoenix_project": _env("PHOENIX_PROJECT_NAME"),
    }
    return Settings(**{k: v for k, v in valores.items() if v is not None})
