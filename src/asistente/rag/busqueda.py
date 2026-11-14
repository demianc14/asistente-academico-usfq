"""Recuperación BM25 sobre el índice FTS5."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

_RE_PALABRA = re.compile(r"[^\W_]+")
_VACIAS = frozenset(
    """
    a al algo ante aquel como con cual cuales cuando cuanto cuantos cuanta cuantas de del desde
    donde dos el ella ellos en entre era es esa ese eso esta este esto estos hay hasta la las le
    les lo los mas me mi mis muy ni no nos o otra otro para pero por porque puede puedo que quien
    se segun ser si sin sobre son su sus tambien te tiene tengo un una uno unos y ya debo hacer
    qué cuál cuándo cuánto cuántos cuánta cuántas dónde cómo quién más según también
    """.split()  # noqa: SIM905
)


@dataclass(frozen=True)
class Recuperado:
    id: int
    documento: str
    seccion: str
    pagina: int
    texto: str
    puntaje: float


_RE_ENCLITICO = re.compile(r"(.+[aei]r)(?:me|te|se|nos|lo|la|le|los|las|les)")


def _raiz(palabra: str) -> str:
    """Raíz que se busca como prefijo: sin plural y sin pronombre pegado al infinitivo.

    'matrículas' → 'matrícula'; 'retirarme' → 'retirar' (que encuentra 'retirarse').
    """
    enclitico = _RE_ENCLITICO.fullmatch(palabra)
    if enclitico:
        return enclitico[1]
    if len(palabra) > 5 and palabra.endswith("es"):
        return palabra[:-2]
    if len(palabra) > 4 and palabra.endswith("s"):
        return palabra[:-1]
    return palabra


def consulta_fts(pregunta: str) -> str:
    """Convierte una pregunta en una consulta FTS5: términos con contenido unidos por OR."""
    terminos: list[str] = []
    for palabra in _RE_PALABRA.findall(pregunta.casefold()):
        if palabra in _VACIAS or (len(palabra) < 3 and not palabra.isdigit()):
            continue
        termino = f'"{palabra}"' if palabra.isdigit() else f'"{_raiz(palabra)}"*'
        if termino not in terminos:
            terminos.append(termino)
    return " OR ".join(terminos)


def buscar(con: sqlite3.Connection, pregunta: str, k: int = 5) -> list[Recuperado]:
    """Los k fragmentos más relevantes. Lista vacía si la pregunta no tiene términos útiles."""
    consulta = consulta_fts(pregunta)
    if not consulta:
        return []
    filas = con.execute(
        """
        SELECT f.id, d.titulo, f.seccion, f.pagina, f.texto,
               bm25(fragmentos_fts, 1.0, 0.5) AS puntaje
        FROM fragmentos_fts
        JOIN fragmentos AS f ON f.id = fragmentos_fts.rowid
        JOIN documentos AS d ON d.id = f.documento_id
        WHERE fragmentos_fts MATCH ?
        ORDER BY puntaje
        LIMIT ?
        """,
        (consulta, k),
    ).fetchall()
    return [Recuperado(*fila) for fila in filas]
