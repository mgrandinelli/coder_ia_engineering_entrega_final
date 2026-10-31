"""Agente de Investigación: busca en la documentación técnica con el RAG híbrido. No hace cálculos."""

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field

from app.rag.retriever import get_retriever

PROMPT = (
    "Sos el agente de investigación. Tu única tarea es encontrar en la documentación técnica de la "
    "plataforma Atlas los datos que pide la instrucción, usando la herramienta buscar_documentacion.\n"
    "Respondé con los datos encontrados, textuales y con su fuente entre corchetes. No hagas cálculos "
    "ni conclusiones. Si la documentación no tiene el dato, decilo explícitamente."
)


class BusquedaInput(BaseModel):
    consulta: str = Field(min_length=3, max_length=300,
                          description="Consulta con los términos técnicos que usaría la documentación.")


class Fragmento(BaseModel):
    fuente: str
    seccion: str
    texto: str


class ResultadoBusqueda(BaseModel):
    consulta: str
    fragmentos: list[Fragmento]


@tool(args_schema=BusquedaInput)
async def buscar_documentacion(consulta: str) -> str:
    """Busca en la documentación técnica de la plataforma Atlas (arquitectura, API, despliegues,
    incidentes, observabilidad y seguridad) y devuelve los fragmentos más relevantes con su fuente.

    Usar para obtener datos documentados: objetivos de nivel de servicio, límites, plazos, códigos
    de error, procedimientos o reglas. Combina búsqueda por términos exactos (útil para códigos como
    ATL-4012) y búsqueda por significado.
    """
    documentos = await get_retriever().ainvoke(consulta)
    resultado = ResultadoBusqueda(consulta=consulta, fragmentos=[
        Fragmento(fuente=d.metadata["fuente"], seccion=d.metadata.get("seccion", ""), texto=d.page_content)
        for d in documentos])
    if not resultado.fragmentos:
        return "Sin resultados para esa consulta. Probar con otros términos."
    return "\n\n".join(f"[{f.fuente} · {f.seccion}]\n{f.texto}" for f in resultado.fragmentos)


TOOLS = [buscar_documentacion]


def build_research_agent(model: BaseChatModel):
    return create_react_agent(model, TOOLS, prompt=PROMPT, name="research_agent")
