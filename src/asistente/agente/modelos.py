"""Lo que entra al agente, lo que el LLM puede pedir y lo que sale.

Ninguna acción del LLM tiene un campo de texto libre que llegue al usuario: las preguntas y
las abstenciones son valores de un catálogo cerrado y el texto lo pone el código.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from asistente.etl.modelos import Codigo, Periodo, Texto
from asistente.planificador import Plan, ResultadoVerificacion
from asistente.planificador.modelos import ErrorEntrada


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ------------------------------------------------------------------ entrada


class Solicitud(_Modelo):
    """Todo lo que aquí es restricción lo pone quien llama (la interfaz), nunca el LLM.

    `creditos_max=None` significa que el usuario no dio un límite: el agente pregunta, no
    asume uno. El límite real de la USFQ no está verificado.

    `periodo=None` significa que no dijo para qué periodo: el agente pregunta. El plan solo
    puede ser de ese periodo y el periodo tiene que tener oferta cargada.

    `cursos_pedidos` son los cursos que el usuario pidió por código. No obligan al plan, pero
    el plan visible lista los que no entraron y por qué. Como `aprobados`, tienen que existir
    en la base: si no, la solicitud se devuelve con un error de entrada sin llamar al LLM.

    `preferencias` son los deseos del usuario que ningún código comprueba ("sin clases los
    viernes", "priorizar datos"). Las separa quien llama. No son requisitos: el plan visible
    las lista como no verificadas.
    """

    texto: Texto
    periodo: Periodo | None = None
    creditos_max: int | None = Field(default=None, gt=0)
    aprobados: tuple[Codigo, ...] = ()
    cursos_pedidos: tuple[Codigo, ...] = ()
    preferencias: tuple[Texto, ...] = ()


# ------------------------------------------------------------------ acciones del LLM

PreguntaDelLLM = Literal["restriccion_ambigua"]
AbstencionDelLLM = Literal["sin_plan_posible", "fuera_de_alcance"]

TipoPregunta = Literal[
    "falta_creditos_max",  # estas dos solo las emite el código
    "falta_periodo",
    "restriccion_ambigua",
]
MotivoAbstencion = Literal[
    "sin_plan_posible",
    "fuera_de_alcance",
    "no_converge",  # estos tres solo los emite el código
    "sin_pasos",
    "salida_invalida",
]


class LlamarHerramienta(_Modelo):
    accion: Literal["herramienta"]
    nombre: str
    argumentos: dict[str, Any] = Field(default_factory=dict)


class Proponer(_Modelo):
    accion: Literal["proponer"]
    # se valida al verificarlo; `aprobados`, `creditos_max` y `periodo` se pisan
    plan: dict[str, Any]


class Preguntar(_Modelo):
    accion: Literal["preguntar"]
    pregunta: PreguntaDelLLM


class Abstenerse(_Modelo):
    accion: Literal["abstenerse"]
    motivo: AbstencionDelLLM


Accion = Annotated[
    LlamarHerramienta | Proponer | Preguntar | Abstenerse, Field(discriminator="accion")
]


# ------------------------------------------------------------------ salida


class Paso(_Modelo):
    """Una llamada al LLM y lo que el código hizo con ella. Es traza para la evaluación.

    No es salida visible: `herramienta` e `inexistentes` repiten cadenas que escribió el LLM.
    """

    tipo: Literal["herramienta", "propuesta", "pregunta", "abstencion", "salida_invalida"]
    herramienta: str | None = None  # en una propuesta, siempre "verificar_plan"
    ok: bool | None = None  # herramienta: la llamada estaba bien hecha; propuesta: plan válido
    motivos: tuple[str, ...] = ()  # tipos de los motivos de rechazo de una propuesta
    inexistentes: tuple[str, ...] = ()  # códigos que el código atrapó porque no existen


class PedidoFuera(_Modelo):
    """Un curso de `cursos_pedidos` que no está en el plan mostrado, y por qué.

    El motivo lo saca el código: añade el curso al plan ya verificado y vuelve a pasar
    `verificar_plan`. `no_propuesto` significa que habría pasado y la propuesta no lo trajo.
    """

    codigo: str
    motivos: tuple[str, ...]  # tipos de motivo de `verificar_plan`, o ("no_propuesto",)
    explicacion: str


class ResultadoAgente(_Modelo):
    """Lo único que se enseña al usuario es `texto` y, si hay, `plan`."""

    estado: Literal["plan", "pregunta", "abstencion", "error_entrada"]
    texto: str
    plan: Plan | None = None
    verificacion: ResultadoVerificacion | None = None
    horario_verificado: bool | None = None
    preferencias_no_verificadas: tuple[str, ...] = ()  # las de la solicitud, tal cual
    pedidos_fuera: tuple[PedidoFuera, ...] = ()  # solo con plan
    errores: tuple[ErrorEntrada, ...] = ()  # solo con error_entrada: la solicitud está mal
    pregunta: TipoPregunta | None = None
    motivo: MotivoAbstencion | None = None
    vueltas: int  # correcciones usadas: propuestas hechas menos una
    llamadas_llm: int
    traza: tuple[Paso, ...] = ()

    @model_validator(mode="after")
    def _solo_planes_verificados(self) -> Self:
        hay_plan = self.plan is not None or self.verificacion is not None
        if self.estado == "plan":
            if self.plan is None or self.verificacion is None or not self.verificacion.valido:
                raise ValueError("un plan solo se muestra si pasó verificar_plan")
        elif hay_plan:
            raise ValueError("solo el estado 'plan' lleva un plan")
        if (self.estado == "pregunta") != (self.pregunta is not None):
            raise ValueError("la pregunta va con el estado 'pregunta' y solo con él")
        if (self.estado == "abstencion") != (self.motivo is not None):
            raise ValueError("el motivo va con el estado 'abstencion' y solo con él")
        if (self.estado == "error_entrada") != bool(self.errores):
            raise ValueError("los errores van con el estado 'error_entrada' y solo con él")
        if self.estado != "plan" and self.pedidos_fuera:
            raise ValueError("los pedidos que no entraron solo se listan junto a un plan")
        return self
