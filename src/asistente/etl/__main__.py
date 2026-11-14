"""`python -m asistente.etl`: valida y carga la malla, la oferta y el historial."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from asistente.db import RAIZ, RUTA_DB, conectar
from asistente.etl.cargar import (
    ErrorDeCarga,
    aplicar_prerrequisitos,
    cargar,
    leer_historial,
    leer_malla,
    leer_prerrequisitos,
)

MALLA_REAL = RAIZ / "datos" / "malla.yaml"
MALLA_EJEMPLO = RAIZ / "datos" / "ejemplo" / "malla.yaml"
HISTORIAL_LOCAL = RAIZ / "datos" / "local" / "historial.yaml"
HISTORIAL_EJEMPLO = RAIZ / "datos" / "ejemplo" / "historial.yaml"
PRERREQUISITOS = RAIZ / "datos" / "malla_prerrequisitos.csv"


def _elegir_entradas(malla: Path | None, historial: Path | None) -> tuple[Path, Path | None]:
    """Sin argumentos: datos reales si existen; si no, los de ejemplo."""
    if malla is None:
        malla = MALLA_REAL if MALLA_REAL.exists() else MALLA_EJEMPLO
    if historial is None:
        if HISTORIAL_LOCAL.exists():
            historial = HISTORIAL_LOCAL
        elif malla == MALLA_EJEMPLO:
            historial = HISTORIAL_EJEMPLO
    return malla, historial


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asistente.etl", description=__doc__)
    parser.add_argument("--malla", type=Path, help="YAML de malla y oferta")
    parser.add_argument("--historial", type=Path, help="YAML de historial (solo local)")
    parser.add_argument(
        "--prerrequisitos",
        type=Path,
        help=f"CSV de requisitos verificados (por defecto {PRERREQUISITOS.name} con la malla real)",
    )
    parser.add_argument(
        "--db", type=Path, default=RUTA_DB, help=f"base SQLite (por defecto {RUTA_DB})"
    )
    args = parser.parse_args(argv)

    ruta_malla, ruta_historial = _elegir_entradas(args.malla, args.historial)
    print(f"Malla:     {ruta_malla}")
    ruta_pre = args.prerrequisitos
    if ruta_pre is None and ruta_malla == MALLA_REAL and PRERREQUISITOS.exists():
        ruta_pre = PRERREQUISITOS
    print(f"Historial: {ruta_historial or '(ninguno)'}")
    print(f"Requisitos: {ruta_pre or '(los de la malla)'}")
    print(f"Base:      {args.db}")

    try:
        malla = leer_malla(ruta_malla)
        if ruta_pre is not None:
            malla = aplicar_prerrequisitos(malla, leer_prerrequisitos(ruta_pre))
        historial = leer_historial(ruta_historial) if ruta_historial else None
        con = conectar(args.db)
        try:
            reporte = cargar(con, malla, historial)
        finally:
            con.close()
    except ErrorDeCarga as exc:
        print(
            f"\nCarga detenida: {len(exc.errores)} error(es). La base no se modificó.",
            file=sys.stderr,
        )
        for error in exc.errores:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("\n" + reporte.texto())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
