"""Evaluación del RAG sobre evals/preguntas.yaml.

  python -m asistente.evaluacion.rag --solo-recuperacion     (sin LLM, instantánea)
  python -m asistente.evaluacion.rag --conjunto dev          (con LLM: solo anuncia las llamadas)
  python -m asistente.evaluacion.rag --conjunto dev --confirmar   (las hace)

El conjunto "test" no se usa para ajustar nada: se corre una vez, al final.

Las corridas se guardan caso por caso en evals/corridas/<nombre>.jsonl. Una corrida
con LLM que se interrumpe se retoma con el mismo comando: los casos hechos no se repiten.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from asistente.db import RAIZ, RUTA_DB, conectar
from asistente.llm import INTENTOS, ClienteLLM, ErrorDelLLM, cargar_env, cliente_desde_env
from asistente.rag.busqueda import buscar
from asistente.rag.responder import (
    MAX_INTENTOS,
    SISTEMA,
    Respuesta,
    _leer_borrador,
    _mensaje,
    responder,
)
from asistente.rag.texto import normalizar
from asistente.rag.verificar import ESQUEMA_BORRADOR, verificar

RUTA_PREGUNTAS = RAIZ / "evals" / "preguntas.yaml"
DIR_CORRIDAS = RAIZ / "evals" / "corridas"


class Pregunta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    pregunta: str
    tiene_respuesta: bool
    evidencia: str | None = None
    # "dev" para ajustar el sistema, "test" para la medición final. Lo asigna Demian.
    conjunto: Literal["dev", "test"] | None = None

    @model_validator(mode="after")
    def _evidencia_solo_si_hay_respuesta(self) -> Self:
        if self.tiene_respuesta != (self.evidencia is not None):
            raise ValueError(f"{self.id}: `evidencia` va si y solo si tiene_respuesta es true")
        return self


def cargar_preguntas(ruta: Path = RUTA_PREGUNTAS) -> list[Pregunta]:
    preguntas = TypeAdapter(list[Pregunta]).validate_python(
        yaml.safe_load(ruta.read_text(encoding="utf-8"))
    )
    ids = [p.id for p in preguntas]
    if len(ids) != len(set(ids)):
        raise ValueError("hay ids de pregunta repetidos")
    return preguntas


class Corrida:
    """Registros JSONL de una corrida; permite retomarla donde quedó."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta

    def registros(self) -> list[dict[str, Any]]:
        if not self.ruta.exists():
            return []
        lineas = self.ruta.read_text(encoding="utf-8").splitlines()
        return [json.loads(linea) for linea in lineas if linea.strip()]

    def hechos(self) -> set[str]:
        return {str(r["id"]) for r in self.registros()}

    def agregar(self, registro: dict[str, Any]) -> None:
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta.open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(registro, ensure_ascii=False) + "\n")

    def reiniciar(self) -> None:
        self.ruta.unlink(missing_ok=True)


def _percentil(valores: list[float], q: float) -> float | None:
    if not valores:
        return None
    orden = sorted(valores)
    return orden[min(len(orden) - 1, round(q * (len(orden) - 1)))]


# ------------------------------------------------------------------ recuperación


def evaluar_recuperacion(
    con: sqlite3.Connection, preguntas: list[Pregunta], k: int
) -> list[dict[str, Any]]:
    """Para cada pregunta con respuesta: ¿en qué puesto del top-k sale su evidencia?"""
    corpus = [
        (int(fila[0]), normalizar(str(fila[1])))
        for fila in con.execute("SELECT id, texto FROM fragmentos")
    ]
    registros: list[dict[str, Any]] = []
    for pregunta in preguntas:
        if pregunta.evidencia is None:
            continue
        evidencia = normalizar(pregunta.evidencia)
        con_evidencia = {id_ for id_, texto in corpus if evidencia in texto}
        recuperados = [r.id for r in buscar(con, pregunta.pregunta, k)]
        puesto = next((i for i, id_ in enumerate(recuperados, 1) if id_ in con_evidencia), None)
        registros.append(
            {
                "id": pregunta.id,
                "k": k,
                # False = la pregunta está mal etiquetada: su evidencia no existe en el corpus.
                "evidencia_en_corpus": bool(con_evidencia),
                "puesto": puesto,
                "recuperados": recuperados,
            }
        )
    return registros


