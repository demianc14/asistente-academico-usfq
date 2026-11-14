"""Modelos Pydantic de los archivos de entrada (malla, oferta e historial)."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)


def _normalizar_codigo(valor: object) -> object:
    """'cmp 4005' → 'CMP4005'. Deja pasar lo que no es texto para que Pydantic lo rechace."""
    if isinstance(valor, str):
        return "".join(valor.split()).upper()
    return valor


Codigo = Annotated[
    str,
    BeforeValidator(_normalizar_codigo),
    StringConstraints(pattern=r"^[A-Z]{2,4}\d{4}[A-Z]?$"),
]
Periodo = Annotated[str, StringConstraints(pattern=r"^\d{6}$")]
Hora = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
Dia = Literal["LU", "MA", "MI", "JU", "VI", "SA"]
Texto = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
IdCupo = Annotated[
    str,
    BeforeValidator(_normalizar_codigo),
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
