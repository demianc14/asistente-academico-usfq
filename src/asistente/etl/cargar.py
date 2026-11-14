"""Carga de la malla, la oferta y el historial en SQLite.

Fail-fast: se valida todo antes de escribir. Si algo no cuadra (por ejemplo, un
prerrequisito que apunta a un curso inexistente) se lanza `ErrorDeCarga` con la
lista completa de problemas y la base no se toca.
"""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from asistente.db import crear_esquema
from asistente.etl.modelos import ArchivoHistorial, ArchivoMalla, FilaPrerrequisito

M = TypeVar("M", bound=BaseModel)


class ErrorDeCarga(Exception):
    """Los datos de entrada no son válidos; `errores` trae todos los motivos."""

    def __init__(self, errores: list[str]) -> None:
        super().__init__("\n".join(errores))
        self.errores = errores


@dataclass(frozen=True)
class ReporteCalidad:
    carrera: str
    cursos: int
    creditos: int
    cupos: int
    creditos_cupos: int
    prerrequisitos: int
    correquisitos: int
    secciones: int
    registros_historial: int
    cursos_sin_seccion: tuple[str, ...]
    avisos: tuple[str, ...]

    def texto(self) -> str:
        lineas = [
            f"Carrera: {self.carrera}",
            f"Cursos: {self.cursos} ({self.creditos} créditos)",
            f"Cupos sin curso fijo: {self.cupos} ({self.creditos_cupos} créditos)",
            f"Requisitos: {self.prerrequisitos} pre, {self.correquisitos} co",
            f"Secciones: {self.secciones} ({len(self.cursos_sin_seccion)} cursos sin oferta)",
            f"Historial: {self.registros_historial} registros",
            f"Avisos: {len(self.avisos)}",
        ]
        lineas += [f"  - {aviso}" for aviso in self.avisos]
        return "\n".join(lineas)


# ------------------------------------------------------------------ lectura


