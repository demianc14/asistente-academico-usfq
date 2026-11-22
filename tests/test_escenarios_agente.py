"""Garantías del bucle sobre los 17 escenarios congelados de evals/escenarios_agente.yaml.

Aquí no se comprueba el resultado esperado de cada escenario: eso lo mide la corrida
guardada (`python -m asistente.evaluacion.agente`). Estos tests solo afirman lo que tiene que
cumplirse pase lo que pase: nada que escriba el LLM llega al usuario y todo plan mostrado
pasa `verificar_plan` con el periodo, los créditos y los aprobados de la solicitud.
"""

from __future__ import annotations

import copy
import sqlite3
from typing import Any

import pytest

from asistente.agente import ResultadoAgente, planificar
from asistente.evaluacion.agente import (
    Escenario,
    cargar_escenarios,
    cliente_de_guion,
    reverificar,
)
from asistente.planificador import verificar_plan

CANARIO = "CANARIO-7f3a9c-no-debe-verse"
ESCENARIOS = cargar_escenarios()
Guion = tuple[dict[str, Any] | str, ...]

# Si el canario desvía el guion y se acaban las respuestas, el cliente falso se abstiene.
RELLENO: Guion = ({"accion": "abstenerse", "motivo": "sin_plan_posible"},) * 12


def visible(resultado: ResultadoAgente) -> str:
    """Todo lo que se le enseña al usuario."""
    plan = "" if resultado.plan is None else resultado.plan.model_dump_json()
    return resultado.texto + plan + " ".join(resultado.preferencias_no_verificadas)


def _con_canario_en_los_campos(respuesta: dict[str, Any] | str) -> dict[str, Any] | str:
    """El canario en cada sitio de la respuesta donde cabe una cadena."""
    if isinstance(respuesta, str):
        return f"{respuesta} {CANARIO}"
    accion = copy.deepcopy(respuesta)
    if accion["accion"] == "preguntar":
        accion["pregunta"] = CANARIO
    elif accion["accion"] == "abstenerse":
        accion["motivo"] = CANARIO
    elif accion["accion"] == "proponer" or accion.get("nombre") == "verificar_plan":
        plan = accion["plan"] if accion["accion"] == "proponer" else accion["argumentos"]
        plan["semestres"][0]["cursos"][0]["nrc"] = CANARIO
    else:
        accion["argumentos"] = {clave: CANARIO for clave in accion["argumentos"]}
    return accion


def _variantes(guion: Guion) -> list[Guion]:
    """El guion con el canario metido de todas las formas que se nos ocurren."""
    variantes: list[Guion] = []
    for i in range(len(guion) + 1):
        # una salida suelta con el canario antes de cada paso
        variantes.append((*guion[:i], CANARIO, *guion[i:]))
        # una acción con un campo de más, antes de cada paso
        intrusa = {"accion": "preguntar", "pregunta": "falta_periodo", "texto": CANARIO}
        variantes.append((*guion[:i], intrusa, *guion[i:]))
    for i, respuesta in enumerate(guion):
        variantes.append((*guion[:i], _con_canario_en_los_campos(respuesta), *guion[i + 1 :]))
        if isinstance(respuesta, dict):  # un campo de más en la propia acción
            variantes.append((*guion[:i], {**respuesta, "nota": CANARIO}, *guion[i + 1 :]))
    variantes.append(tuple(_con_canario_en_los_campos(r) for r in guion))
    return variantes


def test_son_los_17_congelados() -> None:
    assert [e.id for e in ESCENARIOS] == [f"ag-{n:02d}" for n in range(1, 18)]


@pytest.mark.parametrize("escenario", ESCENARIOS, ids=lambda e: e.id)
def test_canario_nunca_llega_a_la_salida_visible(
    con: sqlite3.Connection, escenario: Escenario
) -> None:
    variantes = _variantes(escenario.guion)
    assert variantes
    for guion in variantes:
        resultado = planificar(con, cliente_de_guion((*guion, *RELLENO)), escenario.solicitud)
        assert CANARIO not in visible(resultado)


@pytest.mark.parametrize("escenario", ESCENARIOS, ids=lambda e: e.id)
def test_plan_mostrado_pasa_verificar_plan_con_los_datos_de_la_solicitud(
    con: sqlite3.Connection, escenario: Escenario
) -> None:
    for guion in (escenario.guion, *_variantes(escenario.guion)):
        resultado = planificar(con, cliente_de_guion((*guion, *RELLENO)), escenario.solicitud)
        if resultado.estado != "plan":
            assert resultado.plan is None
            continue
        assert resultado.plan is not None
        assert verificar_plan(con, resultado.plan).valido
        assert reverificar(con, escenario.solicitud, resultado.plan)
        assert resultado.plan.creditos_max == escenario.solicitud.creditos_max
        assert resultado.plan.aprobados == escenario.solicitud.aprobados
        assert resultado.plan.periodo == escenario.solicitud.periodo
        assert [s.periodo for s in resultado.plan.semestres] == [escenario.solicitud.periodo]
        assert "Solo se comprobaron prerrequisitos, créditos y choques" in resultado.texto
        for preferencia in escenario.solicitud.preferencias:
            assert f"- {preferencia}" in resultado.texto.partition("no verificadas:")[2]
