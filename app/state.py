"""Estado compartido del orquestador: qué se pidió, quién aportó qué y cuándo terminar."""

import operator
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph import MessagesState

AgentName = Literal["research_agent", "analyst_agent", "synthesizer"]


class Contribution(TypedDict):
    """Un aporte de un especialista: quién lo hizo, qué se le pidió y qué devolvió."""

    agent: AgentName
    task: str
    output: str
    step: int


class State(MessagesState):
    """Hereda `messages` de MessagesState: es lo que perdura entre turnos de una conversación.

    Cada campo extra tiene un único dueño que lo escribe, y describe solo el turno en curso:
    el nodo `inicio_turno` los reinicia.
    """

    next_agent: AgentName | None  # lo escribe el supervisor
    instruction: str  # instrucción puntual del supervisor para el próximo especialista
    task_completed: bool  # lo escribe el sintetizador al cerrar
    # Registro de aportes: se agregan, nunca se pisan (reductor operator.add).
    contributions: Annotated[list[Contribution], operator.add]
    steps: Annotated[int, operator.add]  # decisiones tomadas por el supervisor
    # Human-in-the-loop: los escribe solo el nodo de aprobación humana.
    critical_reason: str | None  # por qué la tarea se consideró crítica (None si no lo es)
    approval: dict | None  # decisión del operador: {"aprobado": bool, "comentario": str}
    actions: Annotated[list[dict], operator.add]  # acciones con efectos secundarios ya ejecutadas


def ultima_solicitud(mensajes: list[AnyMessage]) -> str:
    """La solicitud del turno en curso: el último mensaje del usuario."""
    return next(m.content for m in reversed(mensajes) if isinstance(m, HumanMessage))


def historial_previo(mensajes: list[AnyMessage]) -> str:
    """Turnos anteriores de la conversación: preguntas del usuario y respuestas finales."""
    humanos = [i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)]
    previos = mensajes[:humanos[-1]] if humanos else []
    lineas = [f"Usuario: {m.content}" if isinstance(m, HumanMessage) else f"Asistente: {m.content}"
              for m in previos if isinstance(m, HumanMessage) or m.name == "synthesizer"]
    return "\n".join(lineas)
