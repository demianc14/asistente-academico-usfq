from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import TypeAdapter, ValidationError

from asistente.db import conectar, crear_esquema
from asistente.etl import ErrorDeCarga, cargar, leer_historial, leer_malla
from asistente.etl.__main__ import main
from asistente.etl.modelos import ArchivoMalla, Codigo, Hora, Periodo, Texto


def _malla(**cambios: Any) -> ArchivoMalla:
    base: dict[str, Any] = {
        "carrera": "PRUEBA",
        "cursos": [
            {"codigo": "EJA1001", "nombre": "A", "creditos": 3, "area": "x", "semestre": 1},
            {
                "codigo": "EJA1002",
                "nombre": "B",
                "creditos": 3,
                "area": "x",
                "semestre": 2,
                "pre": ["EJA1001"],
            },
        ],
    }
    base.update(cambios)
    return ArchivoMalla.model_validate(base)


def _contar(con: sqlite3.Connection, tabla: str) -> int:
    fila = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()
    return int(fila[0])


def test_carga_el_ejemplo_y_reporta(ejemplo: Path) -> None:
    con = conectar(":memory:")
    reporte = cargar(
        con, leer_malla(ejemplo / "malla.yaml"), leer_historial(ejemplo / "historial.yaml")
    )
    assert (reporte.cursos, reporte.creditos) == (13, 39)
    assert (reporte.cupos, reporte.creditos_cupos) == (1, 3)
    assert (reporte.prerrequisitos, reporte.correquisitos) == (12, 1)
    assert reporte.secciones == 7
    assert reporte.registros_historial == 7
    assert len(reporte.cursos_sin_seccion) == 9
    assert reporte.avisos == ()
    assert _contar(con, "cursos") == 13
    assert _contar(con, "horarios") == 14


def test_prerrequisito_inexistente_detiene_la_carga_sin_tocar_la_base(
    con: sqlite3.Connection,
) -> None:
    mala = _malla(
        cursos=[
            {
                "codigo": "EJA1002",
                "nombre": "B",
                "creditos": 3,
                "area": "x",
                "semestre": 2,
                "pre": ["EJA9999"],
            }
        ]
    )
    with pytest.raises(ErrorDeCarga) as info:
        cargar(con, mala)
    assert info.value.errores == ["EJA1002: el requisito EJA9999 no existe en la malla"]
    assert _contar(con, "cursos") == 13  # sigue la carga anterior


def test_detecta_ciclo_de_prerrequisitos() -> None:
    cursos = [
        {"codigo": c, "nombre": c, "creditos": 3, "area": "x", "semestre": 1, "pre": [p]}
        for c, p in [("EJA1001", "EJA1003"), ("EJA1002", "EJA1001"), ("EJA1003", "EJA1002")]
    ]
    with pytest.raises(ErrorDeCarga, match="ciclo de prerrequisitos: EJA1001 → EJA1003 → EJA1002"):
        cargar(conectar(":memory:"), _malla(cursos=cursos))


def test_reune_todos_los_errores_de_una_vez() -> None:
    curso = {"codigo": "EJA1001", "nombre": "A", "creditos": 3, "area": "x", "semestre": 1}
    seccion = {
        "curso": "EJA7777",
        "nrc": "1",
        "periodo": "209910",
        "horario": [{"dia": "LU", "inicio": "08:00", "fin": "09:00"}],
    }
    mala = _malla(cursos=[curso, {**curso, "pre": ["EJA1001"]}], secciones=[seccion, seccion])
    with pytest.raises(ErrorDeCarga) as info:
        cargar(conectar(":memory:"), mala)
    texto = str(info.value)
    assert "curso duplicado: EJA1001" in texto
    assert "es requisito de sí mismo" in texto
    assert "el curso EJA7777 no existe" in texto
    assert "sección duplicada: NRC 1 en 209910" in texto


def test_historial_con_curso_inexistente(ejemplo: Path) -> None:
    with pytest.raises(ErrorDeCarga, match="historial: el curso EJM1001 no existe"):
        cargar(conectar(":memory:"), _malla(), leer_historial(ejemplo / "historial.yaml"))


def test_normaliza_codigos_con_espacios_y_minusculas() -> None:
    malla = _malla(
        cursos=[{"codigo": "eja 1001", "nombre": "A", "creditos": 3, "area": "x", "semestre": 1}]
    )
    assert malla.cursos[0].codigo == "EJA1001"


