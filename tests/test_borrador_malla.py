"""El borrador de la malla real: bien formado y, a propósito, sin prerrequisitos."""

from __future__ import annotations

from collections import Counter

from asistente.db import RAIZ, conectar
from asistente.etl import cargar, leer_malla
from asistente.etl.cargar import validar

BORRADOR = RAIZ / "datos" / "borrador" / "malla_cmp.yaml"


def test_el_borrador_es_valido_y_cuadra_con_el_diagrama() -> None:
    malla = leer_malla(BORRADOR)
    assert validar(malla) == []

    por_semestre = Counter(c.semestre for c in malla.cursos)
    assert dict(sorted(por_semestre.items())) == {
        1: 7,
        2: 6,
        3: 7,
        4: 5,
        5: 5,
        6: 4,
        7: 3,
        8: 4,
        9: 5,
    }
    assert sum(c.creditos for c in malla.cursos) == 118
    assert [c.codigo for c in malla.cursos if c.creditos == 0] == [
        f"ESL000{n}" for n in range(1, 7)
    ]
    assert (len(malla.cupos), sum(c.creditos for c in malla.cupos)) == (8, 24)


def test_el_borrador_no_trae_prerrequisitos_sin_confirmar() -> None:
    # Si alguien añade un prerrequisito, este test obliga a hacerlo a conciencia:
    # con la malla confirmada, el archivo se muda a datos/malla.yaml y este test se borra.
    malla = leer_malla(BORRADOR)
    assert [c.codigo for c in malla.cursos if c.pre or c.co] == []


def test_el_borrador_carga_en_la_base() -> None:
    con = conectar(":memory:")
    reporte = cargar(con, leer_malla(BORRADOR))
    assert (reporte.cursos, reporte.cupos) == (46, 8)
    fila = con.execute("SELECT nombre, creditos FROM cursos WHERE codigo = 'ESL0006'").fetchone()
    assert tuple(fila) == ("Inglés Nivel 6", 0)
    assert con.execute("SELECT COUNT(*) FROM cupos").fetchone()[0] == 8
