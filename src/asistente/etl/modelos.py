"""Modelos Pydantic de los archivos de entrada (malla, oferta e historial)."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

# Forma de código respaldada por las fuentes (ver docs/decisiones.md): tres letras y cuatro
# dígitos, como en la malla de bloques, con la "E" final que el manual usa para los cursos
# dictados en inglés. No es una definición oficial: otras carreras podrían usar otra.
# Dígitos ASCII a propósito: `\d` acepta cualquier dígito Unicode ("２０９９１０", "٢٠٩٩١٠"),
# y con eso dos cadenas distintas pasarían por el mismo periodo o código.
PATRON_CODIGO = r"^[A-Z]{3}[0-9]{4}E?$"
MENSAJE_CODIGO = (
    "código de curso mal formado: se esperan tres letras y cuatro dígitos, "
    "con una E final opcional (como ABC1234 o ABC1234E)"
)
_CODIGO_ESCRITO = re.compile(r"\s*([A-Za-z]{3})[ -]?([0-9]{4}[Ee]?)\s*")


def _normalizar_codigo(valor: object) -> object:
    """'cmp 4005' y 'CMP-4005' → 'CMP4005': un espacio o un guion entre letras y dígitos.

    Lo que no encaja se deja igual para que `_validar_codigo` lo rechace.
    """
    if isinstance(valor, str):
        coincidencia = _CODIGO_ESCRITO.fullmatch(valor)
        if coincidencia is not None:
            return "".join(coincidencia.groups()).upper()
    return valor


def _validar_codigo(valor: str) -> str:
    if re.fullmatch(PATRON_CODIGO, valor) is None:
        raise ValueError(MENSAJE_CODIGO)
    return valor


# Cc: control (saltos de línea, tabuladores); Cf: formato invisible (dirección del texto,
# ancho cero); Cs: sustitutos sueltos; Zl y Zp: separadores de línea y de párrafo.
_CATEGORIAS_PROHIBIDAS = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
MENSAJE_TEXTO = "el texto va en una sola línea, sin saltos ni caracteres de control"


def _una_linea(valor: str) -> str:
    """Un `Texto` se imprime tal cual: con un salto de línea podría imitar otra sección."""
    if any(unicodedata.category(c) in _CATEGORIAS_PROHIBIDAS for c in valor):
        raise ValueError(MENSAJE_TEXTO)
    return valor


def _sin_espacios(valor: object) -> object:
    if isinstance(valor, str):
        return "".join(valor.split()).upper()
    return valor


Codigo = Annotated[str, BeforeValidator(_normalizar_codigo), AfterValidator(_validar_codigo)]
Periodo = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
Hora = Annotated[str, StringConstraints(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Dia = Literal["LU", "MA", "MI", "JU", "VI", "SA"]
Texto = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    AfterValidator(_una_linea),
]
IdCupo = Annotated[
    str,
    BeforeValidator(_sin_espacios),
    StringConstraints(pattern=r"^[A-Z]{2,10}\d?$"),
]


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Curso(_Modelo):
    codigo: Codigo
    nombre: Texto
    creditos: int = Field(ge=0, le=12)  # 0: requisitos de graduación sin créditos (p. ej. Inglés)
    area: Texto
    semestre: int = Field(ge=1, le=12)
    tipo: Literal["obligatorio", "electiva"] = "obligatorio"
    pre: tuple[Codigo, ...] = ()
    co: tuple[Codigo, ...] = ()


class Cupo(_Modelo):
    """Casilla de la malla que no es un curso fijo: una optativa o electiva a elegir."""

    id: IdCupo
    nombre: Texto
    creditos: int = Field(gt=0, le=12)
    area: Texto
    semestre: int = Field(ge=1, le=12)


class Bloque(_Modelo):
    dia: Dia
    inicio: Hora
    fin: Hora

    @model_validator(mode="after")
    def _inicio_antes_de_fin(self) -> Self:
        if self.inicio >= self.fin:
            raise ValueError(f"el bloque termina ({self.fin}) antes de empezar ({self.inicio})")
        return self


class Seccion(_Modelo):
    curso: Codigo
    nrc: Texto
    periodo: Periodo
    horario: tuple[Bloque, ...] = Field(min_length=1)


class ArchivoMalla(_Modelo):
    carrera: Texto
    cursos: tuple[Curso, ...] = Field(min_length=1)
    cupos: tuple[Cupo, ...] = ()
    secciones: tuple[Seccion, ...] = ()


class RegistroHistorial(_Modelo):
    curso: Codigo
    periodo: Periodo
    estado: Literal["aprobado", "cursando", "reprobado"]


class ArchivoHistorial(_Modelo):
    historial: tuple[RegistroHistorial, ...] = ()


class FilaPrerrequisito(_Modelo):
    """Una fila de datos/malla_prerrequisitos.csv.

    `verificado` lo pone Demian a "si" tras comprobarla contra el catálogo del portal.
    Por defecto es "no", y el ETL no carga una tabla que tenga alguna fila sin verificar.
    """

    curso: Codigo
    requisito: Codigo
    tipo: Literal["pre", "co"]
    verificado: Literal["si", "no"] = "no"
