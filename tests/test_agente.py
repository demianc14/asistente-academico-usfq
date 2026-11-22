"""Bucle del agente planificador, con un cliente de guion. Ninguna llamada a un LLM real.

Estos guiones son de desarrollo. Los 15 escenarios de evaluación viven en
`evals/escenarios_agente.yaml` y ningún test de este archivo los carga.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
from pydantic import ValidationError

from asistente.agente import (
    MAX_LLAMADAS,
    ResultadoAgente,
    Solicitud,
    planificar,
)
from asistente.llm import ClienteDeGuion
from asistente.planificador import verificar_plan

APROBADOS = ("EJM1001", "EJC1001", "EJG1001", "EJC1002", "EJC1003")
CANARIO = "CANARIO-7f3a9c-no-debe-verse"

# Un semestre válido con secciones que no chocan (7 créditos).
VALIDO = [
    {
        "periodo": "209910",
        "cursos": [{"codigo": "EJM1002", "nrc": "1002"}, {"codigo": "EJM2001", "nrc": "1004"}],
    }
]
# Las secciones 1001 y 1003 chocan los lunes.
CON_CHOQUE = [
    {
        "periodo": "209910",
        "cursos": [{"codigo": "EJM1002", "nrc": "1001"}, {"codigo": "EJM2001", "nrc": "1003"}],
    }
]
# EJC2001 no tiene secciones cargadas en 209910: es el único caso en que puede ir sin NRC.
SIN_NRC = [
    {"periodo": "209910", "cursos": [{"codigo": "EJC2001"}, {"codigo": "EJM2001", "nrc": "1004"}]}
]
# EJC2002 exige EJC2001 y EJM2001, que no están aprobados.
SIN_PRERREQUISITO = [{"periodo": "209910", "cursos": [{"codigo": "EJC2002", "nrc": "1005"}]}]
INVENTADO = [
    {"periodo": "209910", "cursos": [{"codigo": "EJX9999"}, {"codigo": "EJM1002", "nrc": "1002"}]}
]


def proponer(semestres: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"accion": "proponer", "plan": {"semestres": semestres, **extra}}


def herramienta(nombre: str, **argumentos: Any) -> dict[str, Any]:
    return {"accion": "herramienta", "nombre": nombre, "argumentos": argumentos}


def guion(*respuestas: dict[str, Any] | str) -> ClienteDeGuion:
    return ClienteDeGuion(r if isinstance(r, str) else json.dumps(r) for r in respuestas)


def solicitud(
    creditos_max: int | None = 18,
    aprobados: tuple[str, ...] = APROBADOS,
    periodo: str | None = "209910",
    cursos_pedidos: tuple[str, ...] = (),
) -> Solicitud:
    return Solicitud(
        texto="Quiero un plan para 209910",
        periodo=periodo,
        creditos_max=creditos_max,
        aprobados=aprobados,
        cursos_pedidos=cursos_pedidos,
    )


def visible(resultado: ResultadoAgente) -> str:
    """Todo lo que se le enseña al usuario: el texto y el plan."""
    plan = "" if resultado.plan is None else resultado.plan.model_dump_json()
    return resultado.texto + plan


def enviado(llm: ClienteDeGuion, llamada: int) -> dict[str, Any]:
    """El mensaje de usuario de una llamada (0 = la primera), ya leído como JSON."""
    contenido: dict[str, Any] = json.loads(llm.llamadas[llamada][1])
    return contenido


# ------------------------------------------------------------------ plan válido


def test_plan_valido_a_la_primera(con: sqlite3.Connection) -> None:
    llm = guion(proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 0
    assert resultado.llamadas_llm == 1
    assert resultado.horario_verificado is True
    assert "EJM1002" in resultado.texto
    assert "Cálculo de ejemplo II" in resultado.texto  # el nombre sale de la base
    assert "NRC 1002" in resultado.texto
    assert "horario verificado" in resultado.texto


def test_herramientas_antes_de_proponer(con: sqlite3.Connection) -> None:
    llm = guion(
        herramienta("cursos_habilitados", aprobados=list(APROBADOS)),
        herramienta("horarios", curso="EJM1002", periodo="209910"),
        proponer(VALIDO),
    )
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.llamadas_llm == 3
    assert [(p.tipo, p.herramienta, p.ok) for p in resultado.traza] == [
        ("herramienta", "cursos_habilitados", True),
        ("herramienta", "horarios", True),
        ("propuesta", "verificar_plan", True),
    ]
    # El resultado de cada herramienta le llega al LLM en la llamada siguiente.
    historial = enviado(llm, 2)["historial"]
    assert historial[1]["observacion"]["resultado"]["secciones"][0]["nrc"] == "1001"


def test_valido_tras_una_correccion_recibe_los_motivos(con: sqlite3.Connection) -> None:
    llm = guion(proponer(CON_CHOQUE), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 1
    observacion = enviado(llm, 1)["historial"][0]["observacion"]
    assert observacion["valido"] is False
    assert observacion["correcciones_restantes"] == 2  # falló la primera de tres propuestas
    (motivo,) = observacion["motivos"]
    assert motivo["tipo"] == "choque_horario"
    assert motivo["cursos"] == ["EJM1002", "EJM2001"]
    assert set(motivo) == {"tipo", "periodo", "cursos", "detalle", "mensaje"}


def test_valido_tras_dos_correcciones(con: sqlite3.Connection) -> None:
    llm = guion(proponer(CON_CHOQUE), proponer(SIN_PRERREQUISITO), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 2
    assert enviado(llm, 2)["historial"][1]["observacion"]["correcciones_restantes"] == 1


def test_recibe_todos_los_motivos_no_solo_el_primero(con: sqlite3.Connection) -> None:
    # Con tope 6 ninguna propuesta pasa: aquí solo importa lo que recibe el LLM.
    llm = guion(proponer(CON_CHOQUE), proponer(VALIDO), proponer(VALIDO))
    planificar(con, llm, solicitud(creditos_max=6))
    tipos = [m["tipo"] for m in enviado(llm, 1)["historial"][0]["observacion"]["motivos"]]
    assert tipos == ["creditos_excedidos", "choque_horario"]


# ------------------------------------------------------------------ no converge


def test_no_converge_se_abstiene_y_no_muestra_el_plan(con: sqlite3.Connection) -> None:
    llm = guion(proponer(CON_CHOQUE), proponer(CON_CHOQUE), proponer(CON_CHOQUE), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "abstencion"
    assert resultado.motivo == "no_converge"
    assert resultado.plan is None
    assert resultado.vueltas == 2
    assert len(llm.llamadas) == 3  # la cuarta respuesta del guion nunca se pide
    assert "EJM1002" not in resultado.texto
    assert "1001" not in resultado.texto


def test_tope_de_llamadas(con: sqlite3.Connection) -> None:
    llm = guion(*[herramienta("buscar_curso", texto="ejemplo")] * (MAX_LLAMADAS + 1))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "abstencion"
    assert resultado.motivo == "sin_pasos"
    assert len(llm.llamadas) == MAX_LLAMADAS


def test_tres_salidas_invalidas_seguidas(con: sqlite3.Connection) -> None:
    llm = guion("no soy JSON", "[1, 2]", '{"accion": "bailar"}', proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "abstencion"
    assert resultado.motivo == "salida_invalida"
    assert len(llm.llamadas) == 3


def test_una_salida_invalida_no_gasta_vuelta(con: sqlite3.Connection) -> None:
    llm = guion("no soy JSON", "```json\n" + json.dumps(proponer(VALIDO)) + "\n```")
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 0
    assert enviado(llm, 1)["historial"][0]["observacion"]["error"] == "salida_invalida"


# ------------------------------------------------------------------ entradas malas


def test_curso_inventado_lo_atrapa_verificar_plan(con: sqlite3.Connection) -> None:
    llm = guion(proponer(INVENTADO), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 1
    assert resultado.traza[0].inexistentes == ("EJX9999",)
    assert "EJX9999" not in visible(resultado)


def test_curso_inventado_en_una_consulta(con: sqlite3.Connection) -> None:
    llm = guion(herramienta("prerrequisitos", curso="EJX9999"), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.traza[0].inexistentes == ("EJX9999",)
    assert resultado.vueltas == 0


def test_codigo_mal_formado_en_la_propuesta_gasta_una_vuelta(con: sqlite3.Connection) -> None:
    mal = [{"periodo": "209910", "cursos": [{"codigo": "calculo dos"}]}]
    llm = guion(proponer(mal), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 1
    assert resultado.traza[0].ok is False
    observacion = enviado(llm, 1)["historial"][0]["observacion"]
    assert observacion["errores"][0]["campo"] == "semestres.0.cursos.0.codigo"
    assert observacion["correcciones_restantes"] == 2


def test_codigo_mal_formado_en_una_consulta_no_gasta_vuelta(con: sqlite3.Connection) -> None:
    llm = guion(herramienta("prerrequisitos", curso="calculo dos"), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.vueltas == 0
    assert resultado.traza[0].ok is False
    assert enviado(llm, 1)["historial"][0]["observacion"]["ok"] is False


def test_herramienta_inexistente(con: sqlite3.Connection) -> None:
    llm = guion(herramienta("inscribir_curso", curso="EJM1002"), proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.vueltas == 0
    assert (resultado.traza[0].herramienta, resultado.traza[0].ok) == ("inscribir_curso", False)
    error = enviado(llm, 1)["historial"][0]["observacion"]["errores"][0]
    assert "no existe la herramienta" in error["mensaje"]


def test_verificar_plan_como_herramienta_cuenta_como_propuesta(con: sqlite3.Connection) -> None:
    llamada = herramienta("verificar_plan", semestres=CON_CHOQUE, creditos_max=99)
    llm = guion(llamada, llamada, llamada)
    resultado = planificar(con, llm, solicitud())
    assert resultado.motivo == "no_converge"
    assert [p.tipo for p in resultado.traza] == ["propuesta"] * 3


# ------------------------------------------------------------------ créditos y aprobados


def test_sin_creditos_max_pregunta_sin_llamar_al_llm(con: sqlite3.Connection) -> None:
    llm = guion()  # si se le llamara, el guion vacío lanzaría
    resultado = planificar(con, llm, solicitud(creditos_max=None))
    assert resultado.estado == "pregunta"
    assert resultado.pregunta == "falta_creditos_max"
    assert resultado.llamadas_llm == 0
    assert resultado.plan is None
    assert not any(c.isdigit() for c in resultado.texto)  # no sugiere ningún límite


def test_el_plan_lleva_los_creditos_y_aprobados_de_la_solicitud(con: sqlite3.Connection) -> None:
    llm = guion(proponer(VALIDO, creditos_max=99, aprobados=["EJC2001", "EJM2001"]))
    resultado = planificar(con, llm, solicitud(creditos_max=7))
    assert resultado.estado == "plan"
    assert resultado.plan is not None
    assert resultado.plan.creditos_max == 7
    assert resultado.plan.aprobados == APROBADOS
    assert resultado.plan.periodo == "209910"


def test_aprobados_inventados_no_saltan_un_prerrequisito(con: sqlite3.Connection) -> None:
    trampa = proponer(SIN_PRERREQUISITO, aprobados=[*APROBADOS, "EJC2001", "EJM2001"])
    llm = guion(trampa, trampa, trampa)
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "abstencion"
    tipos = [m["tipo"] for m in enviado(llm, 1)["historial"][0]["observacion"]["motivos"]]
    assert tipos == ["prerrequisito_faltante", "prerrequisito_faltante"]


def test_creditos_inflados_no_saltan_el_limite(con: sqlite3.Connection) -> None:
    trampa = proponer(VALIDO, creditos_max=99)
    llm = guion(trampa, trampa, trampa)
    resultado = planificar(con, llm, solicitud(creditos_max=6))
    assert resultado.estado == "abstencion"
    assert resultado.motivo == "no_converge"


# ------------------------------------------------------------------ sin NRC


def test_plan_sin_nrc_se_muestra_con_horario_sin_verificar(con: sqlite3.Connection) -> None:
    resultado = planificar(con, guion(proponer(SIN_NRC)), solicitud())
    assert resultado.estado == "plan"
    assert resultado.horario_verificado is False
    assert "horario sin verificar" in resultado.texto
    assert "sin NRC" in resultado.texto


# ------------------------------------------------------------------ preferencias


def test_las_preferencias_salen_como_no_verificadas(con: sqlite3.Connection) -> None:
    pedido = Solicitud(
        texto="Cálculo II sin clases los viernes",
        periodo="209910",
        creditos_max=18,
        aprobados=APROBADOS,
        preferencias=("sin clases los viernes", "priorizar datos"),
    )
    # La sección 1002 sí tiene clase el viernes: el plan pasa igual, porque nadie lo comprueba.
    resultado = planificar(con, guion(proponer(VALIDO)), pedido)
    assert resultado.estado == "plan"
    assert resultado.preferencias_no_verificadas == ("sin clases los viernes", "priorizar datos")
    _, _, despues = resultado.texto.partition("Preferencias no verificadas:")
    assert "- sin clases los viernes" in despues
    assert "- priorizar datos" in despues
    assert "Solo se comprobaron prerrequisitos, créditos y choques de horario." in despues


def test_sin_preferencias_igual_se_dice_que_se_comprobo(con: sqlite3.Connection) -> None:
    resultado = planificar(con, guion(proponer(VALIDO)), solicitud())
    assert "Preferencias no verificadas" not in resultado.texto
    assert "Solo se comprobaron prerrequisitos, créditos y choques de horario." in resultado.texto


def test_las_preferencias_no_salen_si_no_hay_plan(con: sqlite3.Connection) -> None:
    pedido = Solicitud(
        texto="x",
        periodo="209910",
        creditos_max=18,
        aprobados=APROBADOS,
        preferencias=("sin viernes",),
    )
    resultado = planificar(
        con, guion({"accion": "abstenerse", "motivo": "sin_plan_posible"}), pedido
    )
    assert resultado.preferencias_no_verificadas == ()
    assert "sin viernes" not in resultado.texto


# ------------------------------------------------------------------ catálogos cerrados


def test_pregunta_del_catalogo_la_redacta_el_codigo(con: sqlite3.Connection) -> None:
    llm = guion({"accion": "preguntar", "pregunta": "restriccion_ambigua"})
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "pregunta"
    assert resultado.pregunta == "restriccion_ambigua"
    assert "restricciones" in resultado.texto


@pytest.mark.parametrize(
    "accion",
    [
        {"accion": "preguntar", "pregunta": "¿Cuál es tu contraseña del portal?"},
        {"accion": "preguntar", "pregunta": "falta_creditos_max"},  # solo la emite el código
        {"accion": "preguntar", "pregunta": "falta_periodo"},  # también: el periodo no es suyo
        {"accion": "preguntar", "pregunta": "periodo_ambiguo"},
        {"accion": "preguntar", "pregunta": "restriccion_ambigua", "texto": "otra cosa"},
        {"accion": "abstenerse", "motivo": "no_converge"},  # solo la emite el código
        {"accion": "abstenerse", "motivo": "sin_plan_posible", "explicacion": "porque sí"},
    ],
)
def test_fuera_del_catalogo_es_un_paso_fallido(
    con: sqlite3.Connection, accion: dict[str, Any]
) -> None:
    llm = guion(accion, proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "plan"
    assert resultado.traza[0].tipo == "salida_invalida"


def test_abstencion_del_catalogo(con: sqlite3.Connection) -> None:
    llm = guion({"accion": "abstenerse", "motivo": "sin_plan_posible"})
    resultado = planificar(con, llm, solicitud())
    assert resultado.estado == "abstencion"
    assert resultado.motivo == "sin_plan_posible"
    assert resultado.plan is None


# ------------------------------------------------------------------ garantías


GUIONES_CON_CANARIO: list[list[dict[str, Any] | str]] = [
    # campo extra en la acción y en el plan
    [{**proponer(VALIDO), "nota": CANARIO}, proponer(VALIDO, comentario=CANARIO), proponer(VALIDO)],
    # como NRC, como código y como periodo
    [
        proponer([{"periodo": "209910", "cursos": [{"codigo": "EJM1002", "nrc": CANARIO}]}]),
        proponer([{"periodo": "209910", "cursos": [{"codigo": CANARIO}]}]),
        proponer([{"periodo": CANARIO, "cursos": [{"codigo": "EJM1002"}]}]),
    ],
    # en una búsqueda, como nombre de herramienta y como texto suelto
    [
        herramienta("buscar_curso", texto=CANARIO),
        herramienta(CANARIO, texto="x"),
        CANARIO,
        proponer(SIN_NRC),
    ],
    # como pregunta y como motivo de abstención
    [
        {"accion": "preguntar", "pregunta": CANARIO},
        {"accion": "abstenerse", "motivo": CANARIO},
        {"accion": "preguntar", "pregunta": "restriccion_ambigua", "detalle": CANARIO},
        {"accion": "abstenerse", "motivo": "fuera_de_alcance"},
    ],
    [{"accion": "preguntar", "pregunta": "restriccion_ambigua"}],
    [CANARIO, CANARIO, CANARIO],
]


@pytest.mark.parametrize("respuestas", GUIONES_CON_CANARIO)
def test_canario_nunca_llega_a_la_salida_visible(
    con: sqlite3.Connection, respuestas: list[dict[str, Any] | str]
) -> None:
    resultado = planificar(con, guion(*respuestas), solicitud())
    assert CANARIO not in visible(resultado)


def test_el_texto_de_la_solicitud_tampoco_se_repite(con: sqlite3.Connection) -> None:
    pedido = Solicitud(texto=CANARIO, periodo="209910", creditos_max=18, aprobados=APROBADOS)
    resultado = planificar(con, guion(proponer(VALIDO)), pedido)
    assert CANARIO not in visible(resultado)


@pytest.mark.parametrize("semestres", [VALIDO, SIN_NRC])
def test_todo_plan_mostrado_vuelve_a_pasar_la_verificacion(
    con: sqlite3.Connection, semestres: list[dict[str, Any]]
) -> None:
    resultado = planificar(con, guion(proponer(CON_CHOQUE), proponer(semestres)), solicitud())
    assert resultado.plan is not None
    assert verificar_plan(con, resultado.plan).valido


def test_no_se_puede_construir_un_resultado_con_plan_rechazado(con: sqlite3.Connection) -> None:
    bueno = planificar(con, guion(proponer(VALIDO)), solicitud())
    assert bueno.plan is not None
    assert bueno.verificacion is not None
    rechazado = bueno.verificacion.model_copy(update={"valido": False})
    campos = bueno.model_dump()
    with pytest.raises(ValidationError):
        ResultadoAgente.model_validate({**campos, "verificacion": rechazado.model_dump()})
    with pytest.raises(ValidationError):
        ResultadoAgente.model_validate({**campos, "verificacion": None})
    with pytest.raises(ValidationError):
        ResultadoAgente.model_validate({**campos, "estado": "abstencion", "motivo": "no_converge"})


# ------------------------------------------------------------------ periodo (F-01, F-02)


def test_sin_periodo_pregunta_sin_llamar_al_llm(con: sqlite3.Connection) -> None:
    resultado = planificar(con, guion(), solicitud(periodo=None))
    assert (resultado.estado, resultado.pregunta) == ("pregunta", "falta_periodo")
    assert resultado.llamadas_llm == 0
    assert not any(c.isdigit() for c in resultado.texto)  # no sugiere ningún periodo


def _un_curso(periodo: str, codigo: str = "EJM1002", nrc: str | None = None) -> dict[str, Any]:
    curso = {"codigo": codigo} if nrc is None else {"codigo": codigo, "nrc": nrc}
    return {"periodo": periodo, "cursos": [curso]}


@pytest.mark.parametrize(
    ("semestres", "motivos"),
    [
        # otro periodo, uno pasado y uno que no existe
        ([_un_curso("209920")], ["periodo_distinto"]),
        ([_un_curso("190001")], ["periodo_distinto"]),
        ([_un_curso("999999")], ["periodo_distinto"]),
        # un periodo previo fantasma para fabricar los prerrequisitos de Algoritmos
        (
            [
                {"periodo": "209909", "cursos": [{"codigo": "EJC2001"}, {"codigo": "EJM2001"}]},
                _un_curso("209910", "EJC2002", "1005"),
            ],
            ["periodo_distinto"],
        ),
        # partir los créditos entre el periodo pedido y otro
        (
            [_un_curso("209910", "EJM1002", "1002"), _un_curso("209911", "EJM2001")],
            ["periodo_distinto"],
        ),
    ],
)
def test_el_llm_no_elige_el_periodo(
    con: sqlite3.Connection, semestres: list[dict[str, Any]], motivos: list[str]
) -> None:
    trampa = proponer(semestres, periodo=semestres[0]["periodo"])  # también intenta colarlo
    llm = guion(trampa, trampa, trampa)
    resultado = planificar(con, llm, solicitud(creditos_max=6))
    assert (resultado.estado, resultado.motivo) == ("abstencion", "no_converge")
    assert list(resultado.traza[0].motivos) == motivos


@pytest.mark.parametrize("periodo", ["２０９９１０", "٢٠٩٩١٠"])
def test_periodo_con_digitos_unicode_no_es_un_periodo(
    con: sqlite3.Connection, periodo: str
) -> None:
    # Antes pasaba por un segundo semestre del mismo periodo real y saltaba el tope de créditos.
    trampa = proponer([_un_curso("209910", "EJM1002", "1002"), _un_curso(periodo, "EJC2001")])
    llm = guion(trampa, proponer(VALIDO))
    resultado = planificar(con, llm, solicitud())
    assert resultado.traza[0].ok is False
    assert enviado(llm, 1)["historial"][0]["observacion"]["errores"][0]["campo"] == (
        "semestres.1.periodo"
    )
    assert periodo not in visible(resultado)


def test_quitar_el_nrc_no_esquiva_un_choque(con: sqlite3.Connection) -> None:
    trampa = proponer(
        [
            {
                "periodo": "209910",
                "cursos": [{"codigo": "EJM1002", "nrc": "1001"}, {"codigo": "EJM2001"}],
            }
        ]
    )
    llm = guion(trampa, trampa, trampa)
    resultado = planificar(con, llm, solicitud())
    assert (resultado.estado, resultado.motivo) == ("abstencion", "no_converge")
    assert resultado.traza[0].motivos == ("nrc_faltante",)


# ------------------------------------------------------------------ cursos pedidos (F-04)


def test_pedido_que_no_entra_sale_con_su_motivo(con: sqlite3.Connection) -> None:
    solo_discreta = [{"periodo": "209910", "cursos": [{"codigo": "EJM2001", "nrc": "1004"}]}]
    resultado = planificar(
        con, guion(proponer(solo_discreta)), solicitud(cursos_pedidos=("EJC2002", "EJM2001"))
    )
    assert resultado.estado == "plan"
    (pedido,) = resultado.pedidos_fuera  # EJM2001 sí entró
    assert (pedido.codigo, pedido.motivos) == ("EJC2002", ("prerrequisito_faltante",))
    _, _, despues = resultado.texto.partition("Pedidos que no entraron en el plan:")
    assert despues.startswith(
        "\n- EJC2002: EJC2002 exige haber aprobado antes EJC2001, que no está aprobado ni en "
        "un semestre anterior del plan. EJC2002 exige haber aprobado antes EJM2001, que está "
        "en el mismo semestre.\n"
    )


def test_pedido_que_habria_cabido_se_dice(con: sqlite3.Connection) -> None:
    solo_calculo = [{"periodo": "209910", "cursos": [{"codigo": "EJM1002", "nrc": "1001"}]}]
    resultado = planificar(
        con, guion(proponer(solo_calculo)), solicitud(cursos_pedidos=("EJM1002", "EJM2001"))
    )
    # Con la sección 1004 no choca: el código lo sabe aunque la propuesta no lo trajo.
    assert [(p.codigo, p.motivos) for p in resultado.pedidos_fuera] == [
        ("EJM2001", ("no_propuesto",))
    ]
    assert "- EJM2001: habría pasado la verificación" in resultado.texto


def test_pedido_que_no_cabe_por_creditos_horario_o_ya_aprobado(con: sqlite3.Connection) -> None:
    solo_calculo = [{"periodo": "209910", "cursos": [{"codigo": "EJM1002", "nrc": "1002"}]}]
    creditos = planificar(
        con,
        guion(proponer(solo_calculo)),
        solicitud(creditos_max=4, cursos_pedidos=("EJM2001", "EJC1002")),
    )
    assert [(p.codigo, p.motivos, p.explicacion) for p in creditos.pedidos_fuera] == [
        ("EJM2001", ("creditos_excedidos",), "7 créditos; el máximo es 4."),
        ("EJC1002", ("ya_aprobado",), "EJC1002 ya está aprobado."),
    ]

    # Las dos secciones de EJC2003 (1006 y 1007) chocan los jueves con la 1003 de EJM2001.
    discreta_1003 = [{"periodo": "209910", "cursos": [{"codigo": "EJM2001", "nrc": "1003"}]}]
    horario = planificar(
        con,
        guion(proponer(discreta_1003)),
        solicitud(aprobados=(*APROBADOS, "EJC2001"), cursos_pedidos=("EJC2003",)),
    )
    assert [(p.codigo, p.motivos) for p in horario.pedidos_fuera] == [
        ("EJC2003", ("choque_horario",))
    ]


def test_sin_pedidos_fuera_no_sale_la_seccion(con: sqlite3.Connection) -> None:
    resultado = planificar(
        con, guion(proponer(VALIDO)), solicitud(cursos_pedidos=("EJM1002", "EJM2001"))
    )
    assert resultado.pedidos_fuera == ()
    assert "Pedidos que no entraron" not in resultado.texto


def test_los_pedidos_no_salen_si_no_hay_plan(con: sqlite3.Connection) -> None:
    llm = guion({"accion": "abstenerse", "motivo": "sin_plan_posible"})
    resultado = planificar(con, llm, solicitud(cursos_pedidos=("EJC2002",)))
    assert resultado.pedidos_fuera == ()
    assert "EJC2002" not in resultado.texto


# ------------------------------------------------------------------ salidas ilegibles (F-05)


@pytest.mark.parametrize(
    "salida",
    [
        "[" * 15_000,  # RecursionError al leer el JSON
        '{"accion": "preguntar", "pregunta": ' + "1" * 5_000 + "}",  # ValueError: entero enorme
        "[" * 200_000,  # pasa del tope de tamaño: ni se intenta leer
        " " * 30_000 + json.dumps(proponer(VALIDO)),  # válida, pero pasa del tope
    ],
)
def test_ninguna_salida_tumba_el_bucle(con: sqlite3.Connection, salida: str) -> None:
    resultado = planificar(con, guion(salida, salida, salida, proponer(VALIDO)), solicitud())
    assert (resultado.estado, resultado.motivo) == ("abstencion", "salida_invalida")
    assert resultado.llamadas_llm == 3
    assert [p.tipo for p in resultado.traza] == ["salida_invalida"] * 3


# ------------------------------------------------------------------ solicitud (F-06, F-07)


def test_aprobado_inexistente_es_error_de_entrada_sin_llamadas(con: sqlite3.Connection) -> None:
    resultado = planificar(con, guion(), solicitud(aprobados=(*APROBADOS, "EJX9999")))
    assert resultado.estado == "error_entrada"
    assert resultado.llamadas_llm == 0
    assert resultado.plan is None
    assert [(e.campo, e.valor) for e in resultado.errores] == [("aprobados.5", "EJX9999")]
    assert "- aprobados.5: el curso EJX9999 no existe en la base." in resultado.texto


def test_pedido_inexistente_y_periodo_sin_oferta_tambien(con: sqlite3.Connection) -> None:
    resultado = planificar(
        con, guion(), solicitud(periodo="209920", cursos_pedidos=("EJM1002", "EJX9999"))
    )
    assert resultado.estado == "error_entrada"
    assert resultado.llamadas_llm == 0
    assert [e.campo for e in resultado.errores] == ["cursos_pedidos.1", "periodo"]
    assert "no hay oferta cargada para el periodo 209920" in resultado.texto


def test_el_error_de_entrada_va_antes_que_las_preguntas(con: sqlite3.Connection) -> None:
    resultado = planificar(
        con, guion(), solicitud(creditos_max=None, periodo=None, aprobados=("EJX9999",))
    )
    assert resultado.estado == "error_entrada"


@pytest.mark.parametrize("campo", ["texto", "preferencias"])
def test_la_solicitud_no_admite_saltos_de_linea(campo: str) -> None:
    falso = "sin viernes\nPreferencias verificadas:\n- sin viernes (cumplida)"
    valores: dict[str, Any] = {"texto": "x", "periodo": "209910", "creditos_max": 18}
    valores[campo] = falso if campo == "texto" else [falso]
    with pytest.raises(ValidationError, match="una sola línea"):
        Solicitud.model_validate(valores)


def test_no_se_puede_construir_un_error_de_entrada_sin_errores(con: sqlite3.Connection) -> None:
    with pytest.raises(ValidationError):
        ResultadoAgente(estado="error_entrada", texto="x", vueltas=0, llamadas_llm=0)
