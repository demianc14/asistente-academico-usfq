"""Utilidades de texto compartidas por la ingesta y la verificación de citas."""

from __future__ import annotations

import re
import unicodedata

_COMILLAS = str.maketrans({"“": '"', "”": '"', "«": '"', "»": '"', "‘": "'", "’": "'"})
_GUIONES = str.maketrans({"–": "-", "—": "-", "‐": "-", "‑": "-", "­": ""})
_RE_ESPACIOS = re.compile(r"\s+")
_RE_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_RE_CORTE = re.compile(r"[^\W\d_]-$")  # letra seguida de guion al final de la línea


def normalizar(texto: str) -> str:
    """Forma canónica para comparar citas: sin mayúsculas, espacios ni tipografía variable.

    Conserva tildes, puntuación y dígitos: una cita tiene que coincidir en todo eso.
    """
    texto = unicodedata.normalize("NFKC", texto).translate(_COMILLAS).translate(_GUIONES)
    return _RE_ESPACIOS.sub(" ", texto).strip().casefold()


def aplastar(texto: str) -> str:
    """Solo letras y dígitos en mayúsculas. Sirve para emparejar títulos partidos en líneas."""
    return "".join(c for c in unicodedata.normalize("NFKC", texto).upper() if c.isalnum())


def unir_lineas(lineas: list[str]) -> str:
    """Une líneas de un PDF en un párrafo, reuniendo palabras cortadas con guion.

    'es-' + 'tudios' → 'estudios'. Una palabra compuesta que justo cae al final de
    la línea pierde su guion; se acepta porque las citas se verifican contra este
    mismo texto, no contra el PDF.
    """
    salida = ""
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        if _RE_CORTE.search(salida) and linea[0].isalpha():
            salida = salida[:-1] + linea
        elif salida:
            salida += " " + linea
        else:
            salida = linea
    return _RE_ESPACIOS.sub(" ", salida)


def numeros(texto: str) -> set[str]:
    """Cifras que aparecen en un texto: '4 al 18 de noviembre de 2026' → {'4', '18', '2026'}."""
    return set(_RE_NUMERO.findall(texto))
