"""ETL: malla, oferta e historial → SQLite, con validación fail-fast."""

from asistente.etl.cargar import (
    ErrorDeCarga,
    ReporteCalidad,
    aplicar_prerrequisitos,
    cargar,
    leer_historial,
    leer_malla,
    leer_prerrequisitos,
)

__all__ = [
    "ErrorDeCarga",
    "ReporteCalidad",
    "aplicar_prerrequisitos",
    "cargar",
    "leer_historial",
    "leer_malla",
    "leer_prerrequisitos",
]
