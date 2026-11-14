"""Pregunta → respuesta con citas verificadas, o abstención."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from asistente.llm import ClienteLLM
from asistente.rag.busqueda import Recuperado, buscar
from asistente.rag.verificar import Borrador, Cita, verificar

MENSAJE_ABSTENCION = "No está en los documentos cargados."
MAX_INTENTOS = 2  # el intento inicial y un reintento

SISTEMA = """\
Respondes preguntas de estudiantes usando SOLO los fragmentos de documentos que se te entregan.

Devuelve únicamente un objeto JSON con esta forma:
{"abstenerse": false, "respuesta": "...", "citas": [{"fragmento_id": 12, "texto": "..."}]}

Reglas:
- Cada cita copia un pasaje LITERAL del fragmento indicado: sin resumir, sin corregir, sin
  puntos suspensivos. Un programa comprueba que el texto exista tal cual.
- Toda cifra o fecha que aparezca en la respuesta tiene que estar dentro de alguna cita.
- No pongas marcas de referencia como [1] en la respuesta.
- Si los fragmentos no contienen la respuesta, devuelve {"abstenerse": true}. Abstenerse es una
  respuesta correcta; inventar no lo es.
"""


@dataclass(frozen=True)
class Respuesta:
    estado: Literal["respondida", "abstencion"]
    texto: str
    citas: tuple[Cita, ...]
    recuperados: tuple[Recuperado, ...]
    llamadas_llm: int
    # Por qué se abstuvo: sin_fragmentos, el_modelo_se_abstuvo o no_verificable.
    motivo: str | None = None
    problemas: tuple[str, ...] = ()


def _mensaje(pregunta: str, recuperados: Sequence[Recuperado]) -> str:
    partes = [f"Pregunta: {pregunta}", "", "Fragmentos:"]
    for r in recuperados:
        partes.append(f'<fragmento id="{r.id}" documento="{r.documento}" pagina="{r.pagina}">')
        partes.append(r.texto)
        partes.append("</fragmento>")
    return "\n".join(partes)


def _leer_borrador(crudo: str) -> Borrador | str:
    """El borrador, o el motivo por el que no se pudo leer."""
    texto = crudo.strip()
    if texto.startswith("```"):
        texto = texto.strip("`").removeprefix("json").strip()
    try:
        return Borrador.model_validate(json.loads(texto))
    except json.JSONDecodeError as exc:
        return f"la salida no es JSON válido ({exc.msg})"
    except ValidationError as exc:
        return "el JSON no tiene la forma pedida: " + "; ".join(e["msg"] for e in exc.errors())


def responder(con: sqlite3.Connection, llm: ClienteLLM, pregunta: str, k: int = 5) -> Respuesta:
    recuperados = tuple(buscar(con, pregunta, k))
    if not recuperados:
        return Respuesta("abstencion", MENSAJE_ABSTENCION, (), (), 0, "sin_fragmentos")

    mensaje = _mensaje(pregunta, recuperados)
    problemas: list[str] = []
    for intento in range(1, MAX_INTENTOS + 1):
        borrador = _leer_borrador(llm.completar(SISTEMA, mensaje))
        if isinstance(borrador, str):
            problemas = [borrador]
        elif borrador.abstenerse:
            return Respuesta(
                "abstencion", MENSAJE_ABSTENCION, (), recuperados, intento, "el_modelo_se_abstuvo"
            )
        else:
            problemas = verificar(borrador, recuperados)
            if not problemas:
                return Respuesta(
                    "respondida", borrador.respuesta, tuple(borrador.citas), recuperados, intento
                )
        mensaje = (
            _mensaje(pregunta, recuperados)
            + "\n\nTu respuesta anterior no pasó la verificación:\n"
            + "\n".join(f"- {p}" for p in problemas)
            + "\nCorrígela o abstente."
        )

    return Respuesta(
        "abstencion",
        MENSAJE_ABSTENCION,
        (),
        recuperados,
        MAX_INTENTOS,
        "no_verificable",
        tuple(problemas),
    )
