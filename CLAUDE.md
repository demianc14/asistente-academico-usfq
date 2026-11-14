# Asistente académico USFQ

Proyecto de portafolio de Demian (Ingeniería en Ciencias de la Computación, USFQ): RAG con citas
verificadas y planificador de semestre. La tesis es **el LLM propone, el código verifica**.

**Estado, siguientes pasos y decisiones pendientes: `ROADMAP.md`.** Léelo antes de empezar y
actualízalo al cerrar cada fase. De dónde sale cada documento: `docs/fuentes.md`.

## Dónde estamos (12-nov-2026)

- Fases 0 y 1 hechas con datos de ejemplo; Fase 2 construida pero sin LLM real conectado.
- Rama actual `fase-2-rag`. Todo son commits locales: no hay remoto ni PR.
- Lo siguiente es conectar un proveedor detrás de `ClienteLLM` y correr la evaluación; espera que
  Demian elija proveedor.

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
  evaluacion/     corridas guardadas y reanudables
sql/              schema.sql y vistas.sql
datos/ejemplo/    datos ficticios (códigos EJ…), versionados
datos/borrador/   malla real sin prerrequisitos; el ETL no la carga por defecto
datos/publico/    PDF descargados (ignorado por git)
datos/local/      historial real, syllabus y base (ignorado por git)
evals/            preguntas.yaml y corridas/
```

## Reglas de trabajo

- Una rama y una PR por fase o subfase, con commits chicos. Antes de cada commit: `pytest`, `ruff`
  y `mypy --strict`.
- Commits y GitHub con la identidad personal (`demiandidier3@gmail.com`), nunca la de trabajo. La
  cuenta activa de `gh` en esta máquina es la de trabajo: preguntar antes de cambiarla o de subir
  algo.
- Toda cifra del README sale de una corrida guardada en `evals/corridas/`.
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
