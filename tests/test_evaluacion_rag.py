from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from asistente.db import conectar
from asistente.evaluacion.rag import (
    Corrida,
    Pregunta,
    cargar_preguntas,
    evaluar_recuperacion,
    evaluar_respuestas,
    resumir_recuperacion,
    resumir_respuestas,
)
from asistente.llm import ClienteDeGuion
from asistente.rag.ingesta import Fragmento, guardar

BUENA = json.dumps(
    {
        "abstenerse": False,
        "respuesta": "Solo dos veces.",
        "citas": [{"fragmento_id": 1, "texto": "hasta por dos ocasiones de una misma materia"}],
    }
)
ABSTENCION = json.dumps({"abstenerse": True})

PREGUNTAS = [
    Pregunta(
        id="a",
        pregunta="¿Cuántas veces puedo retirarme de una materia?",
        tiene_respuesta=True,
        evidencia="retirarse hasta por dos ocasiones",
    ),
    Pregunta(id="b", pregunta="¿Qué materia dicta cada profesor?", tiene_respuesta=False),
]


def _base() -> sqlite3.Connection:
    con = conectar(":memory:")
    guardar(
        con,
        "Doc",
        None,
        "sha",
        "2099-01-01",
        [
            Fragmento(
                "RETIROS",
                1,
                "Solo es posible retirarse hasta por dos ocasiones de una misma materia.",
            ),
            Fragmento("SYLLABUS", 2, "Cada profesor entrega el syllabus de su materia."),
        ],
    )
    return con


def test_el_archivo_de_preguntas_cumple_el_diseno() -> None:
    preguntas = cargar_preguntas()
    assert len(preguntas) == 40
    assert sum(p.tiene_respuesta for p in preguntas) == 30
    # Evidencias cortas: son pasajes para ubicar el fragmento, no copias del documento.
    assert max(len((p.evidencia or "").split()) for p in preguntas) <= 18


def test_evidencia_y_tiene_respuesta_van_juntas() -> None:
    with pytest.raises(ValidationError):
        Pregunta(id="x", pregunta="¿?", tiene_respuesta=True)
    with pytest.raises(ValidationError):
        Pregunta(id="x", pregunta="¿?", tiene_respuesta=False, evidencia="algo")


def test_recuperacion_puesto_y_preguntas_mal_etiquetadas() -> None:
    con = _base()
    mala = Pregunta(
        id="c", pregunta="¿retiro?", tiene_respuesta=True, evidencia="texto que no existe"
    )
    registros = evaluar_recuperacion(con, [*PREGUNTAS, mala], k=5)
    assert [r["id"] for r in registros] == ["a", "c"]  # las sin respuesta no entran
    assert registros[0]["puesto"] == 1
    resumen = resumir_recuperacion(registros)
    assert resumen["invalidas"] == ["c"]
    assert (resumen["en_top_k"], resumen["recall_top_k"], resumen["mrr"]) == (1, 1.0, 1.0)


def test_la_corrida_guarda_caso_por_caso_y_se_retoma(tmp_path: Path) -> None:
    con = _base()
    corrida = Corrida(tmp_path / "corridas" / "prueba.jsonl")

    # Primera pasada: el LLM "se cae" después del primer caso.
    with pytest.raises(AssertionError, match="sin respuestas"):
        evaluar_respuestas(con, ClienteDeGuion([BUENA]), PREGUNTAS, corrida, k=5)
    assert corrida.hechos() == {"a"}

    # Segunda pasada: solo se corre lo pendiente (una llamada, no dos).
    llm = ClienteDeGuion([ABSTENCION])
    registros = evaluar_respuestas(con, llm, PREGUNTAS, corrida, k=5)
    assert len(llm.llamadas) == 1
    assert [r["id"] for r in registros] == ["a", "b"]

    resumen = resumir_respuestas(registros)
    assert resumen["citas_no_verificables"] == 0
    assert (resumen["respondidas"], resumen["con_respuesta"]) == (1, 1)
    assert resumen["respondidas_citando_el_fragmento_esperado"] == 1
    assert (resumen["abstenciones_correctas"], resumen["sin_respuesta"]) == (1, 1)
    assert resumen["llamadas_llm"] == 2


def _base_en_archivo(ruta: Path) -> Path:
    con = _base()
    destino = sqlite3.connect(ruta)
    con.backup(destino)
    destino.close()
    con.close()
    return ruta


def test_con_llm_no_se_corre_sin_marcar_los_conjuntos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from asistente.evaluacion import rag

    db = _base_en_archivo(tmp_path / "base.db")
    monkeypatch.setattr(rag, "DIR_CORRIDAS", tmp_path)
    monkeypatch.setattr(rag, "cargar_preguntas", lambda: PREGUNTAS)
    assert rag.main(["--db", str(db)]) == 2
    assert rag.main(["--db", str(db), "--conjunto", "dev", "--confirmar"]) == 2
    assert "no tienen `conjunto`" in capsys.readouterr().err


def test_sin_confirmar_solo_anuncia_las_llamadas_del_conjunto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from asistente.evaluacion import rag

    db = _base_en_archivo(tmp_path / "base.db")
    marcadas = [
        PREGUNTAS[0].model_copy(update={"conjunto": "dev"}),
        PREGUNTAS[1].model_copy(update={"conjunto": "test"}),
    ]
    monkeypatch.setattr(rag, "DIR_CORRIDAS", tmp_path)
    monkeypatch.setattr(rag, "cargar_preguntas", lambda: marcadas)
    monkeypatch.setattr(rag, "cliente_desde_env", lambda *_: pytest.fail("no debía llamarse"))
    assert rag.main(["--db", str(db), "--conjunto", "dev"]) == 0
    assert "1 preguntas pendientes de 1; entre 1 y 2 llamadas" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.jsonl"))


def test_humo_hace_una_sola_llamada_aunque_la_cita_falle() -> None:
    from asistente.evaluacion.rag import humo

    mala = json.dumps(
        {
            "abstenerse": False,
            "respuesta": "Dos.",
            "citas": [{"fragmento_id": 1, "texto": "x" * 20}],
        }
    )
    llm = ClienteDeGuion([mala])
    registro = humo(_base(), llm, PREGUNTAS[0], k=5)
    assert len(llm.llamadas) == 1
    assert registro["formato"] == "ok"
    assert registro["problemas_de_verificacion"]
    assert registro["crudo"] == mala