def _leer(ruta: Path, modelo: type[M]) -> M:
    try:
        crudo = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ErrorDeCarga([f"{ruta.name}: no se pudo leer: {exc}"]) from exc
    try:
        return modelo.model_validate(crudo)
    except ValidationError as exc:
        errores = [
            f"{ruta.name}: {'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        raise ErrorDeCarga(errores) from exc


def leer_malla(ruta: Path) -> ArchivoMalla:
    return _leer(ruta, ArchivoMalla)


def leer_historial(ruta: Path) -> ArchivoHistorial:
    return _leer(ruta, ArchivoHistorial)


COLUMNAS_PRERREQUISITOS = ("curso", "requisito", "tipo", "verificado")


def leer_prerrequisitos(ruta: Path) -> list[FilaPrerrequisito]:
    """Lee el CSV de prerrequisitos. Fail-fast: una sola fila sin verificar detiene la carga.

    Cargar solo las verificadas sería peor que no cargar ninguna: el planificador daría por
    válido un plan al que le falta un prerrequisito que nadie confirmó todavía.
    """
    try:
        with ruta.open(encoding="utf-8", newline="") as archivo:
            lector = csv.DictReader(archivo)
            columnas = tuple(lector.fieldnames or ())
            crudas = list(lector)
    except OSError as exc:
        raise ErrorDeCarga([f"{ruta.name}: no se pudo leer: {exc}"]) from exc
    if columnas != COLUMNAS_PRERREQUISITOS:
        raise ErrorDeCarga(
            [f"{ruta.name}: columnas {columnas}; se esperan {COLUMNAS_PRERREQUISITOS}"]
        )

    filas: list[FilaPrerrequisito] = []
    errores: list[str] = []
    for numero, cruda in enumerate(crudas, start=2):  # la línea 1 es el encabezado
        datos = {k: (v or "").strip() for k, v in cruda.items()}
        if not datos["verificado"]:
            del datos["verificado"]  # vacío = el valor por defecto, "no"
        try:
            fila = FilaPrerrequisito.model_validate(datos)
        except ValidationError as exc:
            errores += [f"{ruta.name}:{numero}: {e['loc'][0]}: {e['msg']}" for e in exc.errors()]
            continue
        if fila.verificado != "si":
            errores.append(
                f"{ruta.name}:{numero}: {fila.requisito} → {fila.curso} ({fila.tipo}) "
                "no está verificado"
            )
        filas.append(fila)
    if errores:
        raise ErrorDeCarga(errores)
    return filas


def aplicar_prerrequisitos(malla: ArchivoMalla, filas: list[FilaPrerrequisito]) -> ArchivoMalla:
    """Añade los requisitos del CSV a los cursos de la malla. `validar` revisa el resultado."""
    por_curso = {c.codigo: c for c in malla.cursos}
    errores = [
        f"prerrequisitos: el curso {f.curso} no existe en la malla"
        for f in filas
        if f.curso not in por_curso
    ]
    if errores:
        raise ErrorDeCarga(errores)
    cursos = tuple(
        c.model_copy(
            update={
                "pre": (
                    *c.pre,
                    *(f.requisito for f in filas if f.curso == c.codigo and f.tipo == "pre"),
                ),
                "co": (
                    *c.co,
                    *(f.requisito for f in filas if f.curso == c.codigo and f.tipo == "co"),
                ),
            }
        )
        for c in malla.cursos
    )
    return malla.model_copy(update={"cursos": cursos})


# ------------------------------------------------------------------ validación cruzada


def _repetidos(valores: list[str]) -> list[str]:
    return sorted(v for v, n in Counter(valores).items() if n > 1)


def _ciclo(aristas: dict[str, tuple[str, ...]]) -> list[str] | None:
    """Devuelve un ciclo de prerrequisitos (A → B → ... → A) si existe."""
    blanco, gris, negro = 0, 1, 2
    color = dict.fromkeys(aristas, blanco)
    for origen in aristas:
        if color[origen] != blanco:
            continue
        camino = [origen]
        pila = [iter(aristas[origen])]
        color[origen] = gris
        while pila:
            siguiente = next(pila[-1], None)
            if siguiente is None:
                color[camino.pop()] = negro
                pila.pop()
            elif color.get(siguiente, negro) == gris:
                return [*camino[camino.index(siguiente) :], siguiente]
            elif color.get(siguiente, negro) == blanco:
                color[siguiente] = gris
                camino.append(siguiente)
                pila.append(iter(aristas[siguiente]))
    return None


def validar(malla: ArchivoMalla, historial: ArchivoHistorial | None = None) -> list[str]:
    """Comprobaciones entre registros. Lista vacía = se puede cargar."""
    errores: list[str] = []
    codigos = {c.codigo for c in malla.cursos}

    for codigo in _repetidos([c.codigo for c in malla.cursos]):
        errores.append(f"curso duplicado: {codigo}")
    for cupo in _repetidos([c.id for c in malla.cupos]):
        errores.append(f"cupo duplicado: {cupo}")

    for curso in malla.cursos:
        requisitos = [*curso.pre, *curso.co]
        for requisito in requisitos:
            if requisito == curso.codigo:
                errores.append(f"{curso.codigo}: es requisito de sí mismo")
            elif requisito not in codigos:
                errores.append(f"{curso.codigo}: el requisito {requisito} no existe en la malla")
        for requisito in _repetidos(requisitos):
            errores.append(f"{curso.codigo}: el requisito {requisito} está repetido")

    ciclo = _ciclo({c.codigo: c.pre for c in malla.cursos})
    if ciclo is not None:
        errores.append("ciclo de prerrequisitos: " + " → ".join(ciclo))

    for seccion in malla.secciones:
        if seccion.curso not in codigos:
            errores.append(f"sección NRC {seccion.nrc}: el curso {seccion.curso} no existe")
        for bloque in _repetidos([f"{b.dia} {b.inicio}" for b in seccion.horario]):
            errores.append(f"sección NRC {seccion.nrc}: bloque repetido ({bloque})")
    for clave in _repetidos([f"{s.nrc} en {s.periodo}" for s in malla.secciones]):
        errores.append(f"sección duplicada: NRC {clave}")

    if historial is not None:
        for registro in historial.historial:
            if registro.curso not in codigos:
                errores.append(f"historial: el curso {registro.curso} no existe en la malla")
        for clave in _repetidos([f"{r.curso} en {r.periodo}" for r in historial.historial]):
            errores.append(f"historial: registro duplicado ({clave})")

    return errores


def _avisos(malla: ArchivoMalla) -> list[str]:
    """Cosas sospechosas que no impiden cargar."""
    semestre = {c.codigo: c.semestre for c in malla.cursos}
    avisos: list[str] = []
    for curso in malla.cursos:
        for requisito in curso.pre:
            if semestre[requisito] >= curso.semestre:
                avisos.append(
                    f"{curso.codigo} (sem. {curso.semestre}) tiene como prerrequisito a "
                    f"{requisito} (sem. {semestre[requisito]}), que no va antes"
                )
        for requisito in curso.co:
            if semestre[requisito] > curso.semestre:
                avisos.append(
                    f"{curso.codigo} (sem. {curso.semestre}) tiene como correquisito a "
                    f"{requisito} (sem. {semestre[requisito]}), que va después"
                )
    return avisos


# ------------------------------------------------------------------ carga

_TABLAS_RECARGADAS = (
    "historial_estudiante",
    "horarios",
    "secciones",
    "malla",
    "cupos",
    "prerrequisitos",
    "cursos",
)


def cargar(
    con: sqlite3.Connection,
    malla: ArchivoMalla,
    historial: ArchivoHistorial | None = None,
) -> ReporteCalidad:
    """Reemplaza malla, oferta e historial en una sola transacción.

    No toca `documentos`, `fragmentos` ni `consultas`. Sin `historial`, la tabla
    de historial queda vacía.
    """
    errores = validar(malla, historial)
    if errores:
        raise ErrorDeCarga(errores)

    crear_esquema(con)
    registros = historial.historial if historial is not None else ()

    with con:
        for tabla in _TABLAS_RECARGADAS:
            con.execute(f"DELETE FROM {tabla}")
        con.executemany(
            "INSERT INTO cursos (codigo, nombre, creditos, area) VALUES (?, ?, ?, ?)",
            [(c.codigo, c.nombre, c.creditos, c.area) for c in malla.cursos],
        )
        con.executemany(
            "INSERT INTO prerrequisitos (curso, requisito, tipo) VALUES (?, ?, ?)",
            [(c.codigo, r, "pre") for c in malla.cursos for r in c.pre]
            + [(c.codigo, r, "co") for c in malla.cursos for r in c.co],
        )
        con.executemany(
            "INSERT INTO malla (carrera, curso, semestre_sugerido, tipo) VALUES (?, ?, ?, ?)",
            [(malla.carrera, c.codigo, c.semestre, c.tipo) for c in malla.cursos],
        )
        con.executemany(
            "INSERT INTO cupos (carrera, id, nombre, creditos, area, semestre_sugerido) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(malla.carrera, c.id, c.nombre, c.creditos, c.area, c.semestre) for c in malla.cupos],
        )
        for seccion in malla.secciones:
            cursor = con.execute(
                "INSERT INTO secciones (curso, nrc, periodo) VALUES (?, ?, ?)",
                (seccion.curso, seccion.nrc, seccion.periodo),
            )
            con.executemany(
                "INSERT INTO horarios (seccion_id, dia, inicio, fin) VALUES (?, ?, ?, ?)",
                [(cursor.lastrowid, b.dia, b.inicio, b.fin) for b in seccion.horario],
            )
        con.executemany(
            "INSERT INTO historial_estudiante (curso, periodo, estado) VALUES (?, ?, ?)",
            [(r.curso, r.periodo, r.estado) for r in registros],
        )

    con_oferta = {s.curso for s in malla.secciones}
    return ReporteCalidad(
        carrera=malla.carrera,
        cursos=len(malla.cursos),
        creditos=sum(c.creditos for c in malla.cursos),
        cupos=len(malla.cupos),
        creditos_cupos=sum(c.creditos for c in malla.cupos),
        prerrequisitos=sum(len(c.pre) for c in malla.cursos),
        correquisitos=sum(len(c.co) for c in malla.cursos),
        secciones=len(malla.secciones),
        registros_historial=len(registros),
        cursos_sin_seccion=tuple(
            sorted(c.codigo for c in malla.cursos if c.codigo not in con_oferta)
        ),
        avisos=tuple(_avisos(malla)),
    )
