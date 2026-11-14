"""PDF → fragmentos en SQLite.

La fragmentación sigue la estructura de cada documento, no un tamaño fijo:
- manual: un fragmento por título del índice (los muy largos se parten en puntos y aparte);
- calendario: un fragmento por tabla ("Registro de materias", "Exámenes finales"...).

Uso:  python -m asistente.rag.ingesta
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from asistente.db import RUTA_DB, conectar, crear_esquema
from asistente.fuentes import FUENTES, Fuente
from asistente.rag.texto import aplastar, unir_lineas

_RE_INDICE = re.compile(r"^(?P<titulo>.*?)\s*\.{4,}\s*(?P<pagina>\d+)\s*$")
_RE_SECCION = re.compile(r"^SECCIÓN [IVXLC]+$")
_CABECERA_TABLA = "Actividad Desde Hasta"
_MAX_LINEAS_TITULO = 6
_LARGO_OBJETIVO = 900  # a partir de aquí se corta en el siguiente punto y aparte
_MINIMO_CUERPO = 40  # un título seguido de otro título no es un fragmento


@dataclass(frozen=True)
class Fragmento:
    seccion: str
    pagina: int
    texto: str
    contexto: str = ""


@dataclass
class Fragmentacion:
    fragmentos: list[Fragmento] = field(default_factory=list)
    titulos_en_indice: int = 0
    titulos_sin_encontrar: list[str] = field(default_factory=list)


def leer_paginas(ruta: Path) -> list[str]:
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # avisos de PDF mal formados
    return [pagina.extract_text() or "" for pagina in PdfReader(ruta).pages]


# ------------------------------------------------------------------ manual


def leer_indice(paginas: list[str]) -> tuple[list[tuple[str, int]], int]:
    """Entradas (título, página) del índice y posición de la primera página de contenido."""
    entradas: list[tuple[str, int]] = []
    fin = 0
    for i, pagina in enumerate(paginas):
        lineas = [linea.strip() for linea in pagina.splitlines()]
        if not any(_RE_INDICE.match(linea) for linea in lineas):
            if entradas:
                break
            continue
        fin = i + 1
        pendiente: list[str] = []  # títulos que ocupan más de una línea
        for linea in lineas:
            if not linea or linea.casefold() == "índice":
                continue
            coincide = _RE_INDICE.match(linea)
            if coincide:
                titulo = " ".join([*pendiente, coincide["titulo"]]).strip()
                entradas.append((titulo, int(coincide["pagina"])))
                pendiente = []
            else:
                pendiente.append(linea)
    return entradas, fin


def _lineas_del_cuerpo(paginas: list[str], inicio: int) -> list[tuple[int, str]]:
    """(página impresa, línea) de cada línea con texto, sin el número de página suelto."""
    salida: list[tuple[int, str]] = []
    for i in range(inicio, len(paginas)):
        lineas = [linea.strip() for linea in paginas[i].splitlines() if linea.strip()]
        numero = i + 1
        if lineas and lineas[0].isdigit():
            numero = int(lineas[0])
            lineas = lineas[1:]
        salida.extend((numero, linea) for linea in lineas)
    return salida


def _partir(lineas: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Parte un apartado largo en trozos que terminan en punto. Devuelve (página, texto)."""
    trozos: list[tuple[int, str]] = []
    actual: list[tuple[int, str]] = []
    largo = 0
    for pagina, linea in lineas:
        actual.append((pagina, linea))
        largo += len(linea) + 1
        if largo >= _LARGO_OBJETIVO and linea.endswith((".", ":")):
            trozos.append((actual[0][0], unir_lineas([texto for _, texto in actual])))
            actual, largo = [], 0
    if actual:
        resto = unir_lineas([texto for _, texto in actual])
        if trozos and len(resto) < _LARGO_OBJETIVO // 3:
            pagina, texto = trozos[-1]
            trozos[-1] = (pagina, f"{texto} {resto}")  # una cola corta no merece fragmento propio
        else:
            trozos.append((actual[0][0], resto))
    return trozos


def fragmentar_manual(paginas: list[str], titulo_documento: str = "") -> Fragmentacion:
    """Usa el índice como autoridad: un título solo cuenta si el índice lo lista en esa página."""
    indice, inicio = leer_indice(paginas)
    cuerpo = _lineas_del_cuerpo(paginas, inicio)
    resultado = Fragmentacion(titulos_en_indice=len(indice))

    # Posición de cada título dentro del cuerpo: (línea inicial, línea final, título).
    marcas: list[tuple[int, int, str]] = []
    cursor = 0
    for titulo, pagina in indice:
        objetivo = aplastar(titulo)
        hallado = None
        for i in range(cursor, len(cuerpo)):
            if cuerpo[i][0] > pagina + 1:
                break
            if cuerpo[i][0] < pagina - 1:
                continue
            junto = ""
            for n in range(1, _MAX_LINEAS_TITULO + 1):
                if i + n > len(cuerpo):
                    break
                junto += aplastar(cuerpo[i + n - 1][1])
                if junto == objetivo:
                    hallado = (i, i + n, titulo)
                    break
                if not objetivo.startswith(junto):
                    break
            if hallado:
                break
        if hallado is None:
            resultado.titulos_sin_encontrar.append(titulo)
            continue
        marcas.append(hallado)
        cursor = hallado[1]

    seccion_mayor = ""
    for k, (_, fin_titulo, titulo) in enumerate(marcas):
        if _RE_SECCION.match(titulo):
            siguiente = marcas[k + 1][2] if k + 1 < len(marcas) else ""
            seccion_mayor = f"{titulo}: {siguiente}".rstrip(": ")
        fin_cuerpo = marcas[k + 1][0] if k + 1 < len(marcas) else len(cuerpo)
        lineas = cuerpo[fin_titulo:fin_cuerpo]
        if sum(len(linea) for _, linea in lineas) < _MINIMO_CUERPO:
            continue
        contexto = " · ".join(parte for parte in (titulo_documento, seccion_mayor) if parte)
        for pagina, texto in _partir(lineas):
            resultado.fragmentos.append(Fragmento(titulo, pagina, f"{titulo}\n{texto}", contexto))
    return resultado


