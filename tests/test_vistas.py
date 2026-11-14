from __future__ import annotations

import sqlite3

from asistente.db import conectar
from asistente.etl.cargar import cargar
from asistente.etl.modelos import ArchivoMalla


def test_cadena_de_prerrequisitos_con_profundidad(con: sqlite3.Connection) -> None:
    filas = con.execute(
        "SELECT requisito, profundidad FROM v_cadena_prerrequisitos WHERE curso = 'EJC4001'"
    ).fetchall()
    assert {f["requisito"]: f["profundidad"] for f in filas} == {
        "EJC2002": 1,
        "EJC2003": 1,
        "EJC2001": 2,
        "EJM2001": 2,
        "EJC1002": 3,
        "EJM1001": 3,
        "EJC1001": 4,
    }


def test_la_cadena_atraviesa_correquisitos(con: sqlite3.Connection) -> None:
    filas = con.execute(
        "SELECT requisito FROM v_cadena_prerrequisitos WHERE curso = 'EJC1003' ORDER BY profundidad"
    ).fetchall()
    assert [f["requisito"] for f in filas] == ["EJC1002", "EJC1001"]


def test_cuellos_de_botella(con: sqlite3.Connection) -> None:
    filas = con.execute(
        "SELECT codigo, dependientes_directos, dependientes_totales FROM v_cuellos_de_botella"
    ).fetchall()
    por_curso = {
        f["codigo"]: (f["dependientes_directos"], f["dependientes_totales"]) for f in filas
    }
    assert len(por_curso) == 13
    assert por_curso["EJC1001"] == (1, 8)
    assert por_curso["EJM1001"] == (2, 5)
    assert por_curso["EJC2001"] == (2, 5)
    assert por_curso["EJC4001"] == (0, 0)
    assert por_curso["EJG1001"] == (0, 0)
    peor = max(por_curso, key=lambda c: por_curso[c][1])
    assert peor == "EJC1001"


def test_cursos_habilitados_segun_el_historial(con: sqlite3.Connection) -> None:
    filas = con.execute("SELECT codigo FROM v_cursos_habilitados").fetchall()
    # EJM1002 se reprobó (se puede repetir); EJC2001 está en curso, así que
    # ni él ni lo que depende de él aparece.
    assert {f["codigo"] for f in filas} == {"EJM1002", "EJM2001"}


def test_sin_historial_solo_se_habilita_lo_que_no_tiene_prerrequisitos(
    con: sqlite3.Connection,
) -> None:
    con.execute("DELETE FROM historial_estudiante")
    filas = con.execute(
        "SELECT codigo, correquisitos_pendientes FROM v_cursos_habilitados"
    ).fetchall()
    assert {f["codigo"]: f["correquisitos_pendientes"] for f in filas} == {
        "EJM1001": None,
        "EJC1001": None,
        "EJG1001": None,
        "EJC1003": "EJC1002",  # el laboratorio solo exige un correquisito
    }


def test_creditos_restantes_por_area(con: sqlite3.Connection) -> None:
    filas = con.execute(
        "SELECT area, tipo, creditos_totales, creditos_aprobados, creditos_restantes "
        "FROM v_creditos_restantes_por_area WHERE carrera = 'EJEMPLO'"
    ).fetchall()
    assert {(f["area"], f["tipo"]): tuple(f)[2:] for f in filas} == {
        ("computacion", "obligatorio"): (19, 7, 12),
        ("matematicas", "obligatorio"): (11, 4, 7),
        ("general", "obligatorio"): (3, 3, 0),
        ("datos", "electiva"): (6, 0, 6),
    }


def test_correquisitos_mutuos_no_cuelgan_la_vista() -> None:
    curso = {"nombre": "x", "creditos": 3, "area": "x", "semestre": 1}
    malla = ArchivoMalla.model_validate(
        {
            "carrera": "P",
            "cursos": [
                {**curso, "codigo": "EJA1001", "co": ["EJA1002"]},
                {**curso, "codigo": "EJA1002", "co": ["EJA1001"]},
            ],
        }
    )
    con = conectar(":memory:")
    cargar(con, malla)
    filas = con.execute("SELECT curso, requisito FROM v_cadena_prerrequisitos").fetchall()
    assert {tuple(f) for f in filas} == {("EJA1001", "EJA1002"), ("EJA1002", "EJA1001")}


def test_busqueda_fts5_ignora_tildes_y_sigue_a_los_borrados(con: sqlite3.Connection) -> None:
    con.execute(
        "INSERT INTO documentos (id, titulo, fecha_consulta, sha256) "
        "VALUES (1, 'Reglamento de ejemplo', '2099-01-01', 'abc')"
    )
    con.executemany(
        "INSERT INTO fragmentos (documento_id, seccion, pagina, texto) VALUES (1, ?, ?, ?)",
        [
            ("Art. 1", 1, "La matrícula ordinaria se paga antes del inicio de clases."),
            ("Art. 2", 2, "El retiro de una materia se solicita en la oficina de registro."),
        ],
    )
    consulta = (
        "SELECT f.seccion FROM fragmentos_fts JOIN fragmentos AS f ON f.id = fragmentos_fts.rowid "
        "WHERE fragmentos_fts MATCH ? ORDER BY bm25(fragmentos_fts)"
    )
    assert [f["seccion"] for f in con.execute(consulta, ("matricula",))] == ["Art. 1"]

    con.execute("DELETE FROM fragmentos WHERE seccion = 'Art. 1'")
    assert con.execute(consulta, ("matricula",)).fetchall() == []
