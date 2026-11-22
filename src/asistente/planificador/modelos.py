"""Entradas y salidas de las herramientas del planificador.

Todo código de curso pasa por `Codigo` (el mismo tipo del ETL): se normaliza y, si no tiene
forma de código, la entrada se rechaza antes de tocar la base.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from asistente.etl.modelos import Codigo, Dia, Hora, Periodo, Texto


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ------------------------------------------------------------------ buscar_curso


class ConsultaCurso(_Modelo):
    """Un código ("CMP 4005", "cmp-4005") o palabras del nombre, sin importar tildes."""

    texto: Texto
    limite: int = Field(default=10, ge=1, le=50)


class CursoInfo(_Modelo):
    codigo: str
    nombre: str
    creditos: int
    area: str
    semestre_sugerido: int | None  # None: el curso no está en la malla de la carrera
    tipo: Literal["obligatorio", "electiva"] | None


class ResultadoBusqueda(_Modelo):
    cursos: tuple[CursoInfo, ...]
    total: int  # coincidencias en la base; `cursos` trae como mucho `limite`


# ------------------------------------------------------------------ prerrequisitos


class ConsultaPrerrequisitos(_Modelo):
    curso: Codigo


class Requisito(_Modelo):
    codigo: str
    nombre: str
    tipo: Literal["pre", "co"]


class RequisitoEnCadena(_Modelo):
    codigo: str
    nombre: str
    profundidad: int  # 1 = requisito directo


class ResultadoPrerrequisitos(_Modelo):
    curso: str
    encontrado: bool  # False: el código no existe en la base
    directos: tuple[Requisito, ...]
    cadena: tuple[RequisitoEnCadena, ...]  # directos e indirectos, siguiendo `pre` y `co`


# ------------------------------------------------------------------ cursos_habilitados


class Historial(_Modelo):
    """Solo códigos. "Cursando" no cuenta como aprobado: ni habilita ni se vuelve a ofrecer."""

    aprobados: tuple[Codigo, ...] = ()
    cursando: tuple[Codigo, ...] = ()


class CursoHabilitado(_Modelo):
    codigo: str
    nombre: str
    creditos: int
    area: str
    correquisitos_pendientes: tuple[str, ...]  # hay que llevarlos en el mismo semestre


class ResultadoHabilitados(_Modelo):
    cursos: tuple[CursoHabilitado, ...]
    desconocidos: tuple[str, ...]  # códigos del historial que no existen en la base


# ------------------------------------------------------------------ horarios


class ConsultaHorarios(_Modelo):
    curso: Codigo
    periodo: Periodo


class BloqueHorario(_Modelo):
    dia: Dia
    inicio: Hora
    fin: Hora


class SeccionInfo(_Modelo):
    nrc: str
    bloques: tuple[BloqueHorario, ...]


class ResultadoHorarios(_Modelo):
    curso: str
    periodo: str
    curso_existe: bool
    secciones: tuple[SeccionInfo, ...]


# ------------------------------------------------------------------ verificar_plan


class CursoPlan(_Modelo):
    codigo: Codigo
    nrc: Texto | None = None  # solo puede faltar si el curso no tiene secciones en el periodo


class SemestrePlan(_Modelo):
    periodo: Periodo
    cursos: tuple[CursoPlan, ...] = Field(min_length=1)


class Plan(_Modelo):
    """Uno o más semestres en orden cronológico.

    `creditos_max` es el tope por semestre y no tiene valor por defecto a propósito: el
    límite real de la USFQ no está verificado, así que lo pone quien llama.

    `periodo` es el periodo que pidió el usuario. Si viene, todo semestre del plan tiene que
    ser de ese periodo y el periodo tiene que tener oferta cargada: así quien propone el plan
    no puede mover cursos a un periodo que nadie pidió. Sin él, el plan puede abarcar varios
    periodos (también futuros, sin oferta) y eso queda a cargo de quien llama.
    """

    aprobados: tuple[Codigo, ...] = ()
    creditos_max: int = Field(gt=0)
    periodo: Periodo | None = None
    semestres: tuple[SemestrePlan, ...] = Field(min_length=1)


TipoMotivo = Literal[
    "curso_inexistente",
    "curso_repetido",
    "ya_aprobado",
    "periodo_invalido",
    "periodo_distinto",
    "periodo_sin_oferta",
    "prerrequisito_faltante",
    "correquisito_faltante",
    "creditos_excedidos",
    "nrc_faltante",
    "seccion_inexistente",
    "choque_horario",
]


class Motivo(_Modelo):
    tipo: TipoMotivo
    periodo: str | None  # None: el motivo es de `aprobados`, no de un semestre
    cursos: tuple[str, ...]
    detalle: str  # el requisito que falta, "10 > 9", los bloques que chocan...
    mensaje: str


class ResumenSemestre(_Modelo):
    periodo: str
    creditos: int  # de los cursos que existen, sin repetidos ni ya aprobados
    horario_verificado: bool
    sin_nrc: tuple[str, ...]  # en un plan válido: cursos sin secciones cargadas en el periodo


class ResultadoVerificacion(_Modelo):
    valido: bool
    motivos: tuple[Motivo, ...]  # todos, no solo el primero
    semestres: tuple[ResumenSemestre, ...]
    mensaje: str  # texto para el usuario; avisa si algún horario quedó sin verificar


# ------------------------------------------------------------------ envoltorio


class ErrorEntrada(_Modelo):
    campo: str
    mensaje: str
    valor: str | None


class RespuestaHerramienta(_Modelo):
    """Lo que recibe quien llama una herramienta por nombre (el agente futuro).

    `ok=False` significa que la llamada estaba mal hecha, no que el plan sea inválido: un
    plan rechazado llega con `ok=True` y `resultado["valido"] == False`.
    """

    herramienta: str
    ok: bool
    resultado: dict[str, Any] | None
    errores: tuple[ErrorEntrada, ...]
