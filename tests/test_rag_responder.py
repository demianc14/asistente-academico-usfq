from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest

from asistente.db import conectar
from asistente.llm import ClienteDeGuion
from asistente.rag.busqueda import buscar, consulta_fts
from asistente.rag.ingesta import Fragmento, guardar
from asistente.rag.responder import MENSAJE_ABSTENCION, responder
from asistente.rag.verificar import Borrador, verificar

FRAGMENTOS = [
    Fragmento(
        "PROMEDIO MÍNIMO",
        50,
        "PROMEDIO MÍNIMO\nTodo estudiante debe mantener un promedio acumulado GPA de al menos "
        "2,00/4,00. Si es menor, entrará en condición académica.",
    ),
    Fragmento(
        "RETIROS",
        54,
        "RETIROS\nSolo es posible retirarse hasta por dos ocasiones de una misma materia.",
    ),
    Fragmento(
        "Periodos de pago",
        1,
        "Periodos de pago\nPago de matrícula ordinaria miércoles, 4 de noviembre de 2099 "
        "miércoles, 18 de noviembre de 2099",
        contexto="Calendario de prueba, segundo semestre",
    ),
]

BUENA = {
    "abstenerse": False,
    "respuesta": "Debes mantener un promedio acumulado de al menos 2,00 sobre 4,00.",
    "citas": [{"fragmento_id": 1, "texto": "promedio acumulado GPA de al menos 2,00/4,00"}],
}
INVENTADA = {
    "abstenerse": False,
    "respuesta": "El promedio mínimo es 2,50.",
    "citas": [{"fragmento_id": 1, "texto": "promedio acumulado GPA de al menos 2,50/4,00"}],
}
PREGUNTA = "¿Cuál es el promedio acumulado mínimo?"


@pytest.fixture
def docs() -> Iterator[sqlite3.Connection]:
    con = conectar(":memory:")
    guardar(con, "Documento de prueba", None, "sha", "2099-01-01", FRAGMENTOS)
    yield con
    con.close()


def _guion(*respuestas: dict[str, Any] | str) -> ClienteDeGuion:
    return ClienteDeGuion(r if isinstance(r, str) else json.dumps(r) for r in respuestas)


# ------------------------------------------------------------------ búsqueda


def test_consulta_fts_descarta_palabras_vacias_y_plurales() -> None:
    assert consulta_fts("¿Cuántas matrículas se admiten en 2026?") == (
        '"matrícula"* OR "admiten"* OR "2026"'
    )
    assert consulta_fts("retirarme") == '"retirar"*'  # encuentra "retirarse" y "retiraron"
    assert consulta_fts("¿y qué es?") == ""


def test_buscar_ordena_por_relevancia_e_ignora_tildes(docs: sqlite3.Connection) -> None:
    resultados = buscar(docs, "¿cuantas veces puedo retirarme de una materia?")
    assert resultados[0].seccion == "RETIROS"
    assert resultados[0].documento == "Documento de prueba"


def test_buscar_usa_el_contexto_del_documento(docs: sqlite3.Connection) -> None:
    # "calendario" solo está en el contexto, no en el texto del fragmento.
    assert [r.seccion for r in buscar(docs, "calendario")] == ["Periodos de pago"]


def test_buscar_sin_coincidencias(docs: sqlite3.Connection) -> None:
    assert buscar(docs, "parqueadero") == []
    assert buscar(docs, "¿y?") == []


# ------------------------------------------------------------------ verificación


def _verificar(docs: sqlite3.Connection, borrador: dict[str, Any]) -> list[str]:
    return verificar(Borrador.model_validate(borrador), buscar(docs, PREGUNTA))


def test_una_cita_literal_pasa(docs: sqlite3.Connection) -> None:
    assert _verificar(docs, BUENA) == []


def test_la_cita_tolera_espacios_y_mayusculas_pero_no_palabras(docs: sqlite3.Connection) -> None:
    con_saltos = {
        **BUENA,
        "citas": [{"fragmento_id": 1, "texto": "Promedio  acumulado\nGPA de al menos 2,00/4,00"}],
    }
    assert _verificar(docs, con_saltos) == []
    parafraseada = {
        **BUENA,
        "citas": [{"fragmento_id": 1, "texto": "promedio GPA de al menos 2,00/4,00"}],
    }
    assert "no aparece literalmente" in _verificar(docs, parafraseada)[0]


