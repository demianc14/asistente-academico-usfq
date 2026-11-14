"""Frontera con el proveedor del LLM.

Todo el código habla con `ClienteLLM`; cambiar de proveedor es escribir otra clase
con ese método. El proveedor real es Gemini (plan gratuito), configurado en `.env`
con GEMINI_API_KEY y GEMINI_MODELO; la clave la pone Demian.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

MODELO_POR_DEFECTO = "gemini-3.5-flash-lite"
"""El que menos cuota gratuita consume (el mismo de internship-matcher)."""

TIMEOUT_MS = 120_000

MAX_TOKENS_SALIDA = 2048
"""Una respuesta con citas cabe holgada; el tope evita respuestas desbocadas."""

NIVEL_DE_RAZONAMIENTO = "minimal"
"""Citar fragmentos no necesita razonamiento largo, y los tokens de razonamiento
cuentan contra el tope de salida (la primera llamada de humo terminó `incomplete`)."""


class ErrorDelLLM(Exception):
    """El proveedor falló: timeout, red, respuesta vacía, bloqueada o cortada."""


class CuotaAgotada(ErrorDelLLM):
    """El proveedor respondió 429 / RESOURCE_EXHAUSTED."""


class ClienteLLM(Protocol):
    def completar(self, sistema: str, usuario: str) -> str:
        """Devuelve el texto generado. Se espera JSON; quien llama lo valida."""
        ...


class ClienteDeGuion:
    """LLM de mentira para tests y demos: devuelve respuestas fijas en orden."""

    def __init__(self, respuestas: Iterable[str]) -> None:
        self._respuestas = list(respuestas)
        self.llamadas: list[tuple[str, str]] = []

    def completar(self, sistema: str, usuario: str) -> str:
        self.llamadas.append((sistema, usuario))
        if not self._respuestas:
            raise AssertionError("el guion se quedó sin respuestas")
        return self._respuestas.pop(0)


class GeminiCliente:
    """Una llamada a `completar` es exactamente una petición HTTP a Gemini.

    Sin reintentos del SDK y sin continuación automática: así el número de llamadas
    que anuncia la evaluación es el que de verdad se gasta de la cuota. Si algo
    falla se lanza `ErrorDelLLM` y la corrida, que es reanudable, se detiene.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str = MODELO_POR_DEFECTO,
        esquema: dict[str, Any] | None = None,
        timeout_ms: int = TIMEOUT_MS,
    ) -> None:
        from google import genai  # import perezoso: solo hace falta al llamar de verdad
        from google.genai import types

        self._cliente: Any = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        self.modelo = modelo
        self._formato: dict[str, Any] = {"type": "text", "mime_type": "application/json"}
        if esquema is not None:
            self._formato["schema"] = esquema

    def completar(self, sistema: str, usuario: str) -> str:
        try:
            interaccion: Any = self._cliente.interactions.create(
                model=self.modelo,
                system_instruction=sistema,
                input=usuario,
                response_format=self._formato,
                generation_config={
                    "max_output_tokens": MAX_TOKENS_SALIDA,
                    "thinking_level": NIVEL_DE_RAZONAMIENTO,
                },
                automatic_continuation=False,
            )
        except Exception as exc:
            if _es_cuota_agotada(exc):
                raise CuotaAgotada(
                    f"cuota de Gemini agotada ({self.modelo}). Si el límite es por minuto, "
                    f"espera un minuto; si persiste, es el diario. Detalle: {exc}"
                ) from exc
            raise ErrorDelLLM(f"Gemini no respondió: {exc}") from exc

        if interaccion.status != "completed":
            detalle = _diagnostico(interaccion)
            raise ErrorDelLLM(f"respuesta no completada (status={interaccion.status}; {detalle})")
        salida = interaccion.output_text
        if not isinstance(salida, str) or not salida.strip():
            raise ErrorDelLLM("Gemini devolvió una respuesta vacía (¿bloqueada?)")
        return salida


def _diagnostico(interaccion: Any) -> str:
    """Tokens, errores y el inicio de la salida parcial: lo necesario para saber qué cortó."""
    uso = getattr(interaccion, "usage", None)
    partes = [
        f"{nombre}={getattr(uso, nombre, None)}"
        for nombre in ("total_input_tokens", "total_output_tokens", "total_thought_tokens")
    ]
    errores = getattr(interaccion, "errors", None)
    if errores:
        partes.append(f"errores={errores}")
    salida = getattr(interaccion, "output_text", None) or ""
    partes.append(f"salida parcial ({len(salida)} caracteres): {salida[:300]!r}")
    return ", ".join(partes)


def _es_cuota_agotada(error: Exception) -> bool:
    codigo = getattr(error, "code", None) or getattr(error, "status_code", None)
    return codigo == 429 or "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)


def cargar_env(ruta: Path) -> None:
    """Carga `CLAVE=valor` de un .env al entorno, sin pisar lo que ya esté definido."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip("\"'"))


def cliente_desde_env(esquema: dict[str, Any] | None = None) -> GeminiCliente:
    """Lee GEMINI_API_KEY y GEMINI_MODELO del entorno. Sin clave, error claro."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ErrorDelLLM("falta GEMINI_API_KEY en .env (https://aistudio.google.com/apikey)")
    modelo = os.environ.get("GEMINI_MODELO", "").strip() or MODELO_POR_DEFECTO
    return GeminiCliente(api_key, modelo, esquema)
