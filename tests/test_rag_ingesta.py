from __future__ import annotations

import sqlite3

from asistente.db import conectar
from asistente.rag.ingesta import (
    Fragmento,
    fragmentar_calendario,
    fragmentar_manual,
    guardar,
    leer_indice,
)
from asistente.rag.texto import normalizar, numeros, unir_lineas

PUNTOS = "." * 40

# Un manual en miniatura con la forma del real: índice con puntos guía, número de
# página suelto arriba, títulos partidos con guion y palabras cortadas.
MANUAL = [
    "\n\nMANUAL DE PRUEBA\n",
    f"Índice\nSECCIÓN I {PUNTOS} 3\nPOLÍTICAS ACADÉMICAS {PUNTOS} 3\n"
    f"PRERREQUISITOS O CORREQUISITOS DE\nMATERIAS {PUNTOS} 3\nRETIRO DE MATERIAS {PUNTOS} 4\n",
    "3 \nSECCIÓN I \nPOLÍTICAS ACADÉMI-\nCAS \nPRERREQUISITOS O CORREQUISITOS DE MATE-\nRIAS \n"
    "Las mallas son secuenciales, por lo que muchas mate-\n"
    "rias requieren la aprobación de otras. \n"
    "Ver sección RETIRO DE \nMATERIAS. \n",
    "4 \nEl decano puede autorizar el sobrepaso. \nRETIRO DE MATERIAS \n"
    "Solo es posible retirarse hasta por dos ocasiones de una misma materia. \n",
]

CALENDARIO = [
    "SEGUNDO SEMESTRE 2099-2100\n"
    "Registro de materias\nActividad Desde Hasta\n"
    "Publicación de la oferta lunes, 9 de noviembre de 2099\n"
    "Periodos ordinarios de registro de materias\n"
    "Segundo Semestre lunes, 23 de noviembre de 2099 lunes, 7 de diciembre de 2099\n"
    "Exámenes finales\nActividad Desde Hasta\n"
    "Segundo Semestre viernes, 7 de mayo de 2100 viernes, 14 de mayo de 2100\n"
]


def test_unir_lineas_reune_palabras_cortadas() -> None:
    assert unir_lineas(["los es-", "tudios de gra-", "do  son", "", "largos."]) == (
        "los estudios de grado son largos."
    )
    # Un guion que no corta una palabra se respeta.
    assert unir_lineas(["periodo 2026 -", "2027"]) == "periodo 2026 - 2027"


def test_normalizar_iguala_tipografia_pero_no_contenido() -> None:
    assert normalizar("La  nota “W”\nse asienta") == normalizar('la nota "w" se asienta')
    assert normalizar("matrícula") != normalizar("matricula")
    assert normalizar("2,00") != normalizar("2.00")


def test_numeros() -> None:
    assert numeros("del 4 al 18 de noviembre de 2026, GPA 2,00/4,00") == {
        "4",
        "18",
        "2026",
        "2,00",
        "4,00",
    }


def test_el_indice_reune_titulos_de_varias_lineas() -> None:
    entradas, inicio = leer_indice(MANUAL)
    assert entradas == [
        ("SECCIÓN I", 3),
        ("POLÍTICAS ACADÉMICAS", 3),
        ("PRERREQUISITOS O CORREQUISITOS DE MATERIAS", 3),
        ("RETIRO DE MATERIAS", 4),
    ]
    assert inicio == 2


def test_manual_se_fragmenta_por_titulo_del_indice() -> None:
    resultado = fragmentar_manual(MANUAL, "Manual de prueba")
    assert resultado.titulos_sin_encontrar == []
    assert [(f.seccion, f.pagina) for f in resultado.fragmentos] == [
        ("PRERREQUISITOS O CORREQUISITOS DE MATERIAS", 3),
        ("RETIRO DE MATERIAS", 4),
    ]
    primero, segundo = resultado.fragmentos
    # El apartado sigue en la página siguiente y las palabras cortadas se reúnen.
    assert primero.texto == (
        "PRERREQUISITOS O CORREQUISITOS DE MATERIAS\n"
        "Las mallas son secuenciales, por lo que muchas materias requieren la aprobación de "
        "otras. Ver sección RETIRO DE MATERIAS. El decano puede autorizar el sobrepaso."
    )
    # "RETIRO DE MATERIAS." citado dentro de un párrafo de la página 3 no abre un apartado.
    assert segundo.texto.endswith("dos ocasiones de una misma materia.")
    assert primero.contexto == "Manual de prueba · SECCIÓN I: POLÍTICAS ACADÉMICAS"


def test_un_titulo_que_no_esta_en_el_cuerpo_se_reporta() -> None:
    paginas = [f"Índice\nUNO {PUNTOS} 2\nFANTASMA {PUNTOS} 2\n", "2 \nUNO \n" + "texto. " * 10]
    resultado = fragmentar_manual(paginas)
    assert resultado.titulos_sin_encontrar == ["FANTASMA"]
    assert [f.seccion for f in resultado.fragmentos] == ["UNO"]


def test_un_apartado_largo_se_parte_en_puntos_y_aparte() -> None:
    cuerpo = "".join(f"Oración número {i} de un apartado largo de prueba. \n" for i in range(60))
    resultado = fragmentar_manual([f"Índice\nLARGO {PUNTOS} 2\n", "2 \nLARGO \n" + cuerpo])
    assert len(resultado.fragmentos) > 1
    assert all(
        f.texto.startswith("LARGO\n") and f.texto.endswith(".") for f in resultado.fragmentos
    )
    assert max(len(f.texto) for f in resultado.fragmentos) < 1400


def test_calendario_un_fragmento_por_tabla() -> None:
    resultado = fragmentar_calendario(CALENDARIO, "Calendario de prueba")
    assert [f.seccion for f in resultado.fragmentos] == ["Registro de materias", "Exámenes finales"]
    registro = resultado.fragmentos[0]
    assert "Actividad Desde Hasta" not in registro.texto
    assert "Periodos ordinarios de registro de materias" in registro.texto  # subtítulo: contexto
    assert registro.contexto == "Calendario de prueba"


def _fts(con: sqlite3.Connection, termino: str) -> int:
    fila = con.execute(
        "SELECT COUNT(*) FROM fragmentos_fts WHERE fragmentos_fts MATCH ?", (termino,)
    ).fetchone()
    return int(fila[0])


def test_guardar_reemplaza_el_documento_y_mantiene_el_indice() -> None:
    con = conectar(":memory:")
    guardar(con, "Doc", None, "sha-1", "2099-01-01", [Fragmento("A", 1, "texto sobre matrícula")])
    guardar(con, "Doc", None, "sha-2", "2099-01-02", [Fragmento("A", 1, "texto sobre retiros")])
    assert con.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1
    assert _fts(con, "matricula") == 0
    assert _fts(con, "retiros") == 1
