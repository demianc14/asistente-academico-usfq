# Asistente académico USFQ

> **En construcción.** Hay capa de datos (Fase 1) y RAG con citas verificadas (Fase 2) probado con
> un LLM de mentira. Todavía no hay proveedor de LLM conectado, planificador, servidor MCP ni
> interfaz.

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
- **Evaluación** (`asistente.evaluacion`, `evals/`): 40 preguntas (30 con respuesta, 10 sin ella)
  y corridas guardadas caso por caso.

## Cómo correrlo

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m asistente.etl
.venv/bin/python scripts/descargar_fuentes.py
.venv/bin/python -m asistente.rag.ingesta
.venv/bin/python -m asistente.evaluacion.rag --solo-recuperacion
.venv/bin/pytest
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
- Una sola carrera y un solo estudiante por base.