@pytest.mark.parametrize(
    ("campo", "valor", "mensaje"),
    [
        ("creditos", -1, "cursos.0.creditos"),
        ("codigo", "PROGRAMACION", "cursos.0.codigo"),
        ("semestre", 13, "cursos.0.semestre"),
        ("profesor", "alguien", "cursos.0.profesor"),  # campos desconocidos: prohibidos
    ],
)
def test_archivo_invalido_se_rechaza_al_leer(
    tmp_path: Path, campo: str, valor: object, mensaje: str
) -> None:
    curso = {"codigo": "EJA1001", "nombre": "A", "creditos": 3, "area": "x", "semestre": 1}
    ruta = tmp_path / "malla.yaml"
    ruta.write_text(yaml.safe_dump({"carrera": "P", "cursos": [{**curso, campo: valor}]}))
    with pytest.raises(ErrorDeCarga, match=mensaje):
        leer_malla(ruta)


def test_bloque_que_termina_antes_de_empezar(tmp_path: Path) -> None:
    ruta = tmp_path / "malla.yaml"
    ruta.write_text(
        yaml.safe_dump(
            {
                "carrera": "P",
                "cursos": [
                    {"codigo": "EJA1001", "nombre": "A", "creditos": 3, "area": "x", "semestre": 1}
                ],
                "secciones": [
                    {
                        "curso": "EJA1001",
                        "nrc": "1",
                        "periodo": "209910",
                        "horario": [{"dia": "LU", "inicio": "10:00", "fin": "09:00"}],
                    }
                ],
            }
        )
    )
    with pytest.raises(ErrorDeCarga, match="termina"):
        leer_malla(ruta)


def test_avisa_si_un_prerrequisito_no_va_antes() -> None:
    cursos = [
        {"codigo": "EJA1001", "nombre": "A", "creditos": 3, "area": "x", "semestre": 3},
        {
            "codigo": "EJA1002",
            "nombre": "B",
            "creditos": 3,
            "area": "x",
            "semestre": 2,
            "pre": ["EJA1001"],
        },
    ]
    reporte = cargar(conectar(":memory:"), _malla(cursos=cursos))
    assert len(reporte.avisos) == 1
    assert "no va antes" in reporte.avisos[0]


def test_recargar_es_idempotente_y_conserva_las_metricas(
    con: sqlite3.Connection, ejemplo: Path
) -> None:
    con.execute(
        "INSERT INTO consultas (fecha, tipo, resultado, latencia_ms) "
        "VALUES ('2099-01-01', 'plan', 'plan_valido', 120)"
    )
    con.commit()
    cargar(con, leer_malla(ejemplo / "malla.yaml"), leer_historial(ejemplo / "historial.yaml"))
    assert _contar(con, "cursos") == 13
    assert _contar(con, "prerrequisitos") == 13
    assert _contar(con, "consultas") == 1


