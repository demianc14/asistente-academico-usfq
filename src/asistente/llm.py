"""Frontera con el proveedor del LLM.

Todo el código habla con `ClienteLLM`; cambiar de proveedor es escribir otra clase
con ese método. El proveedor real es Gemini (plan gratuito), configurado en `.env`
con GEMINI_API_KEY y GEMINI_MODELO; la clave la pone Demian.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal, Protocol, get_args

MODELO_POR_DEFECTO = "gemini-3.5-flash-lite"
"""El que menos cuota gratuita consume (el mismo de internship-matcher)."""

TIMEOUT_MS = 120_000
"""Por petición. Dos minutos es holgura para una respuesta corta, no expectativa."""

MAX_TOKENS_SALIDA = 2048
"""Una respuesta con citas cabe holgada; el tope evita respuestas desbocadas."""

NivelDeRazonamiento = Literal["minimal", "low", "medium", "high", "xhigh"]
"""Los valores que acepta `thinking_level` en la API de Interactions. No hay un presupuesto
en tokens: el nivel es el único control del razonamiento."""

NIVEL_DE_RAZONAMIENTO: NivelDeRazonamiento = "minimal"
"""Citar fragmentos no necesita razonamiento largo, y los tokens de razonamiento
cuentan contra el tope de salida (la primera llamada de humo terminó `incomplete`).
El humo del 22-nov confirmó que con `minimal` el razonamiento no se come el tope."""

INTENTOS = 3
"""Peticiones como máximo por llamada a `completar`, contando la primera."""

ESPERA_BASE_S = 2.0
"""Backoff exponencial entre intentos: 2 s, luego 4 s."""

CODIGOS_TRANSITORIOS = frozenset({500, 502, 503, 504})
"""Errores del servidor que suelen pasar solos (503 = modelo saturado). El 429 no está:
reintentar una cuota agotada solo gasta más cuota."""


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
    """Una llamada a `completar` hace entre 1 y `intentos` peticiones HTTP a Gemini.

    Los reintentos son nuestros, no del SDK, y solo ante errores transitorios del servidor
    (5xx) o timeout, con espera exponencial. Cada petición se cuenta en `peticiones`, así
    que el gasto real de cuota se puede reportar. No se reintenta: el 429, los demás 4xx,
    una respuesta `incomplete` (repetirla daría lo mismo) ni un JSON inválido (validarlo es
    de quien llama). Si algo falla se lanza `ErrorDelLLM` y la corrida, que es reanudable,
    se detiene.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str = MODELO_POR_DEFECTO,
        esquema: dict[str, Any] | None = None,
        timeout_ms: int = TIMEOUT_MS,
        max_tokens_salida: int = MAX_TOKENS_SALIDA,
        nivel_razonamiento: NivelDeRazonamiento = NIVEL_DE_RAZONAMIENTO,
        intentos: int = INTENTOS,
        espera_base_s: float = ESPERA_BASE_S,
        dormir: Callable[[float], None] = time.sleep,
    ) -> None:
        if intentos < 1:
            raise ValueError(f"intentos debe ser al menos 1 (llegó {intentos})")
        if max_tokens_salida < 1:
            raise ValueError(f"max_tokens_salida debe ser positivo (llegó {max_tokens_salida})")
        if timeout_ms < 1:
            raise ValueError(f"timeout_ms debe ser positivo (llegó {timeout_ms})")
        if espera_base_s < 0:
            raise ValueError(f"espera_base_s no puede ser negativa (llegó {espera_base_s})")
        if nivel_razonamiento not in get_args(NivelDeRazonamiento):
            raise ValueError(f"nivel_razonamiento desconocido: {nivel_razonamiento!r}")

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
        self.max_tokens_salida = max_tokens_salida
        self.nivel_razonamiento = nivel_razonamiento
        self.intentos = intentos
        self.espera_base_s = espera_base_s
        self._dormir = dormir
        self.peticiones = 0
        """Peticiones HTTP hechas en total, reintentos incluidos: lo que gasta la cuota."""
        self._formato: dict[str, Any] = {"type": "text", "mime_type": "application/json"}
        if esquema is not None:
            self._formato["schema"] = esquema

    def completar(self, sistema: str, usuario: str) -> str:
        interaccion = self._pedir_con_reintentos(sistema, usuario)
        if interaccion.status != "completed":
            detalle = _diagnostico(interaccion, self.max_tokens_salida)
            raise ErrorDelLLM(f"respuesta no completada (status={interaccion.status}; {detalle})")
        salida = interaccion.output_text
        if not isinstance(salida, str) or not salida.strip():
            raise ErrorDelLLM("Gemini devolvió una respuesta vacía (¿bloqueada?)")
        return salida

    def _pedir_con_reintentos(self, sistema: str, usuario: str) -> Any:
        for intento in range(1, self.intentos + 1):
            try:
                return self._pedir(sistema, usuario)
            except Exception as exc:
                if _es_cuota_agotada(exc):
                    raise CuotaAgotada(
                        f"cuota de Gemini agotada ({self.modelo}). Si el límite es por minuto, "
                        f"espera un minuto; si persiste, es el diario. Detalle: {exc}"
                    ) from exc
                if not _es_transitorio(exc):
                    raise ErrorDelLLM(f"Gemini no respondió: {exc}") from exc
                if intento == self.intentos:
                    raise ErrorDelLLM(
                        f"Gemini no respondió tras {self.intentos} intentos "
                        f"(error transitorio): {exc}"
                    ) from exc
                self._dormir(self.espera_base_s * 2 ** (intento - 1))
        raise AssertionError("inalcanzable: el bucle devuelve o lanza")  # pragma: no cover

    def _pedir(self, sistema: str, usuario: str) -> Any:
        self.peticiones += 1
        return self._cliente.interactions.create(
            model=self.modelo,
            system_instruction=sistema,
            input=usuario,
            response_format=self._formato,
            generation_config={
                "max_output_tokens": self.max_tokens_salida,
                "thinking_level": self.nivel_razonamiento,
            },
            automatic_continuation=False,
        )


