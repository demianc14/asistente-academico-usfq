from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from asistente.db import RAIZ, conectar
from asistente.etl import cargar, leer_historial, leer_malla

EJEMPLO = RAIZ / "datos" / "ejemplo"


@pytest.fixture
def ejemplo() -> Path:
    return EJEMPLO


@pytest.fixture
def con() -> Iterator[sqlite3.Connection]:
    """Base en memoria con la malla y el historial de ejemplo cargados."""
    conexion = conectar(":memory:")
    cargar(conexion, leer_malla(EJEMPLO / "malla.yaml"), leer_historial(EJEMPLO / "historial.yaml"))
    yield conexion
    conexion.close()