def test_la_base_rechaza_lo_que_el_esquema_prohibe() -> None:
    con = conectar(":memory:")
    crear_esquema(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO prerrequisitos (curso, requisito, tipo) VALUES ('X', 'Y', 'pre')")
        con.commit()


def test_cli_carga_y_falla_con_codigo_1(
    tmp_path: Path, ejemplo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "prueba.db"
    args = ["--malla", str(ejemplo / "malla.yaml"), "--db", str(db)]
    assert main([*args, "--historial", str(ejemplo / "historial.yaml")]) == 0
    assert "Cursos: 13 (39 créditos)" in capsys.readouterr().out

    mala = tmp_path / "mala.yaml"
    mala.write_text("carrera: P\ncursos: []\n")
    assert main(["--malla", str(mala), "--db", str(db)]) == 1
    assert "La base no se modificó" in capsys.readouterr().err


# ------------------------------------------------------------------ ciclos con correquisitos


def _cursos_con_requisitos(*cursos: tuple[str, list[str], list[str]]) -> list[dict[str, Any]]:
    return [
        {"codigo": c, "nombre": c, "creditos": 3, "area": "x", "semestre": 1, "pre": pre, "co": co}
        for c, pre, co in cursos
    ]


def test_rechaza_ciclo_mixto_a_pre_de_b_y_b_co_de_a() -> None:
    # A es prerrequisito de B y B es correquisito de A: A exige B a la vez o antes,
    # y B exige A estrictamente antes. No hay orden que lo cumpla.
    cursos = _cursos_con_requisitos(("EJA1001", [], ["EJA1002"]), ("EJA1002", ["EJA1001"], []))
    with pytest.raises(ErrorDeCarga) as info:
        cargar(conectar(":memory:"), _malla(cursos=cursos))
    assert info.value.errores == [
        "ciclo de prerrequisitos: EJA1002 → EJA1001 → EJA1002 (pasa por correquisitos)"
    ]


def test_rechaza_ciclo_mixto_largo_con_una_sola_arista_pre() -> None:
    cursos = _cursos_con_requisitos(
        ("EJA1001", [], ["EJA1002"]),
        ("EJA1002", [], ["EJA1003"]),
        ("EJA1003", ["EJA1001"], []),
    )
    with pytest.raises(ErrorDeCarga) as info:
        cargar(conectar(":memory:"), _malla(cursos=cursos))
    assert info.value.errores == [
        "ciclo de prerrequisitos: EJA1003 → EJA1001 → EJA1002 → EJA1003 (pasa por correquisitos)"
    ]


def test_ciclo_solo_de_correquisitos_sigue_permitido() -> None:
    cursos = _cursos_con_requisitos(
        ("EJA1001", [], ["EJA1002"]),
        ("EJA1002", [], ["EJA1001"]),
        ("EJA1003", ["EJA1001"], []),  # una arista pre que no cierra ningún ciclo
    )
    reporte = cargar(conectar(":memory:"), _malla(cursos=cursos))
    assert (reporte.prerrequisitos, reporte.correquisitos) == (1, 2)


def test_ciclo_solo_de_pre_conserva_su_mensaje() -> None:
    cursos = _cursos_con_requisitos(("EJA1001", ["EJA1002"], []), ("EJA1002", ["EJA1001"], []))
    with pytest.raises(ErrorDeCarga) as info:
        cargar(conectar(":memory:"), _malla(cursos=cursos))
    assert info.value.errores == ["ciclo de prerrequisitos: EJA1001 → EJA1002 → EJA1001"]


# ------------------------------------------------------------------ formato del código


@pytest.mark.parametrize(
    ("escrito", "canonico"),
    [
        ("PAS 4000", "PAS4000"),  # con espacio, como en la malla y el manual
        ("ADM-1001", "ADM1001"),  # con guion, como en el manual
        ("rel2000e", "REL2000E"),  # sufijo E: curso dictado en inglés (manual, pág. 38)
        ("  esp1001 ", "ESP1001"),
    ],
)
def test_normaliza_espacio_guion_y_mayusculas(escrito: str, canonico: str) -> None:
    curso = {"codigo": escrito, "nombre": "A", "creditos": 3, "area": "x", "semestre": 1}
    assert _malla(cursos=[curso]).cursos[0].codigo == canonico


@pytest.mark.parametrize(
    "escrito",
    [
        "AB1001",  # dos letras
        "ABCD1001",  # cuatro letras
        "ABC1001X",  # sufijo que no es E
        "ABC10011",  # cinco dígitos
        "ABC100",  # tres dígitos
        "ABC--1001",  # dos separadores
        "ABC - 1001",
        "ABC_1001",
        "A BC1001",  # separador fuera de sitio
        "ABC1001-E",
    ],
)
def test_rechaza_codigos_con_otra_forma(escrito: str) -> None:
    curso = {"codigo": escrito, "nombre": "A", "creditos": 3, "area": "x", "semestre": 1}
    with pytest.raises(ValidationError, match="código de curso mal formado"):
        _malla(cursos=[curso])


# ------------------------------------------------------------------ dígitos ASCII y una línea

ANCHO_COMPLETO = "２０９９１０"  # los mismos seis dígitos de 209910, en otro bloque Unicode
ARABIGOS = "٢٠٩٩١٠"


@pytest.mark.parametrize("periodo", [ANCHO_COMPLETO, ARABIGOS, "20991０", "209910\n", " 209910"])
def test_el_periodo_solo_acepta_digitos_ascii(periodo: str) -> None:
    assert TypeAdapter(Periodo).validate_python("209910") == "209910"
    with pytest.raises(ValidationError):
        TypeAdapter(Periodo).validate_python(periodo)


@pytest.mark.parametrize("codigo", ["EJM１００２", "EJM١٠٠٢", "ejm 100２", "EJM-١٠٠٢E"])
def test_el_codigo_solo_acepta_digitos_ascii(codigo: str) -> None:
    with pytest.raises(ValidationError, match="mal formado"):
        TypeAdapter(Codigo).validate_python(codigo)


@pytest.mark.parametrize("hora", ["０８:30", "08:٣٠"])
def test_la_hora_solo_acepta_digitos_ascii(hora: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Hora).validate_python(hora)


@pytest.mark.parametrize(
    "texto",
    [
        "sin viernes\nPreferencias verificadas:",
        "una\rdos",
        "tab\tulador",
        "nulo\x00",
        "escape\x1b[31m",
        "separador\u2028de línea",
        "párrafo\u2029nuevo",
        "al\u202erevés",
        "ancho\u200bcero",
    ],
)
def test_el_texto_no_admite_saltos_ni_caracteres_de_control(texto: str) -> None:
    with pytest.raises(ValidationError, match="una sola línea"):
        TypeAdapter(Texto).validate_python(texto)


def test_el_texto_conserva_tildes_y_recorta_los_bordes() -> None:
    assert TypeAdapter(Texto).validate_python("  Cálculo II — ¿niño?\n") == "Cálculo II — ¿niño?"
