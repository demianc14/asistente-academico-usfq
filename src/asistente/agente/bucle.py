"""Bucle de tool use del planificador, detrás de `ClienteLLM`.

`ClienteLLM.completar` no guarda estado: en cada paso el LLM recibe la solicitud y el
historial completo en JSON y devuelve una sola acción. El código ejecuta las herramientas,
verifica cada propuesta y redacta todo lo que ve el usuario.

Garantía: un plan solo sale por `_mostrar`, que recibe el `Plan` que `verificar_plan` dio
por válido. El texto libre del LLM no se muestra nunca.

Lo que es restricción del usuario (periodo, límite de créditos, aprobados) viene en la
`Solicitud` y se pisa en cada propuesta: el LLM elige cursos y secciones, nada más.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from pydantic import TypeAdapter, ValidationError

from asistente.agente.modelos import (
    Abstenerse,
    Accion,
    LlamarHerramienta,
    MotivoAbstencion,
    Paso,
    PedidoFuera,
    Preguntar,
    Proponer,
    ResultadoAgente,
    Solicitud,
    TipoPregunta,
)
from asistente.llm import ClienteLLM
from asistente.planificador import (
    ConsultaHorarios,
    Plan,
    ResultadoVerificacion,
    ejecutar,
    horarios,
    verificar_plan,
)
from asistente.planificador.herramientas import hay_oferta
from asistente.planificador.modelos import CursoPlan, ErrorEntrada

MAX_PROPUESTAS = 3  # la inicial y dos correcciones
MAX_LLAMADAS = 10  # llamadas al LLM por solicitud, contando consultas y propuestas
MAX_INVALIDAS_SEGUIDAS = 3  # salidas ilegibles seguidas antes de rendirse (cuida la cuota)
MAX_CARACTERES_SALIDA = 20_000  # una acción cabe de sobra; más que eso ni se intenta leer

_ACCION: TypeAdapter[Accion] = TypeAdapter(Accion)

PREGUNTAS: dict[TipoPregunta, str] = {
    "falta_creditos_max": (
        "¿Cuál es el máximo de créditos por semestre que quieres llevar? "
        "No asumo ningún límite por mi cuenta."
    ),
    "falta_periodo": "¿Para qué periodo quieres el plan?",
    "restriccion_ambigua": (
        "Alguna de tus restricciones no me queda clara. ¿Puedes decirla de otra forma?"
    ),
}

ABSTENCIONES: dict[MotivoAbstencion, str] = {
    "sin_plan_posible": (
        "No encontré un plan que cumpla lo que pides con los cursos y horarios cargados."
    ),
    "fuera_de_alcance": "Eso queda fuera de lo que puedo hacer: solo armo planes de semestre.",
    "no_converge": ("No logré armar un plan que pase la verificación, así que no muestro ninguno."),
    "sin_pasos": "No llegué a un plan verificado en los pasos disponibles.",
    "salida_invalida": "No pude armar un plan esta vez. Vuelve a intentarlo.",
}

SISTEMA = """\
Armas planes de semestre para un estudiante. Tú propones; un programa verifica. No se muestra
ningún plan que no pase la verificación, y nada de lo que escribas llega al usuario tal cual.

Recibes un JSON con la solicitud, el historial de tus pasos con su resultado y cuántas
llamadas y correcciones te quedan. Devuelve únicamente un objeto JSON, una acción:

{"accion": "herramienta", "nombre": "...", "argumentos": {...}}
  buscar_curso        {"texto": "código o palabras del nombre", "limite": 10}
  prerrequisitos      {"curso": "ABC1234"}
  cursos_habilitados  {"aprobados": ["ABC1234"], "cursando": []}
  horarios            {"curso": "ABC1234", "periodo": "202610"}
{"accion": "proponer", "plan": {"semestres": [{"periodo": "202610",
  "cursos": [{"codigo": "ABC1234", "nrc": "1234"}]}]}}
{"accion": "preguntar", "pregunta": "restriccion_ambigua"}
{"accion": "abstenerse", "motivo": "sin_plan_posible" | "fuera_de_alcance"}

Reglas:
- Usa solo códigos de curso y NRC que hayan salido de una herramienta. No inventes ninguno.
- El periodo, el máximo de créditos y los cursos aprobados son los de la solicitud; no puedes
  cambiarlos. El plan lleva un solo semestre, el del periodo de la solicitud.
- "cursos_pedidos" son los cursos que el usuario pidió: inclúyelos si pasan la verificación.
  Los que no entren se le mostrarán al usuario con el motivo.