def resumir_recuperacion(registros: list[dict[str, Any]]) -> dict[str, Any]:
    validas = [r for r in registros if r["evidencia_en_corpus"]]
    puestos = [r["puesto"] for r in validas]
    n = len(validas)
    return {
        "preguntas": len(registros),
        "invalidas": sorted(r["id"] for r in registros if not r["evidencia_en_corpus"]),
        "k": registros[0]["k"] if registros else None,
        "en_top_k": sum(p is not None for p in puestos),
        "en_top_1": sum(p == 1 for p in puestos),
        "recall_top_k": round(sum(p is not None for p in puestos) / n, 3) if n else None,
        "mrr": round(sum(1 / p for p in puestos if p) / n, 3) if n else None,
        "falladas": sorted(r["id"] for r in validas if r["puesto"] is None),
    }


# ------------------------------------------------------------------ respuestas (con LLM)


def _citas_no_verificables(con: sqlite3.Connection, respuesta: Respuesta) -> int:
    """Comprobación independiente del orquestador: cada cita mostrada, contra la base."""
    fallas = 0
    for cita in respuesta.citas:
        fila = con.execute(
            "SELECT texto FROM fragmentos WHERE id = ?", (cita.fragmento_id,)
        ).fetchone()
        if fila is None or normalizar(cita.texto) not in normalizar(str(fila[0])):
            fallas += 1
    return fallas


def evaluar_respuestas(
    con: sqlite3.Connection,
    llm: ClienteLLM,
    preguntas: list[Pregunta],
    corrida: Corrida,
    k: int,
    pausa_s: float = 0.0,
) -> list[dict[str, Any]]:
    """Corre las preguntas pendientes y guarda cada caso en cuanto termina.

    `pausa_s` es la espera por cada llamada hecha, para no pasar el límite por minuto
    del plan gratuito. Va después de medir, así que no entra en la latencia.
    """
    hechos = corrida.hechos()
    for pregunta in preguntas:
        if pregunta.id in hechos:
            continue
        inicio = time.perf_counter()
        respuesta = responder(con, llm, pregunta.pregunta, k)
        latencia_ms = round((time.perf_counter() - inicio) * 1000)
        apoya = None
        if pregunta.evidencia is not None and respuesta.estado == "respondida":
            evidencia = normalizar(pregunta.evidencia)
            citados = {c.fragmento_id for c in respuesta.citas}
            apoya = any(
                evidencia in normalizar(r.texto) for r in respuesta.recuperados if r.id in citados
            )
        corrida.agregar(
            {
                "id": pregunta.id,
                "tiene_respuesta": pregunta.tiene_respuesta,
                "estado": respuesta.estado,
                "motivo": respuesta.motivo,
                "llamadas_llm": respuesta.llamadas_llm,
                "latencia_ms": latencia_ms,
                "citas": len(respuesta.citas),
                "citas_no_verificables": _citas_no_verificables(con, respuesta),
                # ¿Alguna cita sale del fragmento que contiene la evidencia etiquetada?
                "cita_el_fragmento_esperado": apoya,
                "respuesta": respuesta.texto,
            }
        )
        if pausa_s:
            time.sleep(pausa_s * respuesta.llamadas_llm)
    return corrida.registros()


