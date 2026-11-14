# Roadmap

Estado al **14-nov-2026**. El plan completo y sus principios están en el brief original
(`asistente-academico-usfq.md`, fuera del repo); aquí va solo dónde está cada fase.

Todo el trabajo está en commits **locales**. El 14-nov `fase-2-llm` (Fases 0, 1 y 2, con el
proveedor del LLM) entró a `main` con un squash local, sin push, por decisión de Demian. **La Fase 2
entró sin cerrar: el humo de Gemini sigue pendiente** y no hay ninguna cifra medida con LLM. Las
ramas `fase-0-1-datos-sql`, `fase-2-rag` y `fase-2-llm` conservan el historial commit a commit. No
hay remoto en GitHub ni PR abiertas.

## Qué quedó hecho

### Fase 0: fuentes y permisos (parcial)

- Los cinco PDF públicos están localizados, descargados y revisados; el detalle está en
  `docs/fuentes.md` y se bajan con `scripts/descargar_fuentes.py` (no se versionan).
- Todos tienen texto extraíble. Los calendarios 2026-2027 están completos.
- El manual es la edición de **abril de 2023**: la única publicada que se pudo descargar.
- La oferta de horarios no tiene fuente pública sin inicio de sesión: será entrada manual.
- Faltan las decisiones de permisos y proveedor (ver "Qué decide Demian").

### Fase 1: datos y SQL (hecha con datos de ejemplo)

- Esquema (`sql/schema.sql`) y las cuatro vistas del brief (`sql/vistas.sql`): cadena de
  prerrequisitos con CTE recursiva, cuellos de botella, cursos habilitados y créditos por área.
- ETL (`python -m asistente.etl`) con validación Pydantic, fail-fast y reporte de calidad.
- Test de privacidad sobre los archivos versionados y el historial de git.
- Malla real de Ciencias de la Computación transcrita como **borrador sin prerrequisitos**
  (`datos/borrador/malla_cmp.yaml`): 46 cursos y 8 cupos. El ETL carga por defecto los datos
  ficticios de `datos/ejemplo/`.

No hecho: cargar la malla real (falta confirmar prerrequisitos) y decidir cómo se asocia un curso
aprobado a un cupo (optativas, electivas), así que los cupos no entran en la vista de créditos.

### Fase 2: RAG con citas verificadas (construida, sin LLM real)

- Ingesta (`python -m asistente.rag.ingesta`): manual fragmentado por los títulos de su índice
  (322 de 324 localizados, 499 fragmentos) y calendarios por tabla (38 fragmentos).
- Búsqueda BM25 con SQLite FTS5.
- Verificación: cada cita debe aparecer literalmente en su fragmento y toda cifra de la respuesta
  debe estar en una cita; un reintento y, si falla, abstención.
- Evaluación (`python -m asistente.evaluacion.rag`): 40 preguntas en `evals/preguntas.yaml` y
  corridas reanudables en `evals/corridas/`.
- Protocolo `ClienteLLM` con un cliente de guion para tests y **Gemini** (`gemini-3.5-flash-lite`)
  como proveedor real: una llamada es una petición HTTP, sin reintentos del SDK.
- Partición dev (30) y test (10) de las preguntas, decidida por Demian; límites y bitácora de
  ajustes en `docs/evaluacion.md`. La evaluación con LLM exige `--conjunto` y `--confirmar`.
- **Humo fallido (14-nov):** la única llamada hecha a Gemini, sobre `man-01`, terminó
  `status=incomplete`. Se añadió diagnóstico y se fijó `thinking_level=minimal` con un tope de 2048
  tokens. El segundo humo (14-nov) devolvió 503 por saturación del modelo, así que el arreglo
  sigue sin confirmar. Van 2 llamadas en total; la corrida de dev no se ha hecho.

Medido (corrida `evals/corridas/recuperacion-k5.resumen.json`): la evidencia sale en el top-5 en
28 de 30 preguntas y en el primer puesto en 23. Dos reservas: las preguntas las redactó el agente y
no están revisadas, y el buscador se ajustó una vez después de ver la primera corrida (26 de 30).

