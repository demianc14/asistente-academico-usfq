from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from asistente.llm import (
    MODELO_POR_DEFECTO,
    CuotaAgotada,
    ErrorDelLLM,
    GeminiCliente,
    cargar_env,
    cliente_desde_env,
)
from asistente.rag.verificar import ESQUEMA_BORRADOR, Borrador


class _Interacciones:
    """Sustituye a `client.interactions`: registra las llamadas y devuelve lo preparado."""

    def __init__(self, resultado: Any) -> None:
        self.resultado = resultado
        self.llamadas: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.llamadas.append(kwargs)
        if isinstance(self.resultado, Exception):
            raise self.resultado
        return self.resultado


def _cliente(resultado: Any) -> tuple[GeminiCliente, _Interacciones]:
    cliente = GeminiCliente("clave-de-test", esquema=dict(ESQUEMA_BORRADOR))
    falso = _Interacciones(resultado)
    cliente._cliente = SimpleNamespace(interactions=falso)
    return cliente, falso


def test_una_llamada_es_una_peticion_sin_continuacion_automatica() -> None:
    cliente, falso = _cliente(SimpleNamespace(status="completed", output_text='{"a": 1}'))
    assert cliente.completar("sistema", "usuario") == '{"a": 1}'
    (llamada,) = falso.llamadas
    assert llamada["automatic_continuation"] is False
    assert llamada["system_instruction"] == "sistema"
    assert llamada["input"] == "usuario"
    assert llamada["model"] == MODELO_POR_DEFECTO
    assert llamada["response_format"]["mime_type"] == "application/json"
    assert llamada["generation_config"]["thinking_level"] == "minimal"


def test_el_error_de_respuesta_cortada_dice_por_que() -> None:
    uso = SimpleNamespace(total_input_tokens=900, total_output_tokens=12, total_thought_tokens=8000)
    cliente, _ = _cliente(SimpleNamespace(status="incomplete", output_text='{"abs', usage=uso))
    with pytest.raises(ErrorDelLLM, match=r"total_thought_tokens=8000.*\{\"abs"):
        cliente.completar("s", "u")


@pytest.mark.parametrize(
    "resultado",
    [
        SimpleNamespace(status="incomplete", output_text="{"),
        SimpleNamespace(status="requires_continuation", output_text="{"),
        SimpleNamespace(status="completed", output_text="  "),
        RuntimeError("se cayó la red"),
    ],
)
def test_las_fallas_del_proveedor_son_error_del_llm(resultado: Any) -> None:
    cliente, falso = _cliente(resultado)
    with pytest.raises(ErrorDelLLM):
        cliente.completar("s", "u")
    assert len(falso.llamadas) == 1  # no reintenta por su cuenta


def test_el_429_es_cuota_agotada() -> None:
    cliente, _ = _cliente(RuntimeError("429 RESOURCE_EXHAUSTED"))
    with pytest.raises(CuotaAgotada):
        cliente.completar("s", "u")


def test_cliente_desde_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ErrorDelLLM, match="GEMINI_API_KEY"):
        cliente_desde_env()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("GEMINI_MODELO", raising=False)
    assert cliente_desde_env().modelo == MODELO_POR_DEFECTO
    monkeypatch.setenv("GEMINI_MODELO", "gemini-otro")
    assert cliente_desde_env().modelo == "gemini-otro"


def test_cargar_env_no_pisa_el_entorno(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YA_DEFINIDA", "entorno")
    monkeypatch.delenv("NUEVA", raising=False)
    ruta = tmp_path / ".env"
    ruta.write_text('# comentario\nYA_DEFINIDA=archivo\nNUEVA="valor"\n', encoding="utf-8")
    cargar_env(ruta)
    cargar_env(tmp_path / "no-existe")
    import os

    assert os.environ["YA_DEFINIDA"] == "entorno"
    assert os.environ.pop("NUEVA") == "valor"


def test_el_esquema_tiene_los_campos_del_borrador() -> None:
    propiedades = ESQUEMA_BORRADOR["properties"]
    assert isinstance(propiedades, dict)
    assert set(propiedades) == set(Borrador.model_fields)
