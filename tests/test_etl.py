from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from asistente.db import conectar, crear_esquema
from asistente.etl import ErrorDeCarga, cargar, leer_historial, leer_malla
from asistente.etl.__main__ import main
from asistente.etl.modelos import ArchivoMalla


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
    assert reporte.secciones == 6
    assert reporte.registros_historial == 7
    assert len(reporte.cursos_sin_seccion) == 9
    assert reporte.avisos == ()
    assert _contar(con, "cursos") == 13
    assert _contar(con, "horarios") == 12


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
