"""Evaluación del agente planificador sobre evals/escenarios_agente.yaml.

  python -m asistente.evaluacion.agente              (cliente de guion: 0 llamadas a Gemini)

Cada escenario trae el guion que sigue un cliente falso y el resultado esperado. Por eso
todas las cifras de esta corrida miden el bucle y el guion, no a Gemini.

La corrida se guarda en evals/corridas/<nombre>.jsonl (un escenario por línea) con su
.resumen.json. Si ya existe no se pisa: hay que pedirlo con --rehacer.

El nombre por defecto es `agente-guion-v2`: la corrida `agente-guion` es la de los escenarios
v1 (ver docs/evaluacion.md) y se conserva al lado.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from asistente.agente import (
    MotivoAbstencion,
    Paso,
    ResultadoAgente,
    Solicitud,
    TipoPregunta,
    planificar,
)
from asistente.db import RAIZ, conectar
from asistente.etl import cargar, leer_historial, leer_malla
from asistente.llm import ClienteDeGuion
from asistente.planificador import Plan, verificar_plan

RUTA_ESCENARIOS = RAIZ / "evals" / "escenarios_agente.yaml"
DIR_CORRIDAS = RAIZ / "evals" / "corridas"
DIR_EJEMPLO = RAIZ / "datos" / "ejemplo"

NOTA = "Mide el bucle y el guion, no a Gemini: el LLM es un cliente falso que sigue un guion."


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Esperado(_Modelo):
    estado: Literal["plan", "pregunta", "abstencion", "error_entrada"]
    pregunta: TipoPregunta | None = None
    motivo: MotivoAbstencion | None = None
    vueltas: int = Field(ge=0)
    llamadas: int = Field(ge=0)
    horario_verificado: bool | None = None
    plan: dict[str, tuple[str, ...]] | None = None  # periodo → "CÓDIGO/NRC" o "CÓDIGO"
    pasos: tuple[str, ...]
    inventados: tuple[str, ...]  # únicos por escenario
    intentos_inventados: int = Field(ge=0)
    preferencias_no_verificadas: tuple[str, ...] = ()
    pedidos_fuera: dict[str, tuple[str, ...]] = {}  # código pedido que no entró → tipos de motivo


class Escenario(_Modelo):
    id: str
    categoria: str
    descripcion: str
    solicitud: Solicitud
    guion: tuple[dict[str, Any] | str, ...]  # una acción, o texto suelto que no es JSON
    esperado: Esperado


def cargar_escenarios(ruta: Path = RUTA_ESCENARIOS) -> list[Escenario]:
    escenarios = TypeAdapter(list[Escenario]).validate_python(
        yaml.safe_load(ruta.read_text(encoding="utf-8"))
    )
    ids = [e.id for e in escenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("hay ids de escenario repetidos")
    return escenarios


def cliente_de_guion(guion: tuple[dict[str, Any] | str, ...]) -> ClienteDeGuion:
    return ClienteDeGuion(
        r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in guion
    )


def base_de_ejemplo() -> sqlite3.Connection:
    """Base en memoria con los datos ficticios versionados: la corrida no depende de la local."""
    con = conectar(":memory:")
    cargar(
        con,
        leer_malla(DIR_EJEMPLO / "malla.yaml"),
        leer_historial(DIR_EJEMPLO / "historial.yaml"),
    )
    return con


def _etiqueta(paso: Paso) -> str:
    """Qué hizo el código en una llamada, con el vocabulario de `esperado.pasos`."""
    if paso.tipo == "herramienta":
        return paso.herramienta or ""
    return "verificar_plan" if paso.tipo == "propuesta" else paso.tipo


def _plan_plano(plan: Plan | None) -> dict[str, list[str]] | None:
    if plan is None:
        return None
    return {
        s.periodo: [c.codigo if c.nrc is None else f"{c.codigo}/{c.nrc}" for c in s.cursos]
        for s in plan.semestres
    }


def reverificar(con: sqlite3.Connection, solicitud: Solicitud, plan: Plan) -> bool:
    """Los cursos mostrados, contra las restricciones de la solicitud y no las del propio plan.

    Si el bucle dejara pasar un periodo, un límite o unos aprobados que no son los de la
    solicitud, el plan mostrado se verificaría bien a sí mismo; así no.
    """
    if solicitud.creditos_max is None or solicitud.periodo is None:
        return False  # con esa solicitud no debió mostrarse ningún plan
    pedido = Plan(
        aprobados=solicitud.aprobados,
        creditos_max=solicitud.creditos_max,
        periodo=solicitud.periodo,
        semestres=plan.semestres,
    )
    return verificar_plan(con, pedido).valido


def correr_escenario(con: sqlite3.Connection, escenario: Escenario) -> dict[str, Any]:
    """Corre el guion y compara, campo por campo, con lo esperado."""
    llm = cliente_de_guion(escenario.guion)
    resultado: ResultadoAgente = planificar(con, llm, escenario.solicitud)
    esperado = escenario.esperado

    pasos = [_etiqueta(p) for p in resultado.traza]
    intentos = [codigo for p in resultado.traza for codigo in p.inexistentes]
    plan = _plan_plano(resultado.plan)
    # Comprobación independiente del bucle: el plan mostrado, otra vez contra la base y
    # contra la solicitud.
    reverificado = (
        None if resultado.plan is None else reverificar(con, escenario.solicitud, resultado.plan)
    )

    obtenido: dict[str, Any] = {
        "estado": resultado.estado,
        "pregunta": resultado.pregunta,
        "motivo": resultado.motivo,
        "vueltas": resultado.vueltas,
        "llamadas": resultado.llamadas_llm,
        "horario_verificado": resultado.horario_verificado,
        "plan": plan,
        "pasos": pasos,
        "inventados": sorted(set(intentos)),
        "intentos_inventados": len(intentos),
        "preferencias_no_verificadas": list(resultado.preferencias_no_verificadas),
        "pedidos_fuera": {p.codigo: list(p.motivos) for p in resultado.pedidos_fuera},
    }
    previsto = esperado.model_dump(mode="json")
    previsto["inventados"] = sorted(previsto["inventados"])
    if previsto["plan"] is not None:
        previsto["plan"] = {k: list(v) for k, v in previsto["plan"].items()}
    diferencias = [campo for campo in obtenido if obtenido[campo] != previsto[campo]]

    return {
        "id": escenario.id,
        "categoria": escenario.categoria,
        **obtenido,
        "esperado": previsto,
        "cumple_lo_esperado": not diferencias,
        "diferencias": diferencias,
        "pasos_correctos": sum(a == b for a, b in zip(pasos, esperado.pasos, strict=False)),
        "pasos_esperados": len(esperado.pasos),
        "plan_reverificado": reverificado,
        "llamadas_del_guion_sin_pedir": len(escenario.guion) - len(llm.llamadas),
        "texto": resultado.texto,
    }


def resumir(registros: list[dict[str, Any]]) -> dict[str, Any]:
    con_plan = [r for r in registros if r["estado"] == "plan"]
    return {
        "nota": NOTA,
        "llamadas_a_gemini": 0,
        "escenarios": len(registros),
        "cumplen_lo_esperado": sum(r["cumple_lo_esperado"] for r in registros),
        "no_cumplen": {r["id"]: r["diferencias"] for r in registros if not r["cumple_lo_esperado"]},
        "terminan_con_plan_valido": sum(r["plan_reverificado"] is True for r in con_plan),
        "planes_mostrados": len(con_plan),
        # Meta: 0. Es la garantía, medida aparte del bucle.
        "planes_mostrados_que_no_pasan_la_reverificacion": sum(
            r["plan_reverificado"] is not True for r in con_plan
        ),
        "planes_con_horario_sin_verificar": sum(not r["horario_verificado"] for r in con_plan),
        "terminan_en_pregunta": dict(
            Counter(r["pregunta"] for r in registros if r["estado"] == "pregunta")
        ),
        "terminan_en_abstencion": dict(
            Counter(r["motivo"] for r in registros if r["estado"] == "abstencion")
        ),
        # Correcciones usadas por los escenarios que terminan con plan.
        "vueltas_de_los_que_terminan_con_plan": {
            str(v): n for v, n in sorted(Counter(r["vueltas"] for r in con_plan).items())
        },
        "pasos_con_la_herramienta_esperada": sum(r["pasos_correctos"] for r in registros),
        "pasos_esperados": sum(r["pasos_esperados"] for r in registros),
        "pasos_dados": sum(len(r["pasos"]) for r in registros),
        "cursos_inventados_atrapados": sum(len(r["inventados"]) for r in registros),
        "cursos_inventados_esperados": sum(len(r["esperado"]["inventados"]) for r in registros),
        "intentos_con_curso_inventado": sum(r["intentos_inventados"] for r in registros),
        "llamadas_al_cliente_de_guion": sum(r["llamadas"] for r in registros),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m asistente.evaluacion.agente", description=__doc__
    )
    parser.add_argument("--nombre", default="agente-guion-v2")
    parser.add_argument("--escenarios", type=Path, default=RUTA_ESCENARIOS)
    parser.add_argument("--corridas", type=Path, default=DIR_CORRIDAS)
    parser.add_argument("--rehacer", action="store_true", help="pisar una corrida ya guardada")
    args = parser.parse_args(argv)

    ruta = args.corridas / f"{args.nombre}.jsonl"
    if ruta.exists() and not args.rehacer:
        print(
            f"{ruta} ya existe y no se pisa. Si de verdad hay que repetir la corrida, "
            "usa --rehacer y anótalo en docs/evaluacion.md.",
            file=sys.stderr,
        )
        return 2

    escenarios = cargar_escenarios(args.escenarios)
    print(f"{len(escenarios)} escenarios con cliente de guion: 0 llamadas a Gemini.")
    con = base_de_ejemplo()
    try:
        registros = [correr_escenario(con, e) for e in escenarios]
    finally:
        con.close()

    resumen = {
        "escenarios_sha256": hashlib.sha256(args.escenarios.read_bytes()).hexdigest(),
        **resumir(registros),
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in registros), encoding="utf-8"
    )
    ruta.with_suffix(".resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(resumen, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