@pytest.mark.parametrize(
    ("cambio", "esperado"),
    [
        ({"citas": []}, "no trae ninguna cita"),
        (
            {"citas": [{"fragmento_id": 99, "texto": "promedio acumulado GPA de al menos"}]},
            "no se entregó",
        ),
        ({"citas": [{"fragmento_id": 1, "texto": "GPA"}]}, "demasiado corta"),
        ({"respuesta": "  "}, "está vacía"),
        (
            {"respuesta": "El mínimo es 2,00 y se revisa cada 3 semestres."},
            "cifras de la respuesta que no están en ninguna cita: 3",
        ),
    ],
)
def test_problemas_que_impiden_mostrar(
    docs: sqlite3.Connection, cambio: dict[str, Any], esperado: str
) -> None:
    assert esperado in " ".join(_verificar(docs, {**BUENA, **cambio}))


def test_una_cita_de_otro_fragmento_no_respalda(docs: sqlite3.Connection) -> None:
    cita = {"fragmento_id": 2, "texto": "promedio acumulado GPA de al menos 2,00/4,00"}
    entregados = buscar(docs, "promedio acumulado materia")
    assert {r.id for r in entregados} >= {1, 2}
    problemas = verificar(Borrador.model_validate({**BUENA, "citas": [cita]}), entregados)
    assert "no aparece literalmente en el fragmento 2" in problemas[0]


# ------------------------------------------------------------------ orquestador


def test_respuesta_verificada_a_la_primera(docs: sqlite3.Connection) -> None:
    llm = _guion(BUENA)
    r = responder(docs, llm, PREGUNTA)
    assert (r.estado, r.llamadas_llm, r.motivo) == ("respondida", 1, None)
    assert r.texto == BUENA["respuesta"]
    assert [c.fragmento_id for c in r.citas] == [1]
    assert '<fragmento id="1"' in llm.llamadas[0][1]


def test_reintenta_una_vez_con_el_motivo_del_fallo(docs: sqlite3.Connection) -> None:
    llm = _guion(INVENTADA, BUENA)
    r = responder(docs, llm, PREGUNTA)
    assert (r.estado, r.llamadas_llm) == ("respondida", 2)
    assert "no aparece literalmente" in llm.llamadas[1][1]


def test_dos_fallos_seguidos_terminan_en_abstencion(docs: sqlite3.Connection) -> None:
    llm = _guion(INVENTADA, INVENTADA)
    r = responder(docs, llm, PREGUNTA)
    assert (r.estado, r.texto, r.motivo) == ("abstencion", MENSAJE_ABSTENCION, "no_verificable")
    assert r.citas == ()
    assert len(llm.llamadas) == 2  # nunca un tercer intento
    assert "2,50" not in r.texto


def test_json_invalido_cuenta_como_fallo(docs: sqlite3.Connection) -> None:
    r = responder(docs, _guion("claro, la respuesta es 2,00", '{"respuesta": 3}'), PREGUNTA)
    assert (r.estado, r.motivo) == ("abstencion", "no_verificable")
    assert "no tiene la forma pedida" in r.problemas[0]


def test_acepta_json_dentro_de_un_bloque_de_codigo(docs: sqlite3.Connection) -> None:
    r = responder(docs, _guion("```json\n" + json.dumps(BUENA) + "\n```"), PREGUNTA)
    assert r.estado == "respondida"


def test_si_el_modelo_se_abstiene_no_se_insiste(docs: sqlite3.Connection) -> None:
    llm = _guion({"abstenerse": True})
    r = responder(docs, llm, PREGUNTA)
    assert (r.estado, r.motivo, r.llamadas_llm) == ("abstencion", "el_modelo_se_abstuvo", 1)


def test_sin_fragmentos_no_se_llama_al_llm(docs: sqlite3.Connection) -> None:
    llm = _guion()
    r = responder(docs, llm, "¿Cuánto cuesta el parqueadero?")
    assert (r.estado, r.motivo, r.llamadas_llm) == ("abstencion", "sin_fragmentos", 0)
    assert llm.llamadas == []
