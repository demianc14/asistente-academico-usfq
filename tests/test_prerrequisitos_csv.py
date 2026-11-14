"""El CSV de prerrequisitos: solo entra a la base lo que Demian marcó como verificado."""

from __future__ import annotations

from pathlib import Path

import pytest

from asistente.db import RAIZ, conectar
from asistente.etl import (
    ErrorDeCarga,
    aplicar_prerrequisitos,
    cargar,
    leer_malla,
    leer_prerrequisitos,
)
from asistente.etl.cargar import COLUMNAS_PRERREQUISITOS

EJEMPLO = RAIZ / "datos" / "ejemplo" / "malla.yaml"
VERSIONADO = RAIZ / "datos" / "malla_prerrequisitos.csv"


def _csv(tmp_path: Path, *filas: str) -> Path:
    ruta = tmp_path / "pre.csv"
    ruta.write_text("\n".join(["curso,requisito,tipo,verificado", *filas]) + "\n", "utf-8")
    return ruta


def _dos_cursos_sin_requisitos() -> tuple[str, str]:
    malla = leer_malla(EJEMPLO)
    libres = [c for c in malla.cursos if not c.pre and not c.co]
    primero, segundo = sorted(libres, key=lambda c: c.semestre)[:2]
    return primero.codigo, segundo.codigo


def test_el_archivo_versionado_no_trae_prerrequisitos_inventados() -> None:
    # Se versiona solo el encabezado: las filas las añade y verifica Demian.
    assert VERSIONADO.read_text("utf-8").splitlines() == [",".join(COLUMNAS_PRERREQUISITOS)]
    assert leer_prerrequisitos(VERSIONADO) == []


@pytest.mark.parametrize("verificado", ["no", ""])
def test_una_fila_sin_verificar_detiene_la_carga(tmp_path: Path, verificado: str) -> None:
    ruta = _csv(tmp_path, "CMP 4005,CMP 3004,pre,si", f"CMP 5006,CMP 4005,pre,{verificado}")
    with pytest.raises(ErrorDeCarga) as exc:
        leer_prerrequisitos(ruta)
    assert exc.value.errores == ["pre.csv:3: CMP4005 → CMP5006 (pre) no está verificado"]


def test_valores_y_columnas_invalidos(tmp_path: Path) -> None:
    with pytest.raises(ErrorDeCarga, match="verificado"):
        leer_prerrequisitos(_csv(tmp_path, "CMP 4005,CMP 3004,pre,tal vez"))
    with pytest.raises(ErrorDeCarga, match="tipo"):
        leer_prerrequisitos(_csv(tmp_path, "CMP 4005,CMP 3004,post,si"))
    ruta = tmp_path / "otro.csv"
    ruta.write_text("curso,requisito\n", "utf-8")
    with pytest.raises(ErrorDeCarga, match="columnas"):
        leer_prerrequisitos(ruta)


def test_las_verificadas_llegan_a_la_tabla(tmp_path: Path) -> None:
    requisito, curso = _dos_cursos_sin_requisitos()
    filas = leer_prerrequisitos(_csv(tmp_path, f"{curso},{requisito},pre,si"))
    malla = aplicar_prerrequisitos(leer_malla(EJEMPLO), filas)
    con = conectar(":memory:")
    cargar(con, malla)
    filas_db = con.execute("SELECT curso, requisito, tipo FROM prerrequisitos").fetchall()
    assert (curso, requisito, "pre") in [tuple(f) for f in filas_db]


def test_un_curso_inexistente_detiene_la_carga(tmp_path: Path) -> None:
    filas = leer_prerrequisitos(_csv(tmp_path, "XYZ 9999,CMP 3004,pre,si"))
    with pytest.raises(ErrorDeCarga, match="XYZ9999 no existe"):
        aplicar_prerrequisitos(leer_malla(EJEMPLO), filas)