def _diagnostico(interaccion: Any, tope: int) -> str:
    """Tokens, errores y el inicio de la salida parcial: lo necesario para saber qué cortó."""
    uso = getattr(interaccion, "usage", None)
    partes = [
        f"{nombre}={getattr(uso, nombre, None)}"
        for nombre in ("total_input_tokens", "total_output_tokens", "total_thought_tokens")
    ]
    razonamiento = getattr(uso, "total_thought_tokens", None)
    if isinstance(razonamiento, int) and razonamiento * 2 >= tope:
        partes.insert(
            0,
            f"el razonamiento se comió el tope ({razonamiento} de {tope} tokens): "
            "baja nivel_razonamiento o sube max_tokens_salida",
        )
    errores = getattr(interaccion, "errors", None)
    if errores:
        partes.append(f"errores={errores}")
    salida = getattr(interaccion, "output_text", None) or ""
    partes.append(f"salida parcial ({len(salida)} caracteres): {salida[:300]!r}")
    return ", ".join(partes)


def _codigo_http(error: Exception) -> int | None:
    """El SDK de Interactions usa `status_code`; el de generate_content, `code`."""
    codigo = getattr(error, "status_code", None) or getattr(error, "code", None)
    return codigo if isinstance(codigo, int) else None


def _es_cuota_agotada(error: Exception) -> bool:
    return _codigo_http(error) == 429 or "429" in str(error) or "RESOURCE_EXHAUSTED" in str(error)


def _es_transitorio(error: Exception) -> bool:
    """5xx o timeout. El timeout se reconoce por clase: `TimeoutError` de Python o las del
    SDK y de httpx (`APITimeoutError`, `TimeoutException`), sin importar sus módulos privados."""
    if _codigo_http(error) in CODIGOS_TRANSITORIOS:
        return True
    if isinstance(error, TimeoutError):
        return True
    return any(
        clase.__name__ in {"APITimeoutError", "TimeoutException"} for clase in type(error).__mro__
    )


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
