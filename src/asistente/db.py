"""Conexión a SQLite y aplicación del esquema."""

from __future__ import annotations

import sqlite3
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DIR_SQL = RAIZ / "sql"
RUTA_DB = RAIZ / "datos" / "local" / "asistente.db"


def conectar(ruta: Path | str = RUTA_DB) -> sqlite3.Connection:
    """Abre la base con claves foráneas activas y filas accesibles por nombre."""
    if isinstance(ruta, Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def crear_esquema(con: sqlite3.Connection) -> None:
    """Aplica tablas (idempotente) y recrea las vistas."""
    con.executescript((DIR_SQL / "schema.sql").read_text(encoding="utf-8"))
    con.executescript((DIR_SQL / "vistas.sql").read_text(encoding="utf-8"))
