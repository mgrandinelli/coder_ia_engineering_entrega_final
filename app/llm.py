"""Modelos de lenguaje del orquestador, y modelos guionados para el modo demostración (sin API key)."""

import json
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import RunnableLambda

load_dotenv()

log = logging.getLogger("intelligence")

TEMPERATURE = 0


def _openai(modelo: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=modelo, temperature=TEMPERATURE)


def _anthropic(modelo: str) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(model=modelo, temperature=TEMPERATURE, max_tokens=1024)


@dataclass(frozen=True)
class Proveedor:
    api_key_env: str
    model_env: str
    modelo_por_defecto: str
    crear: Callable[[str], BaseChatModel]  # importa la integración solo si se usa


# Registro de proveedores: sumar un modelo nuevo es agregar una entrada acá.
PROVEEDORES: dict[str, Proveedor] = {
    "openai": Proveedor("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini", _openai),
    "anthropic": Proveedor("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "claude-haiku-4-5", _anthropic),
}


def proveedor_actual() -> Proveedor:
    nombre = os.getenv("LLM_PROVIDER") or "openai"
    if nombre not in PROVEEDORES:
        raise ValueError(f"LLM_PROVIDER debe ser uno de {sorted(PROVEEDORES)}, no '{nombre}'")
    return PROVEEDORES[nombre]


def modo_demo() -> bool:
    """True si no hay clave para el proveedor elegido: se usan los modelos guionados."""
    return not os.getenv(proveedor_actual().api_key_env)


def build_llm() -> BaseChatModel:
    proveedor = proveedor_actual()
    return proveedor.crear(os.getenv(proveedor.model_env) or proveedor.modelo_por_defecto)


# --- Modo demostración ---


MODELO_ESTIMADO = "gpt-4o-mini"  # modelo cuyo tokenizador y precio se usan para estimar el costo en modo demostración


@lru_cache(maxsize=1)
def _tokenizador():
    import tiktoken

    return tiktoken.encoding_for_model(MODELO_ESTIMADO)


def contar_tokens(texto: str) -> int:
    try:
        return len(_tokenizador().encode(texto))
    except Exception:  # sin el tokenizador (por ejemplo, sin red para descargarlo) se aproxima
        return max(1, len(texto) // 4)


class ModeloGuionado(GenericFakeChatModel):
    """Devuelve, en orden, los mensajes y las decisiones de un guion.

    No forma parte del orquestador: reemplaza al LLM real cuando no hay credenciales, para ejecutar
    el grafo, los agentes y las herramientas de punta a punta. `bind_tools` no cambia nada porque el
    guion ya trae las llamadas a herramientas; `with_structured_output` devuelve la próxima decisión.

    Cada respuesta informa como uso los tokens del texto que recibe y del que devuelve, contados con
    el tokenizador de gpt-4o-mini, para que la plataforma de observabilidad pueda estimar el costo.
    """

    decisiones: Iterator = iter(())
    model_name: str = MODELO_ESTIMADO

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        # La decisión pasa por una llamada al modelo (como con un LLM real) para que quede su span con tokens.
        respuestas = (AIMessage(content=d.model_dump_json()) for d in self.decisiones)
        modelo = ModeloGuionado(messages=respuestas)
        return modelo | RunnableLambda(lambda mensaje: schema.model_validate_json(mensaje.content))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        resultado = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        mensaje = resultado.generations[0].message
        entrada = sum(contar_tokens(str(m.content)) + 4 for m in messages)
        salida = contar_tokens(str(mensaje.content) + json.dumps([t["args"] for t in mensaje.tool_calls]))
        uso = {"input_tokens": entrada, "output_tokens": salida, "total_tokens": entrada + salida}
        mensaje.usage_metadata = uso
        mensaje.response_metadata = {**mensaje.response_metadata, "model_name": self.model_name}
        resultado.llm_output = {"model_name": self.model_name,
                                "token_usage": {"prompt_tokens": entrada, "completion_tokens": salida,
                                                "total_tokens": entrada + salida}}
        return resultado


def _desde(saltar: int, guion: list) -> Iterator:
    return iter(guion[saltar:])


def _llamada(herramienta: str, numero: int, **argumentos) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": herramienta, "args": argumentos, "id": f"demo_{numero}"}])


SOLICITUD_DEMO = (
    "Según la documentación de Atlas, ¿cuántos minutos de caída por mes permite el objetivo de "
    "disponibilidad? Si en lo que va del mes hubo 12 minutos de caída, ¿qué porcentaje del presupuesto "
    "de error se consumió y corresponde suspender los despliegues?"
)
SOLICITUD_SEGUIMIENTO = "¿Y si en lugar de 12 hubieran sido 18 minutos?"
# La misma consulta, pero además pide una acción con efectos secundarios: es una tarea crítica.
SOLICITUD_CRITICA = SOLICITUD_DEMO + " Notificar el resultado al canal #atlas-despliegues."


def guiones(seguimiento: bool = False, avance: dict | None = None) -> dict[str, ModeloGuionado]:
    """Un modelo guionado por rol: para SOLICITUD_DEMO o, en un segundo turno, para SOLICITUD_SEGUIMIENTO.

    `avance` es el estado de un trabajo recuperado desde su checkpoint: el guion continúa desde ahí
    en lugar de empezar de cero.
    """
    from app.graph import RouteDecision  # import diferido: graph importa este módulo

    avance = avance or {}
    aportes = [c["agent"] for c in avance.get("contributions", [])]
    # Cada aporte de un especialista consume dos mensajes del guion: la llamada a la herramienta y la respuesta.
    saltar_investigador, saltar_analista = 2 * aportes.count("research_agent"), 2 * aportes.count("analyst_agent")
    saltar_supervisor = avance.get("steps", 0)

    if seguimiento:
        return {
            "supervisor": ModeloGuionado(messages=iter(()), decisiones=_desde(saltar_supervisor, [
                RouteDecision(next_agent="analyst_agent", reasoning="Los datos del objetivo ya están en la conversación.",
                              instruction="Con un objetivo de 99,95 % medido en 30 días y 18 minutos de caída, calcular "
                                          "el porcentaje consumido y compararlo con el umbral del 75 %."),
                RouteDecision(next_agent="synthesizer", reasoning="La rúbrica se cumple.",
                              instruction="Redactar la respuesta final."),
            ])),
            "research_agent": ModeloGuionado(messages=iter(())),
            "analyst_agent": ModeloGuionado(messages=_desde(saltar_analista, [
                _llamada("presupuesto_de_error", 1, slo_porcentaje=99.95, ventana_dias=30, minutos_de_caida=18),
                AIMessage(content="Con 18 minutos se consumió el 83,33 % del presupuesto, por encima del umbral del 75 %."),
            ])),
            "synthesizer": ModeloGuionado(messages=iter([
                AIMessage(content="Con 18 minutos de caída se habría consumido el 83,33 % del presupuesto de error. Supera "
                                  "el 75 %, así que corresponde suspender los despliegues de funciones nuevas."),
            ])),
        }

    supervisor = ModeloGuionado(messages=iter(()), decisiones=_desde(saltar_supervisor, [
        RouteDecision(next_agent="research_agent", reasoning="Faltan los datos documentados del objetivo.",
                      instruction="Buscar el objetivo de disponibilidad de Atlas (porcentaje y ventana de medición) "
                                  "y la regla que indica qué hacer según el consumo del presupuesto de error."),
        RouteDecision(next_agent="analyst_agent", reasoning="Ya están los datos; falta calcular.",
                      instruction="Con un objetivo de 99,95 % medido en 30 días y 12 minutos de caída, calcular los "
                                  "minutos permitidos, el porcentaje consumido y los minutos restantes."),
        RouteDecision(next_agent="analyst_agent", reasoning="Rúbrica, criterio 3: falta la conclusión pedida.",
                      instruction="Refinar: comparar el consumo con el umbral del 75 % que fija la documentación, "
                                  "indicar si corresponde suspender los despliegues y cuántos minutos faltan para el umbral."),
        RouteDecision(next_agent="synthesizer", reasoning="La rúbrica se cumple: datos con fuente, cálculos y conclusión.",
                      instruction="Redactar la respuesta final."),
    ]))
    investigador = ModeloGuionado(messages=_desde(saltar_investigador, [
        _llamada("buscar_documentacion", 1, consulta="objetivo de disponibilidad presupuesto de error"),
        AIMessage(content="Atlas tiene un objetivo de disponibilidad del 99,95 % de las solicitudes de cobro sin error "
                          "del servidor, medido en ventanas de 30 días. Si en una ventana se consume más del 75 % del "
                          "presupuesto de error, se suspenden los despliegues de funciones nuevas "
                          "[observabilidad.md · Objetivos de nivel de servicio]."),
    ]))
    analista = ModeloGuionado(messages=_desde(saltar_analista, [
        _llamada("presupuesto_de_error", 2, slo_porcentaje=99.95, ventana_dias=30, minutos_de_caida=12),
        AIMessage(content="El objetivo de 99,95 % en 30 días permite 21,6 minutos de caída. Con 12 minutos se consumió "
                          "el 55,56 % del presupuesto y quedan 9,6 minutos."),
        _llamada("calcular", 3, expresion="0.75 * 21.6 - 12"),
        AIMessage(content="El consumo (55,56 %) está por debajo del umbral del 75 %, así que no corresponde suspender "
                          "los despliegues. El umbral se alcanza a los 16,2 minutos: quedan 4,2 minutos de caída antes "
                          "de llegar a él."),
    ]))
    sintetizador = ModeloGuionado(messages=iter([
        AIMessage(content="El objetivo de disponibilidad de Atlas (99,95 % en ventanas de 30 días) permite 21,6 minutos "
                          "de caída por mes. Con 12 minutos de caída se consumió el 55,56 % del presupuesto de error, "
                          "por debajo del 75 % a partir del cual se suspenden los despliegues de funciones nuevas: por "
                          "ahora no corresponde suspenderlos, pero 4,2 minutos más de caída alcanzarían el umbral. "
                          "Fuente: observabilidad.md, sección Objetivos de nivel de servicio."),
    ]))
    return {"supervisor": supervisor, "research_agent": investigador, "analyst_agent": analista, "synthesizer": sintetizador}
