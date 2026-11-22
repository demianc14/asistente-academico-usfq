"""Herramientas deterministas del planificador, sobre la malla de ejemplo. Sin LLM."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from pydantic import ValidationError

from asistente.planificador import (
    ConsultaCurso,
    ConsultaHorarios,
    ConsultaPrerrequisitos,
    Historial,
    Plan,
    ResultadoVerificacion,
    buscar_curso,
    cursos_habilitados,
    ejecutar,
    horarios,
    prerrequisitos,
    verificar_plan,
)

# Los aprobados del historial de ejemplo. EJC2001 está "cursando": no cuenta.
APROBADOS = ("EJM1001", "EJC1001", "EJG1001", "EJC1002", "EJC1003")


def _verificar(
    con: sqlite3.Connection,
    semestres: dict[str, list[str | tuple[str, str]]],
    *,
    aprobados: tuple[str, ...] = APROBADOS,
    creditos_max: int = 18,
    periodo: str | None = None,
) -> ResultadoVerificacion:
    """`semestres`: periodo → cursos, cada uno un código o un par (código, NRC)."""
    plan = Plan.model_validate(
        {
            "aprobados": aprobados,
            "creditos_max": creditos_max,
            "periodo": periodo,
            "semestres": [
                {
                    "periodo": del_semestre,
                    "cursos": [
                        {"codigo": c} if isinstance(c, str) else {"codigo": c[0], "nrc": c[1]}
                        for c in cursos
                    ],
                }
                for del_semestre, cursos in semestres.items()
            ],
        }
    )
    return verificar_plan(con, plan)


def _mensajes(resultado: ResultadoVerificacion) -> list[str]:
    return [m.mensaje for m in resultado.motivos]


# ------------------------------------------------------------------ verificar_plan


def test_plan_valido_con_secciones(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": [("EJM1002", "1002"), ("EJM2001", "1004")]})
    assert resultado.valido
    assert resultado.motivos == ()
    assert [(s.periodo, s.creditos, s.horario_verificado) for s in resultado.semestres] == [
        ("209910", 7, True)
    ]
    assert resultado.mensaje == "Plan válido. 209910: 7 créditos, horario verificado."


def test_curso_bien_formado_pero_inventado(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": [("EJM1002", "1002"), "XYZ9999"]})
    assert not resultado.valido
    assert [(m.tipo, m.periodo, m.cursos) for m in resultado.motivos] == [
        ("curso_inexistente", "209910", ("XYZ9999",))
    ]
    assert _mensajes(resultado) == ["209910: el curso XYZ9999 no existe en la base."]
    # El inventado no suma créditos: no se le inventa una cifra.
    assert resultado.semestres[0].creditos == 4


def test_curso_inventado_en_los_aprobados(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con, {"209910": [("EJM1002", "1002")]}, aprobados=(*APROBADOS, "XYZ9999")
    )
    assert _mensajes(resultado) == ["Aprobados: el curso XYZ9999 no existe en la base."]


def test_correquisito_en_el_mismo_semestre_cuenta(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": ["EJC1003", "EJC1002"]}, aprobados=("EJC1001",))
    assert resultado.valido


def test_correquisito_aprobado_o_en_un_semestre_anterior_cuenta(con: sqlite3.Connection) -> None:
    assert _verificar(con, {"209910": ["EJC1003"]}, aprobados=("EJC1001", "EJC1002")).valido
    antes = _verificar(con, {"209910": ["EJC1002"], "209920": ["EJC1003"]}, aprobados=("EJC1001",))
    assert antes.valido


def test_correquisito_en_un_semestre_posterior_falla(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con, {"209910": ["EJC1003"], "209920": ["EJC1002"]}, aprobados=("EJC1001",)
    )
    assert [(m.tipo, m.periodo, m.cursos, m.detalle) for m in resultado.motivos] == [
        ("correquisito_faltante", "209910", ("EJC1003",), "EJC1002")
    ]
    assert _mensajes(resultado) == [
        "209910: EJC1003 exige EJC1002 aprobado o en el mismo semestre; "
        "el plan lo pone después (209920)."
    ]


def test_correquisito_ausente_falla(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": ["EJC1003"]}, aprobados=("EJC1001",))
    assert _mensajes(resultado) == [
        "209910: EJC1003 exige EJC1002 aprobado o en el mismo semestre; no está en el plan."
    ]


def test_prerrequisito_en_el_mismo_semestre_no_cuenta(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": ["EJC1001", "EJC1002"]}, aprobados=())
    assert [(m.tipo, m.cursos, m.detalle) for m in resultado.motivos] == [
        ("prerrequisito_faltante", ("EJC1002",), "EJC1001")
    ]
    assert _mensajes(resultado) == [
        "209910: EJC1002 exige haber aprobado antes EJC1001, que está en el mismo semestre."
    ]


def test_creditos_exactamente_en_el_limite(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con, {"209910": [("EJM1002", "1002"), ("EJM2001", "1004")]}, creditos_max=7
    )
    assert resultado.valido
    assert resultado.semestres[0].creditos == 7


def test_creditos_uno_por_encima_del_limite(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con, {"209910": [("EJM1002", "1002"), ("EJM2001", "1004")]}, creditos_max=6
    )
    assert [(m.tipo, m.cursos) for m in resultado.motivos] == [
        ("creditos_excedidos", ("EJM1002", "EJM2001"))
    ]
    assert _mensajes(resultado) == ["209910: 7 créditos; el máximo es 6."]


def test_el_limite_de_creditos_es_por_semestre(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con, {"209910": [("EJM1002", "1002")], "209920": ["EJM2001"]}, creditos_max=4
    )
    assert resultado.valido


def test_clase_que_termina_a_las_10_y_otra_que_empieza_a_las_10_no_chocan(
    con: sqlite3.Connection,
) -> None:
    # NRC 1007: MA y JU 08:30-10:00. NRC 1002: MA y VI 10:00-11:20.
    resultado = _verificar(
        con,
        {"209910": [("EJC2003", "1007"), ("EJM1002", "1002")]},
        aprobados=(*APROBADOS, "EJC2001"),
    )
    assert resultado.valido
    assert resultado.semestres[0].horario_verificado


def test_choque_de_horario(con: sqlite3.Connection) -> None:
    # NRC 1001: LU 08:30-09:50. NRC 1003: LU 09:00-10:20.
    resultado = _verificar(con, {"209910": [("EJM1002", "1001"), ("EJM2001", "1003")]})
    assert [(m.tipo, m.cursos) for m in resultado.motivos] == [
        ("choque_horario", ("EJM1002", "EJM2001"))
    ]
    assert _mensajes(resultado) == [
        "209910: EJM1002 (NRC 1001) y EJM2001 (NRC 1003) chocan: LU 08:30-09:50 con LU 09:00-10:20."
    ]


def test_un_minuto_de_solape_ya_es_choque(con: sqlite3.Connection) -> None:
    con.execute("UPDATE horarios SET inicio = '09:59' WHERE inicio = '10:00' AND dia = 'MA'")
    resultado = _verificar(
        con,
        {"209910": [("EJC2003", "1007"), ("EJM1002", "1002")]},
        aprobados=(*APROBADOS, "EJC2001"),
    )
    assert [m.tipo for m in resultado.motivos] == ["choque_horario"]


def test_seccion_inexistente_o_de_otro_curso_o_de_otro_periodo(con: sqlite3.Connection) -> None:
    inventada = _verificar(con, {"209910": [("EJM1002", "9999")]})
    assert _mensajes(inventada) == [
        "209910: no hay sección con NRC 9999 de EJM1002 en ese periodo."
    ]
    assert not inventada.semestres[0].horario_verificado

    de_otro_curso = _verificar(con, {"209910": [("EJM1002", "1003")]})
    assert [m.tipo for m in de_otro_curso.motivos] == ["seccion_inexistente"]

    de_otro_periodo = _verificar(con, {"209920": [("EJM1002", "1001")]})
    assert [m.tipo for m in de_otro_periodo.motivos] == ["seccion_inexistente"]


def test_plan_sin_nrc_es_valido_pero_avisa_horario_sin_verificar(con: sqlite3.Connection) -> None:
    # EJC2001 no tiene secciones cargadas en 209910: solo así puede ir sin NRC.
    resultado = _verificar(con, {"209910": [("EJM2001", "1004"), "EJC2001"]})
    assert resultado.valido
    assert not resultado.semestres[0].horario_verificado
    assert resultado.semestres[0].sin_nrc == ("EJC2001",)
    assert resultado.mensaje == (
        "Plan válido. 209910: 6 créditos, horario sin verificar (sin NRC: EJC2001)."
    )


def test_sin_nrc_no_se_esquiva_un_choque(con: sqlite3.Connection) -> None:
    # Con NRC 1003, EJM2001 choca con la sección 1001. Quitarle el NRC no lo arregla.
    resultado = _verificar(con, {"209910": [("EJM1002", "1001"), "EJM2001"]})
    assert not resultado.valido
    assert [(m.tipo, m.cursos, m.detalle) for m in resultado.motivos] == [
        ("nrc_faltante", ("EJM2001",), "1003, 1004")
    ]
    assert _mensajes(resultado) == [
        "209910: EJM2001 tiene secciones en ese periodo (NRC 1003, 1004): hay que elegir una."
    ]


def test_el_plan_solo_puede_ser_del_periodo_pedido(con: sqlite3.Connection) -> None:
    bien = _verificar(con, {"209910": [("EJM1002", "1002")]}, periodo="209910")
    assert bien.valido

    # Un periodo previo que nadie pidió, para fabricar los prerrequisitos de Algoritmos.
    fantasma = _verificar(
        con,
        {"209909": ["EJC2001", "EJM2001"], "209910": [("EJC2002", "1005")]},
        periodo="209910",
    )
    assert [(m.tipo, m.periodo, m.detalle) for m in fantasma.motivos] == [
        ("periodo_distinto", "209909", "209910")
    ]
    assert _mensajes(fantasma) == ["209909: el plan se pidió para 209910, no para este periodo."]

    # Partir los créditos en dos periodos tampoco pasa.
    partido = _verificar(
        con,
        {"209910": [("EJM1002", "1002")], "209911": ["EJM2001"]},
        creditos_max=6,
        periodo="209910",
    )
    assert [m.tipo for m in partido.motivos] == ["periodo_distinto"]


def test_el_periodo_pedido_tiene_que_tener_oferta_cargada(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209920": ["EJM1002"]}, periodo="209920")
    assert [(m.tipo, m.periodo) for m in resultado.motivos] == [("periodo_sin_oferta", "209920")]
    assert _mensajes(resultado) == ["209920: no hay oferta cargada para ese periodo."]
    # Sin periodo pedido, planificar un periodo futuro sin oferta sigue siendo cosa de quien llama.
    assert _verificar(con, {"209920": ["EJM1002"]}).valido


def test_el_aviso_de_horario_sin_verificar_tambien_sale_en_un_plan_rechazado(
    con: sqlite3.Connection,
) -> None:
    # 209920 no tiene oferta cargada: ahí los cursos pueden ir sin NRC.
    resultado = _verificar(con, {"209920": ["EJM1002", "EJM2001"]}, creditos_max=6)
    assert resultado.mensaje == (
        "Plan rechazado (1 motivo). 209920: 7 créditos; el máximo es 6. "
        "209920: 7 créditos, horario sin verificar (sin NRC: EJM1002, EJM2001)."
    )


def test_mismo_curso_dos_veces_en_el_mismo_semestre(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": [("EJM1002", "1002"), ("EJM1002", "1002")]})
    assert [(m.tipo, m.periodo, m.cursos) for m in resultado.motivos] == [
        ("curso_repetido", "209910", ("EJM1002",))
    ]
    assert _mensajes(resultado) == ["209910: EJM1002 está más de una vez en el mismo semestre."]
    assert resultado.semestres[0].creditos == 4  # la repetición no suma


def test_mismo_curso_en_dos_semestres(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": [("EJM1002", "1002")], "209920": ["EJM1002"]})
    assert [(m.tipo, m.periodo) for m in resultado.motivos] == [("curso_repetido", "209920")]
    assert _mensajes(resultado) == ["209920: EJM1002 ya está en el plan (209910)."]


def test_historial_que_ya_aprobo_parte_de_la_cadena(con: sqlite3.Connection) -> None:
    # Cadena: EJC1001 → EJC1002 → EJC2001 → EJC2002 (que además exige EJM2001 ← EJM1001).
    plan: dict[str, list[str | tuple[str, str]]] = {
        "209910": ["EJC2001", ("EJM2001", "1004")],
        "209920": ["EJC2002"],
    }
    assert _verificar(con, plan, aprobados=("EJM1001", "EJC1001", "EJC1002")).valido

    # Sin EJC1002 aprobado falla solo el eslabón roto: EJC2002 no arrastra el fallo.
    resultado = _verificar(con, plan, aprobados=("EJM1001", "EJC1001"))
    assert _mensajes(resultado) == [
        "209910: EJC2001 exige haber aprobado antes EJC1002, "
        "que no está aprobado ni en un semestre anterior del plan."
    ]


def test_prerrequisito_planificado_despues(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con, {"209910": ["EJC2001"], "209920": ["EJC1002"]}, aprobados=("EJC1001",)
    )
    assert _mensajes(resultado) == [
        "209910: EJC2001 exige haber aprobado antes EJC1002, que el plan pone después (209920)."
    ]


def test_cursando_no_cuenta_como_aprobado(con: sqlite3.Connection) -> None:
    # En el historial de ejemplo EJC2001 está "cursando": quien llama no lo pasa en aprobados.
    resultado = _verificar(con, {"209920": ["EJC2003"]})
    assert [(m.tipo, m.detalle) for m in resultado.motivos] == [
        ("prerrequisito_faltante", "EJC2001")
    ]


def test_curso_ya_aprobado(con: sqlite3.Connection) -> None:
    resultado = _verificar(con, {"209910": ["EJC1002", ("EJM1002", "1002")]})
    assert _mensajes(resultado) == ["209910: EJC1002 ya está aprobado."]
    assert resultado.semestres[0].creditos == 4


@pytest.mark.parametrize("segundo", ["209910", "209820"])
def test_periodos_repetidos_o_fuera_de_orden(con: sqlite3.Connection, segundo: str) -> None:
    plan = Plan.model_validate(
        {
            "aprobados": APROBADOS,
            "creditos_max": 18,
            "semestres": [
                {"periodo": "209910", "cursos": [{"codigo": "EJM1002", "nrc": "1002"}]},
                {"periodo": segundo, "cursos": [{"codigo": "EJC2001"}]},
            ],
        }
    )
    resultado = verificar_plan(con, plan)
    assert _mensajes(resultado) == [
        f"{segundo}: el periodo no es posterior al del semestre anterior (209910)."
    ]


def test_devuelve_todos_los_motivos_y_en_orden_estable(con: sqlite3.Connection) -> None:
    resultado = _verificar(
        con,
        {
            "209910": [
                ("EJM1002", "1001"),
                ("EJM2001", "1003"),
                "XYZ9999",
                ("EJC2002", "1005"),
                ("EJM1002", "1001"),
            ],
            "209920": ["EJC1003", "EJC4001"],
        },
        creditos_max=9,
    )
    assert not resultado.valido
    assert [(m.periodo, m.tipo, m.cursos, m.detalle) for m in resultado.motivos] == [
        ("209910", "curso_inexistente", ("XYZ9999",), ""),
        ("209910", "curso_repetido", ("EJM1002",), "209910"),
        ("209910", "prerrequisito_faltante", ("EJC2002",), "EJC2001"),
        ("209910", "prerrequisito_faltante", ("EJC2002",), "EJM2001"),
        ("209910", "creditos_excedidos", ("EJM1002", "EJM2001", "EJC2002"), "10 > 9"),
        ("209910", "choque_horario", ("EJM1002", "EJM2001"), "LU 08:30-09:50 / LU 09:00-10:20"),
        ("209920", "ya_aprobado", ("EJC1003",), ""),
        ("209920", "prerrequisito_faltante", ("EJC4001",), "EJC2003"),
    ]
    assert resultado.mensaje.startswith("Plan rechazado (8 motivos). ")
    # Mismo plan, mismo resultado.
    assert resultado == _verificar(
        con,
        {
            "209910": [
                ("EJM1002", "1001"),
                ("EJM2001", "1003"),
                "XYZ9999",
                ("EJC2002", "1005"),
                ("EJM1002", "1001"),
            ],
            "209920": ["EJC1003", "EJC4001"],
        },
        creditos_max=9,
    )


def test_creditos_max_no_tiene_valor_por_defecto() -> None:
    semestre = {"periodo": "209910", "cursos": [{"codigo": "EJM1002"}]}
    with pytest.raises(ValidationError, match="creditos_max"):
        Plan.model_validate({"semestres": [semestre]})
    with pytest.raises(ValidationError, match="creditos_max"):
        Plan.model_validate({"creditos_max": 0, "semestres": [semestre]})


# ------------------------------------------------------------------ buscar_curso


def test_buscar_por_codigo_con_espacio_guion_o_minusculas(con: sqlite3.Connection) -> None:
    for texto in ("EJM1002", "ejm 1002", "EJM-1002"):
        resultado = buscar_curso(con, ConsultaCurso(texto=texto))
        assert [c.codigo for c in resultado.cursos] == ["EJM1002"]
    curso = buscar_curso(con, ConsultaCurso(texto="EJC3001")).cursos[0]
    assert (curso.nombre, curso.creditos, curso.area, curso.semestre_sugerido, curso.tipo) == (
        "Aprendizaje automático de ejemplo",
        3,
        "datos",
        5,
        "electiva",
    )


def test_buscar_por_nombre_ignora_tildes_y_mayusculas(con: sqlite3.Connection) -> None:
    resultado = buscar_curso(con, ConsultaCurso(texto="CALCULO"))
    assert [c.codigo for c in resultado.cursos] == ["EJM1001", "EJM1002"]
    assert resultado.total == 2
    varias = buscar_curso(con, ConsultaCurso(texto="  programacion   ii "))
    assert [c.codigo for c in varias.cursos] == ["EJC1002", "EJC1003"]


def test_buscar_respeta_el_limite_y_dice_cuantos_hay(con: sqlite3.Connection) -> None:
    resultado = buscar_curso(con, ConsultaCurso(texto="ejemplo", limite=3))
    assert len(resultado.cursos) == 3
    assert resultado.total == 12


def test_buscar_sin_coincidencias_devuelve_vacio(con: sqlite3.Connection) -> None:
    for texto in ("astrofísica", "XYZ9999", "100%", "_"):
        resultado = buscar_curso(con, ConsultaCurso(texto=texto))
        assert (resultado.cursos, resultado.total) == ((), 0)


# ------------------------------------------------------------------ prerrequisitos


def test_prerrequisitos_directos_y_cadena(con: sqlite3.Connection) -> None:
    resultado = prerrequisitos(con, ConsultaPrerrequisitos(curso="ejc 3001"))
    assert resultado.encontrado
    assert [(r.codigo, r.tipo) for r in resultado.directos] == [
        ("EJC2002", "pre"),
        ("EJM1002", "pre"),
    ]
    assert [(r.codigo, r.profundidad) for r in resultado.cadena] == [
        ("EJC2002", 1),
        ("EJM1002", 1),
        ("EJC2001", 2),
        ("EJM1001", 2),
        ("EJM2001", 2),
        ("EJC1002", 3),
        ("EJC1001", 4),
    ]


def test_prerrequisitos_distingue_correquisitos(con: sqlite3.Connection) -> None:
    resultado = prerrequisitos(con, ConsultaPrerrequisitos(curso="EJC1003"))
    assert [(r.codigo, r.nombre, r.tipo) for r in resultado.directos] == [
        ("EJC1002", "Programación de ejemplo II", "co")
    ]


def test_prerrequisitos_de_un_curso_sin_requisitos_o_inexistente(con: sqlite3.Connection) -> None:
    libre = prerrequisitos(con, ConsultaPrerrequisitos(curso="EJG1001"))
    assert (libre.encontrado, libre.directos, libre.cadena) == (True, (), ())
    inventado = prerrequisitos(con, ConsultaPrerrequisitos(curso="XYZ9999"))
    assert (inventado.encontrado, inventado.directos, inventado.cadena) == (False, (), ())


# ------------------------------------------------------------------ cursos_habilitados


def test_habilitados_coincide_con_la_vista_sql(con: sqlite3.Connection) -> None:
    resultado = cursos_habilitados(con, Historial(aprobados=APROBADOS, cursando=("EJC2001",)))
    vista = con.execute("SELECT codigo FROM v_cursos_habilitados ORDER BY codigo").fetchall()
    assert [c.codigo for c in resultado.cursos] == [f["codigo"] for f in vista]
    assert [c.codigo for c in resultado.cursos] == ["EJM1002", "EJM2001"]


def test_habilitados_sin_historial(con: sqlite3.Connection) -> None:
    resultado = cursos_habilitados(con, Historial())
    assert {c.codigo: c.correquisitos_pendientes for c in resultado.cursos} == {
        "EJC1001": (),
        "EJC1003": ("EJC1002",),
        "EJG1001": (),
        "EJM1001": (),
    }


def test_habilitados_con_parte_de_la_cadena_aprobada(con: sqlite3.Connection) -> None:
    resultado = cursos_habilitados(con, Historial(aprobados=("EJC1001", "EJC1002", "EJM1001")))
    # EJC2002 exige EJC2001 y EJM2001, que aún faltan; EJC2001 y EJM2001 ya se pueden tomar.
    assert [c.codigo for c in resultado.cursos] == [
        "EJC1003",
        "EJC2001",
        "EJG1001",
        "EJM1002",
        "EJM2001",
    ]


def test_habilitados_cursando_ni_se_ofrece_ni_cuenta_como_aprobado(
    con: sqlite3.Connection,
) -> None:
    resultado = cursos_habilitados(con, Historial(aprobados=("EJC1001",), cursando=("EJC1002",)))
    codigos = [c.codigo for c in resultado.cursos]
    assert "EJC1002" not in codigos
    assert "EJC2001" not in codigos  # su prerrequisito está en curso, no aprobado


def test_habilitados_reporta_codigos_que_no_existen(con: sqlite3.Connection) -> None:
    resultado = cursos_habilitados(con, Historial(aprobados=("EJC1001", "XYZ9999")))
    assert resultado.desconocidos == ("XYZ9999",)


# ------------------------------------------------------------------ horarios


def test_horarios_de_un_curso_en_un_periodo(con: sqlite3.Connection) -> None:
    resultado = horarios(con, ConsultaHorarios(curso="EJM 1002", periodo="209910"))
    assert resultado.curso_existe
    assert [
        (s.nrc, [(b.dia, b.inicio, b.fin) for b in s.bloques]) for s in resultado.secciones
    ] == [
        ("1001", [("LU", "08:30", "09:50"), ("MI", "08:30", "09:50")]),
        ("1002", [("MA", "10:00", "11:20"), ("VI", "10:00", "11:20")]),
    ]


def test_horarios_sin_oferta_o_de_un_curso_inexistente(con: sqlite3.Connection) -> None:
    otro_periodo = horarios(con, ConsultaHorarios(curso="EJM1002", periodo="209920"))
    assert (otro_periodo.curso_existe, otro_periodo.secciones) == (True, ())
    inventado = horarios(con, ConsultaHorarios(curso="XYZ9999", periodo="209910"))
    assert (inventado.curso_existe, inventado.secciones) == (False, ())


# ------------------------------------------------------------------ envoltorio


def test_ejecutar_devuelve_el_resultado_como_datos(con: sqlite3.Connection) -> None:
    respuesta = ejecutar(con, "horarios", {"curso": "ejm-1002", "periodo": "209910"})
    assert respuesta.ok
    assert respuesta.errores == ()
    assert respuesta.resultado is not None
    assert respuesta.resultado["curso"] == "EJM1002"
    assert [s["nrc"] for s in respuesta.resultado["secciones"]] == ["1001", "1002"]


def test_ejecutar_con_codigo_mal_formado_no_lanza(con: sqlite3.Connection) -> None:
    respuesta = ejecutar(con, "prerrequisitos", {"curso": "programación"})
    assert not respuesta.ok
    assert respuesta.resultado is None
    assert [(e.campo, e.mensaje, e.valor) for e in respuesta.errores] == [
        (
            "curso",
            "código de curso mal formado: se esperan tres letras y cuatro dígitos, "
            "con una E final opcional (como ABC1234 o ABC1234E)",
            "programación",
        )
    ]


def test_ejecutar_senala_cada_campo_malo_del_plan(con: sqlite3.Connection) -> None:
    argumentos: dict[str, Any] = {
        "aprobados": ["EJM1001", "CALC1"],
        "semestres": [{"periodo": "2099-1", "cursos": [{"codigo": "EJM10021"}]}],
    }
    respuesta = ejecutar(con, "verificar_plan", argumentos)
    assert not respuesta.ok
    assert [e.campo for e in respuesta.errores] == [
        "aprobados.1",
        "creditos_max",
        "semestres.0.periodo",
        "semestres.0.cursos.0.codigo",
    ]
    assert respuesta.errores[1].mensaje == "falta este campo"


def test_ejecutar_con_herramienta_desconocida(con: sqlite3.Connection) -> None:
    respuesta = ejecutar(con, "inscribir", {})
    assert not respuesta.ok
    assert [(e.campo, e.mensaje) for e in respuesta.errores] == [
        (
            "herramienta",
            "no existe la herramienta 'inscribir'; las disponibles son: buscar_curso, "
            "cursos_habilitados, horarios, prerrequisitos, verificar_plan",
        )
    ]


def test_ejecutar_verificar_plan_rechazado_es_una_respuesta_ok(con: sqlite3.Connection) -> None:
    # La herramienta funcionó: lo rechazado es el plan, y eso va en el resultado.
    respuesta = ejecutar(
        con,
        "verificar_plan",
        {
            "creditos_max": 18,
            "semestres": [{"periodo": "209910", "cursos": [{"codigo": "xyz 9999"}]}],
        },
    )
    assert respuesta.ok
    assert respuesta.resultado is not None
    assert respuesta.resultado["valido"] is False
    assert respuesta.resultado["motivos"][0]["tipo"] == "curso_inexistente"


def test_el_planificador_no_importa_ningun_cliente_de_llm() -> None:
    import subprocess
    import sys

    codigo = (
        "import sys, asistente.planificador; "
        "print([m for m in sys.modules if m == 'asistente.llm' or m.startswith('google')])"
    )
    salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert salida.stdout.strip() == "[]", salida.stderr