Sin medir: citas no verificables, abstención correcta y latencia. Dependen del LLM real.

## Qué sigue

En este orden; lo marcado con (D) espera una decisión de Demian.

1. **Cerrar la Fase 2.** Segundo humo (1 llamada, espera OK de Demian); si pasa, dev (30 a 60
   llamadas); ajustar el prompt solo con dev, anotando cada ajuste; al final, test una sola vez (10
   a 20 llamadas) y poner en el README las cifras de test, no las de dev.
2. **Revisar las 40 preguntas** (D), en especial las 10 "sin respuesta": se eligieron por no estar
   en los documentos, pero no se comprobó leyendo el manual entero.
3. **Cargar la malla real.** Demian llena y verifica `datos/malla_prerrequisitos.csv` (D); el ETL
   rechaza el archivo mientras tenga una fila con `verificado` en `no`. Luego mover el borrador a
   `datos/malla.yaml` y borrar `tests/test_borrador_malla.py`.
4. **Fase 3: planificador con herramientas.** `buscar_curso`, `prerrequisitos`,
   `cursos_habilitados`, `verificar_plan` y `horarios`; el agente con máximo dos vueltas; 15
   escenarios de evaluación. Aquí se modelan también las reglas de la malla que no son
   prerrequisitos (`CMP 5992` en el último semestre, `PAS 4000`, clase en inglés) y los cupos.
   Necesita la malla real y la oferta de un periodo, copiada a mano (D).
5. **Fase 4:** servidor MCP, interfaz para compañeros (D), piloto y CSV para Power BI. La primera
   ventana realista para el piloto es el registro de verano, que abre el 29-abr-2027.
6. **Fase 5:** Docker, GitHub Actions y README final.

Pendientes menores:

- Dos títulos del índice del manual no se localizan en el cuerpo (uno es una entrada que el índice
  trae pegada a otra); su texto queda dentro del apartado anterior.
- Las dos preguntas que la búsqueda falla usan "empieza y termina" donde el calendario dice "inicio
  y fin". Es el dato a favor de probar embeddings, que el brief deja para cuando la evaluación lo
  justifique.
- Crear el repo en GitHub y abrir las PR por fase (D).

Decidido el 14-nov: no habrá preguntas de abstención propias para test. La abstención no tiene
medición limpia y no se reporta como resultado final.

## Qué decide Demian

| Decisión | Bloquea | Notas |
|---|---|---|
| Segundo humo y corrida de dev | Cerrar la Fase 2 | Proveedor decidido: Gemini. Humo: 1 llamada; dev: de 30 a 60. |
| Prerrequisitos de la malla | Fase 3 | Filas en `datos/malla_prerrequisitos.csv`, verificadas contra el catálogo del portal. La malla de bloques no los trae escritos: son flechas del diagrama. |
| Revisión de las 40 preguntas | Credibilidad de las cifras | El brief pide etiquetado a mano. |
| Edición vigente del manual | Abrirlo a compañeros | No confirmada. El PDF cargado es de abril de 2023, no la edición 2023-2024. Conseguir la vigente desde el navegador o en Registro. |
| Cuenta y visibilidad del repo en GitHub | Las PR y el CI | La cuenta activa de `gh` es la de trabajo; hay otra conectada, `demianc14`. |
| Oferta de horarios copiada del portal | Fase 3 | ¿Puede ir en el repo público o se queda en `datos/local/`? |
| Permiso de la universidad | El piloto | ¿Avisar a la carrera o a un profesor antes de abrirlo? |
| Interfaz para compañeros | Fase 4 | Bot de Telegram (recomendado en el brief) o web mínima. |
| Syllabus | Nada por ahora | Solo los de cursos propios, en local. |
| Áreas más finas para los cursos | "Priorizar electivas de datos" en la Fase 3 | El diagrama solo distingue carrera, colegio general e idiomas. |
| Semestre de `PAS 4000` | Nada por ahora | El diagrama la dibuja fuera de las filas; quedó en el semestre 8 como aproximación. |
| Nombre de autor en git | Nada | Quedó "Demian Cruz" con `demiandidier3@gmail.com`; el nombre lo puso el agente. |