# ------------------------------------------------------------------ calendario


def fragmentar_calendario(paginas: list[str], titulo_documento: str = "") -> Fragmentacion:
    """Un fragmento por tabla. El título de la tabla es la línea anterior a su cabecera."""
    resultado = Fragmentacion()
    for numero, pagina in enumerate(paginas, start=1):
        lineas = [linea.strip() for linea in pagina.splitlines() if linea.strip()]
        inicios = [i - 1 for i, linea in enumerate(lineas) if linea == _CABECERA_TABLA and i > 0]
        resultado.titulos_en_indice += len(inicios)
        for k, inicio in enumerate(inicios):
            fin = inicios[k + 1] if k + 1 < len(inicios) else len(lineas)
            titulo = lineas[inicio]
            filas = [linea for linea in lineas[inicio + 2 : fin]]
            if filas:
                texto = "\n".join([titulo, *filas])
                resultado.fragmentos.append(Fragmento(titulo, numero, texto, titulo_documento))
    return resultado


# ------------------------------------------------------------------ base de datos


def guardar(
    con: sqlite3.Connection,
    titulo: str,
    url: str | None,
    sha256: str,
    fecha_consulta: str,
    fragmentos: list[Fragmento],
) -> int:
    """Reemplaza el documento (por título o por contenido) y sus fragmentos."""
    crear_esquema(con)
    with con:
        viejos = con.execute(
            "SELECT id FROM documentos WHERE titulo = ? OR sha256 = ?", (titulo, sha256)
        ).fetchall()
        for (viejo,) in viejos:
            # Borrado explícito: así los disparadores mantienen el índice FTS.
            con.execute("DELETE FROM fragmentos WHERE documento_id = ?", (viejo,))
            con.execute("DELETE FROM documentos WHERE id = ?", (viejo,))
        cursor = con.execute(
            "INSERT INTO documentos (titulo, fuente_url, fecha_consulta, sha256) "
            "VALUES (?, ?, ?, ?)",
            (titulo, url, fecha_consulta, sha256),
        )
        documento_id = cursor.lastrowid
        assert documento_id is not None
        con.executemany(
            "INSERT INTO fragmentos (documento_id, seccion, pagina, texto, contexto) "
            "VALUES (?, ?, ?, ?, ?)",
            [(documento_id, f.seccion, f.pagina, f.texto, f.contexto) for f in fragmentos],
        )
    return documento_id


def ingerir(con: sqlite3.Connection, fuente: Fuente) -> Fragmentacion:
    paginas = leer_paginas(fuente.ruta)
    if fuente.tipo == "manual":
        resultado = fragmentar_manual(paginas, fuente.titulo)
    elif fuente.tipo == "calendario":
        resultado = fragmentar_calendario(paginas, fuente.titulo)
    else:
        raise ValueError(f"{fuente.archivo}: un {fuente.tipo} no se fragmenta")
    sha = hashlib.sha256(fuente.ruta.read_bytes()).hexdigest()
    fecha = date.fromtimestamp(fuente.ruta.stat().st_mtime).isoformat()
    guardar(con, fuente.titulo, fuente.url, sha, fecha, resultado.fragmentos)
    return resultado


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asistente.rag.ingesta", description=__doc__)
    parser.add_argument("--db", type=Path, default=RUTA_DB)
    args = parser.parse_args(argv)

    con = conectar(args.db)
    faltan = 0
    try:
        for fuente in FUENTES:
            if fuente.tipo == "diagrama":
                continue
            if not fuente.ruta.exists():
                faltan += 1
                print(
                    f"FALTA  {fuente.archivo}: corre scripts/descargar_fuentes.py", file=sys.stderr
                )
                continue
            resultado = ingerir(con, fuente)
            largos = [len(f.texto) for f in resultado.fragmentos]
            print(
                f"ok     {fuente.archivo}: {len(largos)} fragmentos "
                f"(mediana {sorted(largos)[len(largos) // 2]} caracteres, máximo {max(largos)}); "
                f"títulos sin encontrar: {len(resultado.titulos_sin_encontrar)} "
                f"de {resultado.titulos_en_indice}"
            )
            for titulo in resultado.titulos_sin_encontrar:
                print(f"         - {titulo}")
    finally:
        con.close()
    return 1 if faltan else 0


if __name__ == "__main__":
    raise SystemExit(main())
