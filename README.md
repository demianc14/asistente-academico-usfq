# Asistente académico USFQ

[![CI](https://github.com/demianc14/asistente-academico-usfq/actions/workflows/ci.yml/badge.svg)](https://github.com/demianc14/asistente-academico-usfq/actions/workflows/ci.yml)

> **En construcción.** Hay capa de datos (Fase 1), RAG con citas verificadas (Fase 2) y
> planificador con su agente (Fase 3), todo probado con un LLM de mentira y datos de ejemplo.
> Gemini está conectado pero no hay ninguna cifra medida con él. Faltan la malla real, el
> servidor MCP y la interfaz.

Asistente para estudiantes: responde sobre el manual del estudiante y el calendario con citas
verificadas y propondrá planes de semestre. La regla del proyecto es que **el LLM propone y el
código verifica**.

## Qué hay

- **Datos y SQL** (`sql/`, `asistente.etl`): malla como grafo, vistas con CTE recursivas (cadena de
  prerrequisitos, cursos cuello de botella, cursos habilitados, créditos por área) y ETL con
  validación fail-fast.
- **RAG** (`asistente.rag`): los PDF se fragmentan por su estructura (títulos del índice del
  manual, tablas del calendario), se buscan con BM25 (SQLite FTS5) y la respuesta del LLM solo se
  muestra si cada cita aparece literalmente en el fragmento que dice citar y toda cifra de la
  respuesta está en una cita. Si falla, se reintenta una vez; si vuelve a fallar, el asistente se
  abstiene.
- **Planificador** (`asistente.planificador`, `asistente.agente`): herramientas deterministas
  (`buscar_curso`, `prerrequisitos`, `cursos_habilitados`, `horarios`, `verificar_plan`) y un bucle
  en el que el LLM propone un plan y el código lo verifica, con dos correcciones como máximo. Solo
  se muestra un plan que pasó `verificar_plan`; el texto que ve el usuario lo redacta el código, y
  el máximo de créditos y los cursos aprobados son los de la solicitud, no los que diga el LLM.
- **Evaluación** (`asistente.evaluacion`, `evals/`): 40 preguntas para el RAG (30 con respuesta,
  10 sin ella), 17 escenarios con guion para el agente y corridas guardadas caso por caso.

## Cómo correrlo

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m asistente.etl
.venv/bin/python scripts/descargar_fuentes.py
.venv/bin/python -m asistente.rag.ingesta
.venv/bin/python -m asistente.evaluacion.rag --solo-recuperacion
.venv/bin/python -m asistente.evaluacion.agente --nombre prueba   # 17 escenarios, sin LLM real
.venv/bin/pytest
```

Con Docker, en una máquina limpia (sin `.env`, base ni PDF): ETL, descarga de los PDF públicos e
ingesta. La base y los PDF quedan en volúmenes de Docker.

```bash
docker compose run --rm pipeline
```

## Resultados

Recuperación (sin LLM), de `evals/corridas/recuperacion-k5.resumen.json`:

| Preguntas con respuesta | Evidencia en el top-5 | En el primer puesto | MRR |
|---|---|---|---|
| 30 | 28 (93 %) | 23 | 0,84 |

Las dos que fallan preguntan cuándo "empieza y termina" un periodo, y el calendario dice "inicio y
fin": es el límite de buscar por palabras. La primera corrida dio 26 de 30; después se añadió una
regla general de español (quitar el pronombre pegado al infinitivo, "retirarme" → "retirar") y
subió a 28. Las preguntas son un borrador escrito por el mismo agente que escribió el buscador, así
que esta cifra es optimista hasta que alguien más las revise.

Citas no verificables y latencia: **sin medir todavía**. El proveedor (Gemini) está conectado,
pero aún no hay una corrida completa (ver `docs/evaluacion.md`).

**Abstención: no hay medición limpia.** Las 10 preguntas sin respuesta están todas en el conjunto
de desarrollo, y el prompt se ajusta en ese conjunto viéndolas. El conjunto de prueba no tiene
ninguna. La cifra de abstención que salga de dev es de desarrollo, no un resultado.

### Agente planificador (con cliente de guion)

De `evals/corridas/agente-guion-v2.resumen.json`, que sale de
`.venv/bin/python -m asistente.evaluacion.agente` (0 llamadas a Gemini). El LLM es un cliente falso
que sigue un guion, así que cada cifra de esta tabla **mide el bucle y el guion, no a Gemini**.

| Cifra | Valor | Campo del resumen | Qué mide |
|---|---|---|---|
| Escenarios que terminan con plan válido | 11 de 17 | `terminan_con_plan_valido` | El bucle y el guion, no a Gemini |
| Planes mostrados que no pasan la re-verificación | 0 de 11 | `planes_mostrados_que_no_pasan_la_reverificacion` | El bucle y el guion, no a Gemini |
| Vueltas usadas por esos 11 | 0 vueltas: 4; 1 vuelta: 5; 2 vueltas: 2 | `vueltas_de_los_que_terminan_con_plan` | El bucle y el guion, no a Gemini |
| Herramienta correcta por paso | 41 de 41 | `pasos_con_la_herramienta_esperada` | El bucle y el guion, no a Gemini |
| Cursos inventados atrapados | 2 de 2 (en 3 intentos) | `cursos_inventados_atrapados` | El bucle y el guion, no a Gemini |
| Escenarios que cumplen lo esperado | 17 de 17 | `cumplen_lo_esperado` | El bucle y el guion, no a Gemini |

Los 6 escenarios que no terminan con plan tampoco debían: 4 abstenciones (2 por no converger
tras dos correcciones, 1 sin plan posible, 1 por tres salidas ilegibles) y 2 preguntas (falta el
máximo de créditos; falta el periodo; las dos las hace el código sin llamar al LLM). Los escenarios y sus resultados esperados los escribió el
mismo agente que escribió el bucle y se congelaron antes de la corrida; con un guion, el 100 % dice
que el código reacciona como se diseñó, no que un modelo real vaya a proponer buenos planes. Son
los escenarios v2: una revisión adversarial encontró que el LLM podía elegir el periodo y hubo que
cambiar el bucle y cuatro escenarios; la corrida anterior (`agente-guion`) se conserva. El detalle
está en `docs/evaluacion.md`.

## Privacidad

El historial real, los syllabus y la base viven en `datos/local/`, fuera de git. Un test revisa que
nada de eso aparezca en ningún commit. Los documentos de la universidad no se versionan: se bajan
con `scripts/descargar_fuentes.py`.

## Limitaciones actuales

- El manual cargado es la edición de **abril de 2023**, no la vigente (ver `docs/fuentes.md`).
- La malla real está transcrita **sin prerrequisitos** (`datos/borrador/`); lo que carga el ETL por
  defecto son datos ficticios (códigos `EJ…`).
- La oferta de horarios no tiene fuente pública: se ingresa a mano.
- La verificación de cifras solo ve números escritos con dígitos, no con palabras.
- El planificador verifica prerrequisitos, créditos y choques de horario, **no las preferencias**
  ("sin clases los viernes", "priorizar datos"): el plan las lista como "preferencias no
  verificadas" y puede incumplirlas.
- Una sola carrera y un solo estudiante por base.
