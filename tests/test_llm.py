from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from asistente.llm import (
    MAX_TOKENS_SALIDA,
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

    def __init__(self, *resultados: Any) -> None:
        self.resultados = list(resultados)
        self.llamadas: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.llamadas.append(kwargs)
        # El último resultado se repite: así se simula un error que persiste.
        resultado = self.resultados.pop(0) if len(self.resultados) > 1 else self.resultados[0]
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


class _ErrorHTTP(Exception):
    """Como los errores del SDK de Interactions: traen `status_code`."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"{status_code} UNAVAILABLE. The model is overloaded.")
        self.status_code = status_code


class APITimeoutError(Exception):
    """Mismo nombre que la clase del SDK para los timeouts."""


OK = SimpleNamespace(status="completed", output_text='{"a": 1}')


def _cliente(
    *resultados: Any, **opciones: Any
) -> tuple[GeminiCliente, _Interacciones, list[float]]:
    esperas: list[float] = []
    cliente = GeminiCliente(
        "clave-de-test", esquema=dict(ESQUEMA_BORRADOR), dormir=esperas.append, **opciones
    )
    falso = _Interacciones(*resultados)
    cliente._cliente = SimpleNamespace(interactions=falso)
    return cliente, falso, esperas


def test_una_llamada_es_una_peticion_sin_continuacion_automatica() -> None:
    cliente, falso, _ = _cliente(SimpleNamespace(status="completed", output_text='{"a": 1}'))
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
    cliente, _, _ = _cliente(SimpleNamespace(status="incomplete", output_text='{"abs', usage=uso))
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
    cliente, falso, _ = _cliente(resultado)
    with pytest.raises(ErrorDelLLM):
        cliente.completar("s", "u")
    assert len(falso.llamadas) == 1  # no reintenta por su cuenta


def test_el_429_es_cuota_agotada_y_no_se_reintenta() -> None:
    for error in (RuntimeError("429 RESOURCE_EXHAUSTED"), _ErrorHTTP(429)):
        cliente, falso, esperas = _cliente(error)
        with pytest.raises(CuotaAgotada):
            cliente.completar("s", "u")
        assert len(falso.llamadas) == 1
        assert esperas == []


# ------------------------------------------------------------------ reintentos y tope


def test_el_503_se_reintenta_con_espera_creciente() -> None:
    cliente, falso, esperas = _cliente(_ErrorHTTP(503), _ErrorHTTP(503), OK)
    assert cliente.completar("s", "u") == '{"a": 1}'
    assert len(falso.llamadas) == 3
    assert esperas == [2.0, 4.0]
    assert cliente.peticiones == 3  # cada intento gasta cuota y se cuenta


def test_el_503_persistente_se_rinde_tras_los_intentos() -> None:
    cliente, falso, esperas = _cliente(_ErrorHTTP(503))
    with pytest.raises(ErrorDelLLM, match=r"3 intentos.*503"):
        cliente.completar("s", "u")
    assert len(falso.llamadas) == 3
    assert esperas == [2.0, 4.0]  # no espera después del último


def test_el_timeout_se_reintenta() -> None:
    cliente, falso, _ = _cliente(APITimeoutError("se acabó el tiempo"), OK)
    assert cliente.completar("s", "u") == '{"a": 1}'
    assert len(falso.llamadas) == 2


@pytest.mark.parametrize("codigo", [400, 401, 404])
def test_los_errores_del_cliente_no_se_reintentan(codigo: int) -> None:
    cliente, falso, esperas = _cliente(_ErrorHTTP(codigo))
    with pytest.raises(ErrorDelLLM):
        cliente.completar("s", "u")
    assert len(falso.llamadas) == 1
    assert esperas == []


def test_incomplete_por_razonamiento_lo_dice_y_no_reintenta() -> None:
    uso = SimpleNamespace(
        total_input_tokens=900, total_output_tokens=2040, total_thought_tokens=1990
    )
    cliente, falso, _ = _cliente(SimpleNamespace(status="incomplete", output_text="", usage=uso))
    with pytest.raises(ErrorDelLLM, match="razonamiento se comió el tope"):
        cliente.completar("s", "u")
    assert len(falso.llamadas) == 1  # repetirla daría lo mismo y gastaría cuota


def test_json_invalido_se_devuelve_tal_cual_sin_reintentar() -> None:
    # Validar el JSON es de quien llama: `responder` reintenta con la queja como retroalimentación.
    cliente, falso, _ = _cliente(
        SimpleNamespace(status="completed", output_text='{"abstenerse": tr')
    )
    assert cliente.completar("s", "u") == '{"abstenerse": tr'
    assert len(falso.llamadas) == 1


def test_tope_y_razonamiento_son_configurables() -> None:
    cliente, falso, _ = _cliente(OK, max_tokens_salida=4096, nivel_razonamiento="low")
    cliente.completar("s", "u")
    (llamada,) = falso.llamadas
    assert llamada["generation_config"] == {"max_output_tokens": 4096, "thinking_level": "low"}
    cliente, falso, _ = _cliente(OK)
    cliente.completar("s", "u")
    (llamada,) = falso.llamadas
    assert llamada["generation_config"]["max_output_tokens"] == MAX_TOKENS_SALIDA


def test_el_timeout_llega_al_sdk_sin_reintentos_propios(monkeypatch: pytest.MonkeyPatch) -> None:
    from google import genai

    capturado: dict[str, Any] = {}

    def falso_client(**kwargs: Any) -> Any:
        capturado.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(genai, "Client", falso_client)
    GeminiCliente("k", timeout_ms=30_000)
    opciones = capturado["http_options"]
    assert opciones.timeout == 30_000
    assert opciones.retry_options.attempts == 1  # los reintentos son nuestros y se cuentan


@pytest.mark.parametrize(
    "opciones",
    [
        {"intentos": 0},
        {"max_tokens_salida": 0},
        {"timeout_ms": 0},
        {"nivel_razonamiento": "muchisimo"},
        {"espera_base_s": -1.0},
    ],
)
def test_configuracion_invalida_falla_al_construir(opciones: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        GeminiCliente("k", **opciones)


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