def resumir_respuestas(registros: list[dict[str, Any]]) -> dict[str, Any]:
    con_respuesta = [r for r in registros if r["tiene_respuesta"]]
    sin_respuesta = [r for r in registros if not r["tiene_respuesta"]]
    latencias = [float(r["latencia_ms"]) for r in registros]
    return {
        "casos": len(registros),
        "citas_mostradas": sum(r["citas"] for r in registros),
        "citas_no_verificables": sum(r["citas_no_verificables"] for r in registros),
        "con_respuesta": len(con_respuesta),
        "respondidas": sum(r["estado"] == "respondida" for r in con_respuesta),
        "respondidas_citando_el_fragmento_esperado": sum(
            bool(r["cita_el_fragmento_esperado"]) for r in con_respuesta
        ),
        "sin_respuesta": len(sin_respuesta),
        "abstenciones_correctas": sum(r["estado"] == "abstencion" for r in sin_respuesta),
        "llamadas_llm": sum(r["llamadas_llm"] for r in registros),
        "latencia_p50_ms": statistics.median(latencias) if latencias else None,
        "latencia_p90_ms": _percentil(latencias, 0.9),
    }


def humo(con: sqlite3.Connection, llm: ClienteLLM, pregunta: Pregunta, k: int) -> dict[str, Any]:
    """Una llamada al LLM, sin reintento por verificación: ¿el formato de ida y vuelta funciona?

    El cliente sí puede repetir la petición ante 503 o timeout; `main` reporta cuántas hizo.
    """
    recuperados = buscar(con, pregunta.pregunta, k)
    inicio = time.perf_counter()
    crudo = llm.completar(SISTEMA, _mensaje(pregunta.pregunta, recuperados))
    latencia_ms = round((time.perf_counter() - inicio) * 1000)
    borrador = _leer_borrador(crudo)
    if isinstance(borrador, str):
        formato, problemas = borrador, []
    else:
        formato = "ok"
        problemas = [] if borrador.abstenerse else verificar(borrador, recuperados)
    return {
        "id": pregunta.id,
        "recuperados": [r.id for r in recuperados],
        "latencia_ms": latencia_ms,
        "crudo": crudo,
        "formato": formato,
        "abstenerse": None if isinstance(borrador, str) else borrador.abstenerse,
        "problemas_de_verificacion": problemas,
    }