- Si la verificación rechaza el plan recibes todos los motivos: corrígelos todos a la vez.
- Las preferencias de la solicitud no se verifican: intenta cumplirlas, pero el usuario las
  verá marcadas como no verificadas. No son motivo para proponer un plan que no pase.
- Si un curso tiene secciones en el periodo, el NRC es obligatorio. Solo puede ir sin NRC un
  curso sin secciones cargadas, y entonces su horario queda sin verificar.
- Si no hay un plan posible, abstente. Abstenerse es una respuesta correcta; inventar no lo es.
"""


def _leer_accion(crudo: str) -> Accion | str:
    """La acción, o por qué no se pudo leer. El motivo solo vuelve al LLM."""
    if len(crudo) > MAX_CARACTERES_SALIDA:
        return f"la salida pasa de {MAX_CARACTERES_SALIDA} caracteres"
    texto = crudo.strip()
    if texto.startswith("```"):
        texto = texto.strip("`").removeprefix("json").strip()
    try:
        return _ACCION.validate_python(json.loads(texto))
    except json.JSONDecodeError as exc:
        return f"la salida no es JSON válido ({exc.msg})"
    except ValidationError as exc:
        return "el JSON no es una acción válida: " + "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
    except Exception as exc:  # noqa: BLE001
        # JSON demasiado anidado (RecursionError), un entero de miles de dígitos (ValueError)...
        # Lo que escriba el LLM nunca tumba el bucle: es una salida inválida más.
        return f"la salida no se pudo leer ({type(exc).__name__})"


def _mensaje(
    solicitud: Solicitud, historial: list[dict[str, Any]], llamadas: int, propuestas: int
) -> str:
    return json.dumps(
        {
            "solicitud": solicitud.model_dump(mode="json"),
            "historial": historial,
            "llamadas_restantes": MAX_LLAMADAS - llamadas,
            "propuestas_restantes": MAX_PROPUESTAS - propuestas,
        },
        ensure_ascii=False,
    )


def _nombres(con: sqlite3.Connection, plan: Plan) -> dict[str, tuple[str, int]]:
    codigos = [c.codigo for s in plan.semestres for c in s.cursos]
    marcas = ",".join("?" * len(codigos))
    return {
        f["codigo"]: (f["nombre"], int(f["creditos"]))
        for f in con.execute(
            f"SELECT codigo, nombre, creditos FROM cursos WHERE codigo IN ({marcas})", codigos
        )
    }


ALCANCE = "Solo se comprobaron prerrequisitos, créditos y choques de horario."
ERROR_DE_ENTRADA = "No puedo armar el plan: la solicitud trae datos que no están en la base."


def _errores_de_entrada(con: sqlite3.Connection, solicitud: Solicitud) -> tuple[ErrorEntrada, ...]:
    """Lo que la solicitud trae y la base no conoce. Se comprueba antes de llamar al LLM."""
    existentes = {f["codigo"] for f in con.execute("SELECT codigo FROM cursos")}
    errores = [
        ErrorEntrada(
            campo=f"{campo}.{i}", mensaje=f"el curso {codigo} no existe en la base", valor=codigo
        )
        for campo, codigos in (
            ("aprobados", solicitud.aprobados),
            ("cursos_pedidos", solicitud.cursos_pedidos),
        )
        for i, codigo in enumerate(codigos)
        if codigo not in existentes
    ]
    if solicitud.periodo is not None and not hay_oferta(con, solicitud.periodo):
        errores.append(
            ErrorEntrada(
                campo="periodo",
                mensaje=f"no hay oferta cargada para el periodo {solicitud.periodo}",
                valor=solicitud.periodo,
            )
        )
    return tuple(errores)


def _pedidos_fuera(
    con: sqlite3.Connection, solicitud: Solicitud, plan: Plan
) -> tuple[PedidoFuera, ...]:
    """Los cursos pedidos que el plan verificado no trae, cada uno con su motivo.

    El motivo sale de `verificar_plan`: se añade el curso al plan, con cada una de sus
    secciones, y se mira qué lo rechaza. Nada de esto lo escribe el LLM.
    """
    (semestre,) = plan.semestres  # verificado: un solo semestre, el del periodo pedido
    en_plan = {c.codigo for c in semestre.cursos}
    fuera: list[PedidoFuera] = []
    for codigo in dict.fromkeys(solicitud.cursos_pedidos):
        if codigo in en_plan:
            continue
        consulta = ConsultaHorarios(curso=codigo, periodo=semestre.periodo)
        nrcs: list[str | None] = [s.nrc for s in horarios(con, consulta).secciones] or [None]
        intentos = [
            verificar_plan(
                con,
                Plan(
                    aprobados=plan.aprobados,
                    creditos_max=plan.creditos_max,
                    periodo=plan.periodo,
                    semestres=(
                        semestre.model_copy(
                            update={"cursos": (*semestre.cursos, CursoPlan(codigo=codigo, nrc=nrc))}
                        ),
                    ),
                ),
            )
            for nrc in nrcs
        ]
        if any(intento.valido for intento in intentos):
            fuera.append(
                PedidoFuera(
                    codigo=codigo,
                    motivos=("no_propuesto",),
                    explicacion="habría pasado la verificación, pero la propuesta no lo incluyó.",
                )
            )
            continue
        # Lo que no depende de la sección (requisitos, créditos, ya aprobado) es igual en
        # todos los intentos. Si solo queda el horario, es que ninguna sección cabe.
        propios = [m for m in intentos[0].motivos if m.tipo != "choque_horario"]
        if propios:
            prefijo = f"{semestre.periodo}: "
            fuera.append(
                PedidoFuera(
                    codigo=codigo,
                    motivos=tuple(dict.fromkeys(m.tipo for m in propios)),
                    explicacion=" ".join(m.mensaje.removeprefix(prefijo) for m in propios),
                )
            )
        else:
            fuera.append(
                PedidoFuera(
                    codigo=codigo,
                    motivos=("choque_horario",),
                    explicacion="todas sus secciones chocan con los cursos del plan.",
                )
            )
    return tuple(fuera)


def _texto_del_plan(
    con: sqlite3.Connection,
    plan: Plan,
    verificacion: ResultadoVerificacion,
    preferencias: tuple[str, ...],
    pedidos_fuera: tuple[PedidoFuera, ...],
) -> str:
    """Códigos, NRC y periodos del plan verificado; nombres y créditos de la base.

    Los pedidos que no entraron y las preferencias van aparte y marcados: que el plan sea
    válido no dice que traiga lo que se pidió.
    """
    nombres = _nombres(con, plan)
    lineas = [verificacion.mensaje, ""]
    for semestre in plan.semestres:
        lineas.append(f"{semestre.periodo}:")
        for curso in semestre.cursos:
            nombre, creditos = nombres[curso.codigo]
            seccion = f"NRC {curso.nrc}" if curso.nrc is not None else "sin NRC"
            lineas.append(f"- {curso.codigo} {nombre} ({creditos} créditos), {seccion}")
    lineas.append("")
    if pedidos_fuera:
        lineas.append("Pedidos que no entraron en el plan:")
        lineas += [f"- {pedido.codigo}: {pedido.explicacion}" for pedido in pedidos_fuera]
    if preferencias:
        lineas.append("Preferencias no verificadas:")
        lineas += [f"- {preferencia}" for preferencia in preferencias]
    lineas.append(ALCANCE)
    return "\n".join(lineas)


def _inexistentes(
    nombre: str, argumentos: dict[str, Any], resultado: dict[str, Any]
) -> tuple[str, ...]:
    """Códigos bien formados que una consulta no encontró en la base."""
    if nombre == "prerrequisitos" and not resultado["encontrado"]:
        return (str(resultado["curso"]),)
    if nombre == "horarios" and not resultado["curso_existe"]:
        return (str(resultado["curso"]),)
    if nombre == "cursos_habilitados":
        return tuple(resultado["desconocidos"])
    return ()


def planificar(con: sqlite3.Connection, llm: ClienteLLM, solicitud: Solicitud) -> ResultadoAgente:
    """Como mucho `MAX_LLAMADAS` llamadas al LLM y `MAX_PROPUESTAS` planes verificados.

    Una solicitud con códigos que no existen, sin límite de créditos o sin periodo se
    resuelve aquí mismo, sin gastar ninguna llamada.
    """
    traza: list[Paso] = []
    historial: list[dict[str, Any]] = []
    llamadas = 0
    propuestas = 0

    def cerrar(
        *, pregunta: TipoPregunta | None = None, motivo: MotivoAbstencion | None = None
    ) -> ResultadoAgente:
        return ResultadoAgente(
            estado="pregunta" if pregunta is not None else "abstencion",
            texto=PREGUNTAS[pregunta]
            if pregunta is not None
            else ABSTENCIONES[motivo or "sin_pasos"],
            pregunta=pregunta,
            motivo=None if pregunta is not None else (motivo or "sin_pasos"),
            vueltas=max(propuestas - 1, 0),
            llamadas_llm=llamadas,
            traza=tuple(traza),
        )

    def _mostrar(plan: Plan, verificacion: ResultadoVerificacion) -> ResultadoAgente:
        """La única salida con plan. `ResultadoAgente` rechaza una verificación no válida."""
        pedidos_fuera = _pedidos_fuera(con, solicitud, plan)
        return ResultadoAgente(
            estado="plan",
            texto=_texto_del_plan(con, plan, verificacion, solicitud.preferencias, pedidos_fuera),
            preferencias_no_verificadas=solicitud.preferencias,
            pedidos_fuera=pedidos_fuera,
            plan=plan,
            verificacion=verificacion,
            horario_verificado=all(s.horario_verificado for s in verificacion.semestres),
            vueltas=propuestas - 1,
            llamadas_llm=llamadas,
            traza=tuple(traza),
        )

    errores = _errores_de_entrada(con, solicitud)
    if errores:
        return ResultadoAgente(
            estado="error_entrada",
            texto="\n".join([ERROR_DE_ENTRADA, *(f"- {e.campo}: {e.mensaje}." for e in errores)]),
            errores=errores,
            vueltas=0,
            llamadas_llm=0,
        )
    if solicitud.creditos_max is None:
        return cerrar(pregunta="falta_creditos_max")
    if solicitud.periodo is None:
        return cerrar(pregunta="falta_periodo")

    invalidas_seguidas = 0
    while llamadas < MAX_LLAMADAS:
        crudo = llm.completar(SISTEMA, _mensaje(solicitud, historial, llamadas, propuestas))
        llamadas += 1
        accion = _leer_accion(crudo)

        if isinstance(accion, str):
            traza.append(Paso(tipo="salida_invalida"))
            historial.append(
                {"accion": None, "observacion": {"error": "salida_invalida", "detalle": accion}}
            )
            invalidas_seguidas += 1
            if invalidas_seguidas == MAX_INVALIDAS_SEGUIDAS:
                return cerrar(motivo="salida_invalida")
            continue
        invalidas_seguidas = 0

        if isinstance(accion, Preguntar):
            traza.append(Paso(tipo="pregunta"))
            return cerrar(pregunta=accion.pregunta)
        if isinstance(accion, Abstenerse):
            traza.append(Paso(tipo="abstencion"))
            return cerrar(motivo=accion.motivo)

        es_propuesta = isinstance(accion, Proponer) or accion.nombre == "verificar_plan"
        if not es_propuesta:
            assert isinstance(accion, LlamarHerramienta)
            respuesta = ejecutar(con, accion.nombre, accion.argumentos)
            traza.append(
                Paso(
                    tipo="herramienta",
                    herramienta=accion.nombre,
                    ok=respuesta.ok,
                    inexistentes=_inexistentes(
                        accion.nombre, accion.argumentos, respuesta.resultado
                    )
                    if respuesta.resultado is not None
                    else (),
                )
            )
            historial.append(
                {
                    "accion": accion.model_dump(mode="json"),
                    "observacion": respuesta.model_dump(mode="json"),
                }
            )
            continue

        # --- propuesta: la verifica el código, con el periodo, el límite y los aprobados
        # de la solicitud
        crudo_plan = accion.plan if isinstance(accion, Proponer) else accion.argumentos
        argumentos = {
            **crudo_plan,
            "aprobados": list(solicitud.aprobados),
            "creditos_max": solicitud.creditos_max,
            "periodo": solicitud.periodo,
        }
        propuestas += 1
        restantes = MAX_PROPUESTAS - propuestas
        respuesta = ejecutar(con, "verificar_plan", argumentos)
        observacion: dict[str, Any]
        if respuesta.resultado is None:
            traza.append(Paso(tipo="propuesta", herramienta="verificar_plan", ok=False))
            observacion = {
                "valido": False,
                "errores": [e.model_dump(mode="json") for e in respuesta.errores],
            }
        else:
            verificacion = ResultadoVerificacion.model_validate(respuesta.resultado)
            traza.append(
                Paso(
                    tipo="propuesta",
                    herramienta="verificar_plan",
                    ok=verificacion.valido,
                    motivos=tuple(m.tipo for m in verificacion.motivos),
                    inexistentes=tuple(
                        codigo
                        for m in verificacion.motivos
                        if m.tipo == "curso_inexistente"
                        for codigo in m.cursos
                    ),
                )
            )
            if verificacion.valido:
                return _mostrar(Plan.model_validate(argumentos), verificacion)
            observacion = {
                "valido": False,
                "motivos": [m.model_dump(mode="json") for m in verificacion.motivos],
            }
        if restantes == 0:
            return cerrar(motivo="no_converge")
        observacion["correcciones_restantes"] = restantes
        historial.append(
            {"accion": {"accion": "proponer", "plan": crudo_plan}, "observacion": observacion}
        )

    return cerrar(motivo="sin_pasos")
