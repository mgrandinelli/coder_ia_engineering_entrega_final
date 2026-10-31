"""Ingesta: documentos de data/ -> fragmentos con metadatos validados -> colección de ChromaDB."""

import hashlib
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger("intelligence.rag")

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
CHUNK_SIZE = 500  # tokens
CHUNK_OVERLAP = 50  # tokens
BATCH_SIZE = 100


class ChunkMetadata(BaseModel):
    """Interfaz única de metadatos: todos los fragmentos tienen los mismos campos."""

    model_config = ConfigDict(extra="forbid")

    documento_id: str = Field(min_length=1)
    fuente: str = Field(min_length=1)
    titulo: str
    seccion: str
    pagina: int = Field(ge=1)  # posición del fragmento dentro del documento
    categoria: str = Field(min_length=1)
    etiquetas: str  # separadas por coma: ChromaDB guarda valores simples en los metadatos


def limpiar(texto: str) -> str:
    texto = re.sub(r"[ \t]+\n", "\n", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _separar_cabecera(texto: str) -> tuple[dict[str, str], str]:
    coincidencia = re.match(r"---\n(.*?)\n---\n(.*)", texto, flags=re.DOTALL)
    if not coincidencia:
        return {}, texto
    campos = dict(linea.split(":", 1) for linea in coincidencia.group(1).splitlines() if ":" in linea)
    return {k.strip(): v.strip() for k, v in campos.items()}, coincidencia.group(2)


def cargar_documentos(data_dir: Path = DATA_DIR) -> list[Document]:
    documentos = []
    for ruta in sorted(Path(data_dir).glob("*.md")):
        cabecera, cuerpo = _separar_cabecera(ruta.read_text(encoding="utf-8"))
        documentos.append(Document(page_content=limpiar(cuerpo), metadata={
            "documento_id": ruta.stem, "fuente": ruta.name,
            "titulo": cabecera.get("titulo", ruta.stem), "categoria": cabecera.get("categoria", "general"),
            "etiquetas": cabecera.get("etiquetas", ""),
        }))
    return documentos


def build_splitter() -> RecursiveCharacterTextSplitter:
    try:
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="cl100k_base", chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    except Exception as exc:  # sin el tokenizador (por ejemplo, sin red) se aproxima a 4 caracteres por token
        log.warning("tiktoken no disponible (%s); se fragmenta por caracteres", type(exc).__name__)
        return RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE * 4, chunk_overlap=CHUNK_OVERLAP * 4)


def _seccion(texto: str) -> str:
    titulos = re.findall(r"^## (.+)$", texto, flags=re.MULTILINE)
    return titulos[0].strip() if titulos else "Introducción"


def fragmentar(documentos: list[Document]) -> list[Document]:
    fragmentos = build_splitter().split_documents(documentos)
    paginas: dict[str, int] = {}
    for fragmento in fragmentos:
        documento_id = fragmento.metadata["documento_id"]
        pagina = paginas[documento_id] = paginas.get(documento_id, 0) + 1
        fragmento.metadata = ChunkMetadata(**fragmento.metadata, pagina=pagina,
                                           seccion=_seccion(fragmento.page_content)).model_dump()
        huella = hashlib.sha1(fragmento.page_content.encode("utf-8")).hexdigest()[:10]
        fragmento.id = f"{documento_id}#{pagina}#{huella}"  # id determinista: reingerir no duplica
    return fragmentos


def en_lotes(elementos: list, tamano: int = BATCH_SIZE) -> Iterator[list]:
    for inicio in range(0, len(elementos), tamano):
        yield elementos[inicio:inicio + tamano]


async def ingest(collection: Any, embeddings: Embeddings, fragmentos: list[Document]) -> int:
    """Sincroniza la colección con los documentos: indexa lo nuevo y borra lo que ya no existe.

    Los ids incluyen un hash del contenido, así que un fragmento modificado cuenta como nuevo.
    Si no cambió nada, no se generan embeddings.
    """
    existentes = set((await collection.get(include=[]))["ids"])
    actuales = {f.id for f in fragmentos}
    nuevos = [f for f in fragmentos if f.id not in existentes]
    obsoletos = sorted(existentes - actuales)

    for numero, lote in enumerate(en_lotes(nuevos), start=1):
        vectores = await embeddings.aembed_documents([f.page_content for f in lote])
        await collection.upsert(ids=[f.id for f in lote], embeddings=vectores,
                                documents=[f.page_content for f in lote], metadatas=[f.metadata for f in lote])
        log.info("Lote %d: %d fragmentos indexados", numero, len(lote))
    if obsoletos:
        await collection.delete(ids=obsoletos)
    log.info("Ingesta: %d fragmentos nuevos, %d eliminados, %d en total", len(nuevos), len(obsoletos), len(actuales))
    return len(actuales)
