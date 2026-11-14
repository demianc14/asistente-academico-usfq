"""Nada privado en git: ni en lo que está versionado ni en ningún commit pasado."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from asistente.db import RAIZ

PREFIJOS_PROHIBIDOS = ("datos/local/", "datos/publico/")
SUFIJOS_PROHIBIDOS = (".db", ".db-wal", ".db-shm", ".db-journal")
NOMBRES_PROHIBIDOS = (".env",)
# Únicos CSV versionados fuera de powerbi/: la misma excepción que hace .gitignore.
CSV_PUBLICOS = ("datos/malla_prerrequisitos.csv",)


def _git(*args: str) -> list[str]:
    if shutil.which("git") is None or not (RAIZ / ".git").exists():
        pytest.skip("no es un repositorio git")
    salida = subprocess.run(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=RAIZ,
        capture_output=True,
        text=True,
        check=False,
    )
    if salida.returncode != 0:
        pytest.skip("el repositorio aún no tiene commits")
    return [linea for linea in salida.stdout.splitlines() if linea.strip()]


def _es_privado(ruta: str) -> bool:
    nombre = ruta.rsplit("/", 1)[-1]
    return (
        ruta.startswith(PREFIJOS_PROHIBIDOS)
        or ruta.endswith(SUFIJOS_PROHIBIDOS)
        or nombre in NOMBRES_PROHIBIDOS
        or (
            ruta.endswith((".csv", ".pbix"))
            and not ruta.startswith("powerbi/")
            and ruta not in CSV_PUBLICOS
        )
    )


def test_el_detector_reconoce_rutas_privadas() -> None:
    assert _es_privado("datos/local/historial.yaml")
    assert _es_privado("datos/local/asistente.db")
    assert _es_privado("datos/publico/manual.pdf")
    assert _es_privado(".env")
    assert _es_privado("exportes/consultas.csv")
    assert not _es_privado("datos/ejemplo/historial.yaml")
    assert not _es_privado("powerbi/ejemplo/cuellos.csv")
    assert not _es_privado("datos/malla_prerrequisitos.csv")
    assert _es_privado("datos/otra_cosa.csv")
    assert not _es_privado(".env.example")


def test_nada_privado_versionado() -> None:
    assert [r for r in _git("ls-files") if _es_privado(r)] == []


def test_nada_privado_en_el_historial_de_git() -> None:
    rutas = set(_git("log", "--all", "--name-only", "--pretty=format:"))
    assert sorted(r for r in rutas if _es_privado(r)) == []


def test_gitignore_cubre_lo_privado() -> None:
    ignoradas = _git(
        "check-ignore", "datos/local/historial.yaml", "datos/local/asistente.db", ".env", "x.csv"
    )
    assert len(ignoradas) == 4
