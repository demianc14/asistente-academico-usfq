"""CI y Docker: sin claves, sin red en los tests y sin datos privados dentro de la imagen."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from asistente.db import RAIZ

CI = RAIZ / ".github" / "workflows" / "ci.yml"
DOCKERFILE = RAIZ / "Dockerfile"
DOCKERIGNORE = RAIZ / ".dockerignore"
COMPOSE = RAIZ / "compose.yaml"

PRIVADOS = (".env", "datos/local", "datos/publico", "*.db")


def _leer(ruta: Path) -> str:
    assert ruta.is_file(), f"falta {ruta.relative_to(RAIZ)}"
    return ruta.read_text(encoding="utf-8")


def _pasos_ci() -> list[dict[str, Any]]:
    flujo: dict[Any, Any] = yaml.safe_load(_leer(CI))
    pasos: list[dict[str, Any]] = []
    for trabajo in flujo["jobs"].values():
        pasos.extend(trabajo["steps"])
    return pasos


def test_ci_corre_ruff_mypy_y_pytest() -> None:
    comandos = " ".join(str(p.get("run", "")) for p in _pasos_ci())
    for herramienta in ("ruff format --check", "ruff check", "mypy", "pytest"):
        assert herramienta in comandos, herramienta


def test_ci_no_usa_claves() -> None:
    texto = _leer(CI)
    assert "secrets." not in texto
    assert "GEMINI_API_KEY" not in texto


def test_ci_trae_el_historial_completo_para_el_test_de_privacidad() -> None:
    checkout = [p for p in _pasos_ci() if str(p.get("uses", "")).startswith("actions/checkout")]
    assert checkout and checkout[0].get("with", {}).get("fetch-depth") == 0


def test_dockerignore_excluye_lo_privado() -> None:
    lineas = {linea.strip().rstrip("/") for linea in _leer(DOCKERIGNORE).splitlines()}
    for ruta in PRIVADOS:
        assert ruta in lineas, ruta


def test_dockerfile_no_copia_datos_privados_ni_claves() -> None:
    copias = [
        linea
        for linea in _leer(DOCKERFILE).splitlines()
        if linea.strip().upper().startswith(("COPY", "ADD"))
    ]
    assert copias
    for linea in copias:
        assert not any(p in linea for p in (".env", "datos/local", "datos/publico", " . ")), linea
    assert "GEMINI_API_KEY" not in _leer(DOCKERFILE)


def test_compose_corre_etl_descarga_e_ingesta_sin_env() -> None:
    texto = _leer(COMPOSE)
    assert "env_file" not in texto and "GEMINI_API_KEY" not in texto
    servicios: dict[str, Any] = yaml.safe_load(texto)["services"]
    comando = " ".join(" ".join(s.get("command", [])) for s in servicios.values())
    orden = [comando.find(paso) for paso in ("asistente.etl", "descargar_fuentes", "rag.ingesta")]
    assert -1 not in orden and orden == sorted(orden)
