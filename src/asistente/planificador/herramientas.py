"""Herramientas deterministas del planificador. Aquí no se llama a ningún LLM.

Cada herramienta recibe la conexión y un modelo Pydantic, y devuelve otro. Solo leen la base.
`ejecutar` las llama por nombre con argumentos sin validar y nunca lanza por una entrada mala.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Callable
from itertools import combinations
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from asistente.etl.modelos import Codigo
from asistente.planificador.modelos import (
    BloqueHorario,
    ConsultaCurso,
    ConsultaHorarios,
    ConsultaPrerrequisitos,
    CursoHabilitado,
    CursoInfo,
    ErrorEntrada,
    Historial,
    Motivo,
    Plan,
    Requisito,
    RequisitoEnCadena,
    RespuestaHerramienta,
    ResultadoBusqueda,
    ResultadoHabilitados,
    ResultadoHorarios,
    ResultadoPrerrequisitos,
    ResultadoVerificacion,
    ResumenSemestre,
    SeccionInfo,
    TipoMotivo,
)

_CODIGO = TypeAdapter(Codigo)


def _plano(texto: str) -> str:
    """Minúsculas y sin tildes, para comparar nombres."""
    sin_tildes = unicodedata.normalize("NFD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).casefold()


def _requisitos(con: sqlite3.Connection) -> dict[str, list[tuple[str, str]]]:
    """curso → [(requisito, tipo)], en orden estable."""
    por_curso: dict[str, list[tuple[str, str]]] = {}
    for fila in con.execute(
        "SELECT curso, requisito, tipo FROM prerrequisitos ORDER BY curso, requisito"
    ):
        por_curso.setdefault(fila["curso"], []).append((fila["requisito"], fila["tipo"]))
    return por_curso


# ------------------------------------------------------------------ buscar_curso


def buscar_curso(con: sqlite3.Connection, consulta: ConsultaCurso) -> ResultadoBusqueda:
    """Busca por código exacto o, si no lo es, por palabras del nombre o del código."""
    filas = con.execute(
        """
        SELECT c.codigo, c.nombre, c.creditos, c.area,
               MIN(m.semestre_sugerido) AS semestre_sugerido, MIN(m.tipo) AS tipo
        FROM cursos AS c
        LEFT JOIN malla AS m ON m.curso = c.codigo
        GROUP BY c.codigo
        ORDER BY c.codigo
        """
    ).fetchall()

    try:
        codigo = _CODIGO.validate_python(consulta.texto)
    except ValidationError:
        codigo = None
    encontradas = [f for f in filas if f["codigo"] == codigo]
    if not encontradas:
        palabras = _plano(consulta.texto).split()
        encontradas = [
            f for f in filas if all(p in _plano(f"{f['codigo']} {f['nombre']}") for p in palabras)
        ]
    return ResultadoBusqueda(
        cursos=tuple(
            CursoInfo(
                codigo=f["codigo"],
                nombre=f["nombre"],
                creditos=f["creditos"],
                area=f["area"],
                semestre_sugerido=f["semestre_sugerido"],
                tipo=f["tipo"],
            )
            for f in encontradas[: consulta.limite]
        ),
        total=len(encontradas),
    )


# ------------------------------------------------------------------ prerrequisitos


def prerrequisitos(
    con: sqlite3.Connection, consulta: ConsultaPrerrequisitos
) -> ResultadoPrerrequisitos:
    """Requisitos directos (con su tipo) y la cadena completa con su profundidad."""
    existe = con.execute("SELECT 1 FROM cursos WHERE codigo = ?", (consulta.curso,)).fetchone()
    directos = con.execute(
        """
        SELECT p.requisito, c.nombre, p.tipo
        FROM prerrequisitos AS p JOIN cursos AS c ON c.codigo = p.requisito
        WHERE p.curso = ? ORDER BY p.requisito
        """,
        (consulta.curso,),
    ).fetchall()
    cadena = con.execute(
        """
        SELECT v.requisito, c.nombre, v.profundidad
        FROM v_cadena_prerrequisitos AS v JOIN cursos AS c ON c.codigo = v.requisito
        WHERE v.curso = ? ORDER BY v.profundidad, v.requisito
        """,
        (consulta.curso,),
    ).fetchall()
    return ResultadoPrerrequisitos(
        curso=consulta.curso,
        encontrado=existe is not None,
        directos=tuple(
            Requisito(codigo=f["requisito"], nombre=f["nombre"], tipo=f["tipo"]) for f in directos
        ),
        cadena=tuple(
            RequisitoEnCadena(
                codigo=f["requisito"], nombre=f["nombre"], profundidad=f["profundidad"]
            )
            for f in cadena
        ),
    )


# ------------------------------------------------------------------ cursos_habilitados


def cursos_habilitados(con: sqlite3.Connection, historial: Historial) -> ResultadoHabilitados:
    """Cursos que el historial permite tomar: misma regla que la vista `v_cursos_habilitados`.

    Ni aprobados ni en curso, y con todos sus `pre` aprobados. Los correquisitos no bloquean:
    se devuelven los pendientes para que el plan los incluya en el mismo semestre.
    """
    aprobados = set(historial.aprobados)
    excluidos = aprobados | set(historial.cursando)
    requisitos = _requisitos(con)
    cursos: list[CursoHabilitado] = []
    existentes: set[str] = set()
    for fila in con.execute("SELECT codigo, nombre, creditos, area FROM cursos ORDER BY codigo"):
        codigo = fila["codigo"]
        existentes.add(codigo)
        del_curso = requisitos.get(codigo, [])
        if codigo in excluidos or any(t == "pre" and r not in aprobados for r, t in del_curso):
            continue
        cursos.append(
            CursoHabilitado(
                codigo=codigo,
                nombre=fila["nombre"],
                creditos=fila["creditos"],
                area=fila["area"],
                correquisitos_pendientes=tuple(
                    r for r, t in del_curso if t == "co" and r not in aprobados
                ),
            )
        )
    return ResultadoHabilitados(
        cursos=tuple(cursos),
        desconocidos=tuple(sorted(excluidos - existentes)),
    )


# ------------------------------------------------------------------ horarios


def _secciones(con: sqlite3.Connection, curso: str, periodo: str) -> dict[str, SeccionInfo]:
    """NRC → sección, con sus bloques ordenados por día y hora."""
    orden_dia = "CASE h.dia WHEN 'LU' THEN 1 WHEN 'MA' THEN 2 WHEN 'MI' THEN 3 " + (
        "WHEN 'JU' THEN 4 WHEN 'VI' THEN 5 ELSE 6 END"
    )
    bloques: dict[str, list[BloqueHorario]] = {}
    for fila in con.execute(
        f"""
        SELECT s.nrc, h.dia, h.inicio, h.fin
        FROM secciones AS s JOIN horarios AS h ON h.seccion_id = s.id
        WHERE s.curso = ? AND s.periodo = ?
        ORDER BY s.nrc, {orden_dia}, h.inicio
        """,
        (curso, periodo),
    ):
        bloques.setdefault(fila["nrc"], []).append(
            BloqueHorario(dia=fila["dia"], inicio=fila["inicio"], fin=fila["fin"])
        )
    return {nrc: SeccionInfo(nrc=nrc, bloques=tuple(b)) for nrc, b in bloques.items()}


def hay_oferta(con: sqlite3.Connection, periodo: str) -> bool:
    """El periodo tiene al menos una sección cargada, de cualquier curso."""
    fila = con.execute("SELECT 1 FROM secciones WHERE periodo = ? LIMIT 1", (periodo,)).fetchone()
    return fila is not None


def horarios(con: sqlite3.Connection, consulta: ConsultaHorarios) -> ResultadoHorarios:
    """Secciones de un curso en un periodo. Vacío si no hay oferta cargada."""
    existe = con.execute("SELECT 1 FROM cursos WHERE codigo = ?", (consulta.curso,)).fetchone()
    return ResultadoHorarios(
        curso=consulta.curso,
        periodo=consulta.periodo,
        curso_existe=existe is not None,
        secciones=tuple(_secciones(con, consulta.curso, consulta.periodo).values()),
    )


# ------------------------------------------------------------------ verificar_plan


def _chocan(a: BloqueHorario, b: BloqueHorario) -> bool:
    """Intervalos semiabiertos: terminar a las 10:00 y empezar a las 10:00 no es choque."""
    return a.dia == b.dia and a.inicio < b.fin and b.inicio < a.fin


def _texto_bloque(bloque: BloqueHorario) -> str:
    return f"{bloque.dia} {bloque.inicio}-{bloque.fin}"


def verificar_plan(con: sqlite3.Connection, plan: Plan) -> ResultadoVerificacion:
    """Comprueba el plan entero y devuelve todos los motivos de rechazo.

    Orden de los motivos: primero los de `aprobados`; después, semestre por semestre, la
    estructura (curso inexistente, repetido, ya aprobado, periodo), los requisitos, los
    créditos y el horario, cada grupo en el orden en que el plan lista los cursos.

    Si el plan trae `periodo` (el que pidió el usuario), un semestre de otro periodo o un
    periodo sin oferta cargada se rechazan. Un curso con secciones en su periodo tiene que
    traer NRC: omitirlo no sirve para esquivar un choque.

    Un curso inexistente, repetido o ya aprobado se reporta una vez y queda fuera del resto
    de las comprobaciones. Un curso del plan cuenta como planificado para los semestres
    siguientes aunque a él mismo le falte un requisito: así un fallo no arrastra otros.
    """
    creditos = {f["codigo"]: int(f["creditos"]) for f in con.execute("SELECT * FROM cursos")}
    requisitos = _requisitos(con)
    aprobados = set(plan.aprobados)
    motivos: list[Motivo] = []

    def motivo(
        tipo: TipoMotivo, periodo: str | None, cursos: tuple[str, ...], detalle: str, texto: str
    ) -> None:
        prefijo = "Aprobados" if periodo is None else periodo
        motivos.append(
            Motivo(
                tipo=tipo,
                periodo=periodo,
                cursos=cursos,
                detalle=detalle,
                mensaje=f"{prefijo}: {texto}",
            )
        )

    for codigo in sorted(aprobados - creditos.keys()):
        motivo("curso_inexistente", None, (codigo,), "", f"el curso {codigo} no existe en la base.")

    # Dónde aparece por primera vez cada curso que existe: índice del semestre.
    primera_vez: dict[str, int] = {}
    for indice, semestre in enumerate(plan.semestres):
        for curso in semestre.cursos:
            if curso.codigo in creditos:
                primera_vez.setdefault(curso.codigo, indice)

    resumenes: list[ResumenSemestre] = []
    periodo_anterior: str | None = None
    for indice, semestre in enumerate(plan.semestres):
        periodo = semestre.periodo

        # --- estructura
        validos: list[tuple[str, str | None]] = []  # (código, NRC)
        vistos: set[str] = set()
        for curso in semestre.cursos:
            codigo = curso.codigo
            if codigo not in creditos:
                motivo(
                    "curso_inexistente",
                    periodo,
                    (codigo,),
                    "",
                    f"el curso {codigo} no existe en la base.",
                )
            elif codigo in vistos:
                motivo(
                    "curso_repetido",
                    periodo,
                    (codigo,),
                    periodo,
                    f"{codigo} está más de una vez en el mismo semestre.",
                )
            elif primera_vez[codigo] < indice:
                anterior = plan.semestres[primera_vez[codigo]].periodo
                motivo(
                    "curso_repetido",
                    periodo,
                    (codigo,),
                    anterior,
                    f"{codigo} ya está en el plan ({anterior}).",
                )
            elif codigo in aprobados:
                motivo("ya_aprobado", periodo, (codigo,), "", f"{codigo} ya está aprobado.")
            else:
                validos.append((codigo, curso.nrc))
            vistos.add(codigo)
        if periodo_anterior is not None and periodo <= periodo_anterior:
            motivo(
                "periodo_invalido",
                periodo,
                (),
                periodo_anterior,
                f"el periodo no es posterior al del semestre anterior ({periodo_anterior}).",
            )
        periodo_anterior = periodo
        if plan.periodo is not None:
            if periodo != plan.periodo:
                motivo(
                    "periodo_distinto",
                    periodo,
                    (),
                    plan.periodo,
                    f"el plan se pidió para {plan.periodo}, no para este periodo.",
                )
            elif not hay_oferta(con, periodo):
                motivo(
                    "periodo_sin_oferta",
                    periodo,
                    (),
                    "",
                    "no hay oferta cargada para ese periodo.",
                )

        # --- requisitos
        for codigo, _ in validos:
            for requisito, tipo in requisitos.get(codigo, []):
                donde = primera_vez.get(requisito)
                if requisito in aprobados or (donde is not None and donde < indice):
                    continue
                if tipo == "co" and donde == indice:
                    continue
                if donde is None:
                    falta = (
                        "que no está aprobado ni en un semestre anterior del plan."
                        if tipo == "pre"
                        else "no está en el plan."
                    )
                elif donde == indice:
                    falta = "que está en el mismo semestre."
                else:
                    despues = plan.semestres[donde].periodo
                    falta = (
                        f"que el plan pone después ({despues})."
                        if tipo == "pre"
                        else f"el plan lo pone después ({despues})."
                    )
                if tipo == "pre":
                    motivo(
                        "prerrequisito_faltante",
                        periodo,
                        (codigo,),
                        requisito,
                        f"{codigo} exige haber aprobado antes {requisito}, {falta}",
                    )
                else:
                    motivo(
                        "correquisito_faltante",
                        periodo,
                        (codigo,),
                        requisito,
                        f"{codigo} exige {requisito} aprobado o en el mismo semestre; {falta}",
                    )

        # --- créditos
        total = sum(creditos[codigo] for codigo, _ in validos)
        if total > plan.creditos_max:
            motivo(
                "creditos_excedidos",
                periodo,
                tuple(codigo for codigo, _ in validos),
                f"{total} > {plan.creditos_max}",
                f"{total} créditos; el máximo es {plan.creditos_max}.",
            )

        # --- horario
        con_seccion: list[tuple[str, SeccionInfo]] = []
        for codigo, nrc in validos:
            secciones = _secciones(con, codigo, periodo)
            if nrc is None:
                if secciones:
                    disponibles = ", ".join(secciones)
                    motivo(
                        "nrc_faltante",
                        periodo,
                        (codigo,),
                        disponibles,
                        f"{codigo} tiene secciones en ese periodo (NRC {disponibles}): "
                        "hay que elegir una.",
                    )
                continue
            seccion = secciones.get(nrc)
            if seccion is None:
                motivo(
                    "seccion_inexistente",
                    periodo,
                    (codigo,),
                    nrc,
                    f"no hay sección con NRC {nrc} de {codigo} en ese periodo.",
                )
            else:
                con_seccion.append((codigo, seccion))
        for (curso_a, seccion_a), (curso_b, seccion_b) in combinations(con_seccion, 2):
            choques = [
                (a, b) for a in seccion_a.bloques for b in seccion_b.bloques if _chocan(a, b)
            ]
            if choques:
                motivo(
                    "choque_horario",
                    periodo,
                    (curso_a, curso_b),
                    "; ".join(f"{_texto_bloque(a)} / {_texto_bloque(b)}" for a, b in choques),
                    f"{curso_a} (NRC {seccion_a.nrc}) y {curso_b} (NRC {seccion_b.nrc}) chocan: "
                    + "; ".join(f"{_texto_bloque(a)} con {_texto_bloque(b)}" for a, b in choques)
                    + ".",
                )

        resumenes.append(
            ResumenSemestre(
                periodo=periodo,
                creditos=total,
                horario_verificado=bool(validos) and len(con_seccion) == len(validos),
                sin_nrc=tuple(codigo for codigo, nrc in validos if nrc is None),
            )
        )

    return ResultadoVerificacion(
        valido=not motivos,
        motivos=tuple(motivos),
        semestres=tuple(resumenes),
        mensaje=_mensaje(motivos, resumenes),
    )


def _mensaje(motivos: list[Motivo], resumenes: list[ResumenSemestre]) -> str:
    """Texto para el usuario. Un horario sin verificar siempre se dice."""
    if motivos:
        partes = [f"Plan rechazado ({len(motivos)} motivo{'s' if len(motivos) != 1 else ''})."]
        partes += [m.mensaje for m in motivos]
    else:
        partes = ["Plan válido."]
    for resumen in resumenes:
        if resumen.horario_verificado:
            horario = "horario verificado"
        elif resumen.sin_nrc:
            horario = f"horario sin verificar (sin NRC: {', '.join(resumen.sin_nrc)})"
        else:
            horario = "horario sin verificar"
        partes.append(f"{resumen.periodo}: {resumen.creditos} créditos, {horario}.")
    return " ".join(partes)


# ------------------------------------------------------------------ envoltorio

_HERRAMIENTAS: dict[str, tuple[type[BaseModel], Callable[[sqlite3.Connection, Any], BaseModel]]] = {
    "buscar_curso": (ConsultaCurso, buscar_curso),
    "cursos_habilitados": (Historial, cursos_habilitados),
    "horarios": (ConsultaHorarios, horarios),
    "prerrequisitos": (ConsultaPrerrequisitos, prerrequisitos),
    "verificar_plan": (Plan, verificar_plan),
}

_MENSAJES = {
    "missing": "falta este campo",
    "extra_forbidden": "campo desconocido",
    "string_pattern_mismatch": "no tiene el formato esperado",
}


def _error_de_entrada(error: Any) -> ErrorEntrada:
    tipo = error["type"]
    if tipo == "value_error":
        mensaje = str(error["msg"]).removeprefix("Value error, ")
    else:
        mensaje = _MENSAJES.get(tipo, str(error["msg"]))
    valor = error["input"]
    return ErrorEntrada(
        campo=".".join(str(parte) for parte in error["loc"]),
        mensaje=mensaje,
        valor=str(valor) if isinstance(valor, (str, int, float)) else None,
    )


def _sin_derivados(errores: list[Any]) -> list[Any]:
    """Quita los "lista demasiado corta" que solo repiten el fallo de un elemento.

    Cuando un elemento de una tupla es inválido, Pydantic lo descarta y además se queja de
    que la tupla quedó vacía. Ese segundo error no le dice nada nuevo a quien llamó.
    """
    return [
        e
        for e in errores
        if not (
            e["type"] == "too_short"
            and any(
                len(o["loc"]) > len(e["loc"]) and o["loc"][: len(e["loc"])] == e["loc"]
                for o in errores
            )
        )
    ]


def ejecutar(
    con: sqlite3.Connection, herramienta: str, argumentos: dict[str, Any]
) -> RespuestaHerramienta:
    """Llama una herramienta por nombre. Una entrada mal formada vuelve como datos, no lanza."""
    if herramienta not in _HERRAMIENTAS:
        disponibles = ", ".join(sorted(_HERRAMIENTAS))
        error = ErrorEntrada(
            campo="herramienta",
            mensaje=f"no existe la herramienta '{herramienta}'; las disponibles son: {disponibles}",
            valor=herramienta,
        )
        return RespuestaHerramienta(
            herramienta=herramienta, ok=False, resultado=None, errores=(error,)
        )
    modelo, funcion = _HERRAMIENTAS[herramienta]
    try:
        entrada = modelo.model_validate(argumentos)
    except ValidationError as exc:
        return RespuestaHerramienta(
            herramienta=herramienta,
            ok=False,
            resultado=None,
            errores=tuple(_error_de_entrada(e) for e in _sin_derivados(exc.errors())),
        )
    return RespuestaHerramienta(
        herramienta=herramienta,
        ok=True,
        resultado=funcion(con, entrada).model_dump(mode="json"),
        errores=(),
    )
