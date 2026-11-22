"""El evaluador del agente, con escenarios propios. No carga evals/escenarios_agente.yaml."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from asistente.evaluacion.agente import (
    Escenario,
    cargar_escenarios,
    correr_escenario,
    main,
    resumir,
    reverificar,
)
from asistente.planificador import Plan

APROBADOS = ["EJM1001", "EJC1001", "EJG1001", "EJC1002", "EJC1003"]


def _proponer(*cursos: dict[str, str]) -> dict[str, Any]:
    return {
        "accion": "proponer",
        "plan": {"semestres": [{"periodo": "209910", "cursos": list(cursos)}]},
    }


# Curso inventado en una consulta y en la primera propuesta; válido a la segunda.
CON_CORRECCION: dict[str, Any] = {
    "id": "x-01",
    "categoria": "prueba",
    "descripcion": "inventado dos veces y una corrección",
    "solicitud": {
        "texto": "Cálculo II y Algoritmos",
        "periodo": "209910",
        "creditos_max": 18,
        "aprobados": APROBADOS,
        "cursos_pedidos": ["EJM1002", "EJC2002"],
        "preferencias": ["sin viernes"],
    },
    "guion": [
        {"accion": "herramienta", "nombre": "prerrequisitos", "argumentos": {"curso": "EJX9999"}},
        _proponer({"codigo": "EJX9999"}),
        _proponer({"codigo": "EJM1002", "nrc": "1002"}),
        "sobra",
    ],
    "esperado": {
        "estado": "plan",
        "vueltas": 1,
        "llamadas": 3,
        "horario_verificado": True,
        "plan": {"209910": ["EJM1002/1002"]},
        "pasos": ["prerrequisitos", "verificar_plan", "verificar_plan"],
        "inventados": ["EJX9999"],
        "intentos_inventados": 2,
        "preferencias_no_verificadas": ["sin viernes"],
        "pedidos_fuera": {"EJC2002": ["prerrequisito_faltante"]},
    },
}

# El mismo guion con expectativas equivocadas: el evaluador tiene que decir cuáles.
MAL_ESPERADO: dict[str, Any] = {
    **CON_CORRECCION,
    "id": "x-02",
    "esperado": {
        **CON_CORRECCION["esperado"],
        "vueltas": 0,
        "pasos": ["horarios", "verificar_plan", "verificar_plan"],
        "inventados": [],
    },
}

ABSTENCION: dict[str, Any] = {
    "id": "x-03",
    "categoria": "prueba",
    "descripcion": "tres salidas ilegibles",
    "solicitud": {
        "texto": "algo",
        "periodo": "209910",
        "creditos_max": 18,
        "aprobados": APROBADOS,
    },
    "guion": ["a", "b", "c"],
    "esperado": {
        "estado": "abstencion",
        "motivo": "salida_invalida",
        "vueltas": 0,
        "llamadas": 3,
        "pasos": ["salida_invalida", "salida_invalida", "salida_invalida"],
        "inventados": [],
        "intentos_inventados": 0,
    },
}


def _correr(con: sqlite3.Connection, crudo: dict[str, Any]) -> dict[str, Any]:
    return correr_escenario(con, Escenario.model_validate(crudo))


def test_escenario_que_cumple(con: sqlite3.Connection) -> None:
    registro = _correr(con, CON_CORRECCION)
    assert registro["cumple_lo_esperado"]
    assert registro["diferencias"] == []
    assert registro["inventados"] == ["EJX9999"]  # únicos
    assert registro["intentos_inventados"] == 2
    assert registro["pasos_correctos"] == 3
    assert registro["plan_reverificado"] is True
    assert registro["llamadas_del_guion_sin_pedir"] == 1


def test_escenario_que_no_cumple_dice_en_que(con: sqlite3.Connection) -> None:
    registro = _correr(con, MAL_ESPERADO)
    assert not registro["cumple_lo_esperado"]
    assert registro["diferencias"] == ["vueltas", "pasos", "inventados"]
    assert registro["pasos_correctos"] == 2
    assert registro["plan_reverificado"] is True  # la garantía no depende de lo esperado


def test_la_reverificacion_usa_la_solicitud_y_no_los_datos_del_plan(
    con: sqlite3.Connection,
) -> None:
    solicitud = Escenario.model_validate(CON_CORRECCION).solicitud
    semestre = {"periodo": "209910", "cursos": [{"codigo": "EJC2002", "nrc": "1005"}]}
    # Un plan que se verifica bien a sí mismo porque trae aprobados que la solicitud no tiene.
    colado = Plan.model_validate(
        {
            "aprobados": [*APROBADOS, "EJC2001", "EJM2001"],
            "creditos_max": 99,
            "semestres": [semestre],
        }
    )
    assert not reverificar(con, solicitud, colado)
    # Lo mismo con un periodo que no es el pedido.
    otro = {"periodo": "209920", "cursos": [{"codigo": "EJM1002"}]}
    otro_periodo = Plan.model_validate(
        {"aprobados": APROBADOS, "creditos_max": 18, "semestres": [otro]}
    )
    assert not reverificar(con, solicitud, otro_periodo)
    # Y una solicitud sin límite o sin periodo nunca debió acabar en un plan.
    assert not reverificar(con, solicitud.model_copy(update={"periodo": None}), colado)


def test_resumen(con: sqlite3.Connection) -> None:
    resumen = resumir([_correr(con, e) for e in (CON_CORRECCION, MAL_ESPERADO, ABSTENCION)])
    assert resumen["escenarios"] == 3
    assert resumen["cumplen_lo_esperado"] == 2
    assert resumen["no_cumplen"] == {"x-02": ["vueltas", "pasos", "inventados"]}
    assert resumen["terminan_con_plan_valido"] == 2
    assert resumen["planes_mostrados_que_no_pasan_la_reverificacion"] == 0
    assert resumen["vueltas_de_los_que_terminan_con_plan"] == {"1": 2}
    assert resumen["terminan_en_abstencion"] == {"salida_invalida": 1}
    assert (resumen["pasos_con_la_herramienta_esperada"], resumen["pasos_esperados"]) == (8, 9)
    assert resumen["cursos_inventados_atrapados"] == 2
    assert resumen["cursos_inventados_esperados"] == 1
    assert resumen["intentos_con_curso_inventado"] == 4
    assert resumen["llamadas_a_gemini"] == 0
    assert "no a Gemini" in resumen["nota"]


def test_ids_repetidos(tmp_path: Path) -> None:
    ruta = tmp_path / "e.yaml"
    ruta.write_text(yaml.safe_dump([ABSTENCION, ABSTENCION]), encoding="utf-8")
    with pytest.raises(ValueError, match="repetidos"):
        cargar_escenarios(ruta)


def test_la_corrida_se_guarda_y_no_se_pisa(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    escenarios = tmp_path / "e.yaml"
    escenarios.write_text(yaml.safe_dump([CON_CORRECCION, ABSTENCION]), encoding="utf-8")
    argumentos = ["--escenarios", str(escenarios), "--corridas", str(tmp_path), "--nombre", "p"]

    assert main(argumentos) == 0
    lineas = (tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(linea)["id"] for linea in lineas] == ["x-01", "x-03"]
    resumen = json.loads((tmp_path / "p.resumen.json").read_text(encoding="utf-8"))
    assert resumen["cumplen_lo_esperado"] == 2
    assert len(resumen["escenarios_sha256"]) == 64

    antes = (tmp_path / "p.jsonl").read_bytes()
    assert main(argumentos) == 2
    assert "no se pisa" in capsys.readouterr().err
    assert (tmp_path / "p.jsonl").read_bytes() == antes
    assert main([*argumentos, "--rehacer"]) == 0
