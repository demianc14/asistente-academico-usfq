# Asistente académico USFQ

Proyecto de portafolio de Demian (Ingeniería en Ciencias de la Computación, USFQ): RAG con citas
verificadas y planificador de semestre. La tesis es **el LLM propone, el código verifica**.

**Estado, siguientes pasos y decisiones pendientes: `ROADMAP.md`.** Léelo antes de empezar y
actualízalo al cerrar cada fase. De dónde sale cada documento: `docs/fuentes.md`. Decisiones de
diseño con su fuente (formato del código de curso, reglas de `verificar_plan`): `docs/decisiones.md`.

## Dónde estamos (15-nov-2026)

- Fases 0 y 1 hechas con datos de ejemplo. Fase 2 construida con Gemini detrás de `ClienteLLM`,
  pero sin cerrar: el humo pasó (22-nov, `fase-2-humo`) y falta la corrida de dev; no hay cifras
  de evaluación con LLM. `ClienteLLM` reintenta 503 y timeout, y cada reintento gasta cuota.
- Fase 3: hechas las herramientas deterministas y el agente (bucle de tool use), con datos de
  ejemplo y un cliente de guion. Tras una revisión adversarial (8 fallos corregidos), 17
  escenarios congelados v2 y su corrida `agente-guion-v2`. Ninguna llamada a Gemini desde el
  agente.
- `main` tiene las Fases 0 a 3 (dos squash locales: `fase-2-llm` y `fase-3-agente`). Las ramas
  de cada fase conservan el historial commit a commit. No hay remoto.

## Principios no negociables

1. El LLM no inventa cursos, reglas ni cifras. Todo código de curso existe en la base y toda cita
   aparece literalmente en un fragmento recuperado. Si no se verifica, no se muestra.
2. Abstenerse es una respuesta correcta.
3. Prerrequisitos, créditos y choques de horario los decide código determinista y testeado.
4. Privacidad: nada de otros estudiantes. El historial real vive en `datos/local/`, fuera de git.
5. Documentos de la universidad: se versiona el script que los baja, no los PDF.
6. Lo simple primero: Python, SQLite con FTS5, Pydantic, pytest. Sin LangChain ni base vectorial.
   Embeddings solo si la evaluación lo justifica.

Esto aplica también a quien escribe el código: no transcribir de un diagrama o de memoria algo que
no se pueda comprobar. Por eso la malla real está sin prerrequisitos.

## Comandos

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m asistente.etl                    # malla, oferta e historial → SQLite
.venv/bin/python scripts/descargar_fuentes.py        # PDF públicos → datos/publico/
.venv/bin/python -m asistente.rag.ingesta            # PDF → fragmentos
.venv/bin/python -m asistente.evaluacion.rag --solo-recuperacion
.venv/bin/python -m asistente.evaluacion.agente      # 17 escenarios con guion; no pisa la corrida
.venv/bin/ruff format . && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest
```

No hay migraciones: si cambia `sql/schema.sql`, borrar `datos/local/asistente.db` y volver a correr
el ETL y la ingesta.

## Mapa

```
src/asistente/
  db.py           conexión y esquema
  fuentes.py      registro de documentos públicos
  llm.py          protocolo ClienteLLM y cliente de guion para tests
  etl/            modelos Pydantic y carga fail-fast
  rag/            ingesta, búsqueda BM25, verificación de citas, orquestador
  planificador/   herramientas deterministas (buscar_curso, verificar_plan...); sin LLM
  agente/         bucle de tool use: el LLM propone, verificar_plan decide qué se muestra
  evaluacion/     corridas guardadas y reanudables
sql/              schema.sql y vistas.sql
datos/ejemplo/    datos ficticios (códigos EJ…), versionados
datos/borrador/   malla real sin prerrequisitos; el ETL no la carga por defecto
datos/publico/    PDF descargados (ignorado por git)
datos/local/      historial real, syllabus y base (ignorado por git)
evals/            preguntas.yaml, escenarios_agente.yaml (congelado) y corridas/
```

## Reglas de trabajo

- Una rama y una PR por fase o subfase, con commits chicos. Antes de cada commit: `pytest`, `ruff`
  y `mypy --strict`.
- Commits y GitHub con la identidad personal (`demiandidier3@gmail.com`), nunca la de trabajo. La
  cuenta activa de `gh` en esta máquina es la de trabajo: preguntar antes de cambiarla o de subir
  algo.
- Toda cifra del README sale de una corrida guardada en `evals/corridas/`.
- `evals/escenarios_agente.yaml` está congelado: un cambio va en commit aparte y se anota en
  `docs/evaluacion.md`. Toda cifra medida con guion lleva la frase "mide el bucle y el guion, no a
  Gemini".
- Antes de una corrida con LLM, avisar cuántas llamadas hará (la cuota gratuita es chica).
- Claves de API: van en `.env` y las pone Demian.
- Nada de scraping del portal con credenciales. Un PDF público trae credenciales del portal: no se
  usan.
- Si hay dos diseños razonables, proponer en cinco líneas y esperar.
- Al cerrar cada fase: qué quedó, cómo se verificó (comando) y qué decide Demian.
- Código, comentarios y documentación en español.

## Fuera de alcance

Predicción de notas, datos de otros estudiantes, varias universidades, fine-tuning y frameworks de
agentes pesados.