# ------------------------------------------------------------------ CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m asistente.evaluacion.rag", description=__doc__)
    parser.add_argument("--solo-recuperacion", action="store_true")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--nombre", default="rag", help="nombre de la corrida con LLM")
    parser.add_argument("--conjunto", choices=["dev", "test"], help="obligatorio con LLM")
    parser.add_argument("--confirmar", action="store_true", help="hacer las llamadas anunciadas")
    parser.add_argument("--humo", metavar="ID", help="una sola llamada sobre esa pregunta de dev")
    parser.add_argument("--pausa", type=float, default=5.0, help="segundos de espera por llamada")
    parser.add_argument("--db", type=Path, default=RUTA_DB)
    args = parser.parse_args(argv)

    preguntas = cargar_preguntas()
    con = conectar(args.db)
    try:
        if con.execute("SELECT COUNT(*) FROM fragmentos").fetchone()[0] == 0:
            print(
                "La base no tiene fragmentos: corre python -m asistente.rag.ingesta",
                file=sys.stderr,
            )
            return 1

        if args.solo_recuperacion:
            corrida = Corrida(DIR_CORRIDAS / f"recuperacion-k{args.k}.jsonl")
            corrida.reiniciar()  # es determinista y no gasta cuota: siempre se rehace entera
            registros = evaluar_recuperacion(con, preguntas, args.k)
            for registro in registros:
                corrida.agregar(registro)
            resumen = resumir_recuperacion(registros)
            corrida.ruta.with_suffix(".resumen.json").write_text(
                json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(json.dumps(resumen, ensure_ascii=False, indent=2))
            return 0

        if args.conjunto is None:
            print("Con LLM hay que elegir --conjunto dev o --conjunto test.", file=sys.stderr)
            return 2
        sin_marcar = [p.id for p in preguntas if p.conjunto is None]
        if sin_marcar:
            print(
                f"{len(sin_marcar)} preguntas de evals/preguntas.yaml no tienen `conjunto` "
                "(dev o test). Sin esa marca no se corre nada.",
                file=sys.stderr,
            )
            return 2
        elegidas = [p for p in preguntas if p.conjunto == args.conjunto]
        if args.humo is not None:
            pregunta = next((p for p in elegidas if p.id == args.humo), None)
            if pregunta is None:
                print(f"{args.humo} no es una pregunta de {args.conjunto}.", file=sys.stderr)
                return 2
            print(
                f"Humo sobre {pregunta.id}: 1 llamada al LLM; hasta {INTENTOS} peticiones HTTP "
                "si hay 503 o timeout (cada reintento gasta cuota)."
            )
            if not args.confirmar:
                print("No se llamó a nada. Repite con --confirmar para hacerla.")
                return 0
            cargar_env(RAIZ / ".env")
            try:
                cliente_humo = cliente_desde_env(ESQUEMA_BORRADOR)
            except ErrorDelLLM as exc:
                print(f"No se llamó a nada: {exc}", file=sys.stderr)
                return 3
            try:
                registro = humo(con, cliente_humo, pregunta, args.k)
            except ErrorDelLLM as exc:
                registro = {"id": pregunta.id, "error": str(exc)}
            print(f"Peticiones HTTP en esta ejecución: {cliente_humo.peticiones}.")
            ruta = DIR_CORRIDAS / f"humo-{pregunta.id}.json"
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(json.dumps(registro, ensure_ascii=False, indent=2) + "\n", "utf-8")
            print(json.dumps(registro, ensure_ascii=False, indent=2))
            return 3 if "error" in registro else 0
        corrida = Corrida(DIR_CORRIDAS / f"{args.nombre}-{args.conjunto}-k{args.k}.jsonl")
        pendientes = len({p.id for p in elegidas} - corrida.hechos())
        print(
            f"Corrida {corrida.ruta.name}: {pendientes} preguntas pendientes de "
            f"{len(elegidas)}; entre {pendientes} y {pendientes * MAX_INTENTOS} llamadas al LLM; "
            f"hasta {pendientes * MAX_INTENTOS * INTENTOS} peticiones HTTP si hay 503 o timeout "
            "(cada reintento gasta cuota)."
        )
        if not args.confirmar:
            print("No se llamó a nada. Repite con --confirmar para correrla.")
            return 0

        cargar_env(RAIZ / ".env")
        try:
            llm = cliente_desde_env(ESQUEMA_BORRADOR)
        except ErrorDelLLM as exc:
            print(f"No se llamó a nada: {exc}", file=sys.stderr)
            return 3
        antes = sum(r["llamadas_llm"] for r in corrida.registros())
        try:
            registros = evaluar_respuestas(con, llm, elegidas, corrida, args.k, args.pausa)
        except ErrorDelLLM as exc:
            hechas = sum(r["llamadas_llm"] for r in corrida.registros())
            print(
                f"Corrida detenida: {exc}\nQuedaron guardados {len(corrida.hechos())} casos "
                f"({hechas} llamadas). {llm.peticiones} peticiones HTTP en esta ejecución. "
                "Se retoma con el mismo comando.",
                file=sys.stderr,
            )
            return 3
        llamadas = sum(r["llamadas_llm"] for r in registros) - antes
        # Las peticiones son de esta ejecución; una corrida retomada no las suma a las anteriores.
        print(f"{llamadas} llamadas al LLM y {llm.peticiones} peticiones HTTP en esta ejecución.")
        resumen = {
            "conjunto": args.conjunto,
            "modelo": llm.modelo,
            "k": args.k,
            **resumir_respuestas([r for r in registros if r["id"] in {p.id for p in elegidas}]),
        }
        corrida.ruta.with_suffix(".resumen.json").write_text(
            json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(resumen, ensure_ascii=False, indent=2))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
