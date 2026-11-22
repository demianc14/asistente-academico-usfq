# Roadmap

Estado al **14-nov-2026**. El plan completo y sus principios están en el brief original
(`asistente-academico-usfq.md`, fuera del repo); aquí va solo dónde está cada fase.

Todo el trabajo está en commits **locales**. El 14-nov `fase-2-llm` (Fases 0, 1 y 2, con el
proveedor del LLM) entró a `main` con un squash local, sin push, por decisión de Demian. **La Fase 2
entró sin cerrar: el humo de Gemini sigue pendiente** y no hay ninguna cifra medida con LLM. Las
ramas `fase-0-1-datos-sql`, `fase-2-rag` y `fase-2-llm` conservan el historial commit a commit. No
hay remoto en GitHub ni PR abiertas. La rama actual es `fase-3-agente`, que sale de
`fase-3-herramientas`, que sale de ese `main`. Ninguna de las dos ha entrado a `main`.

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

### Fase 3: planificador (herramientas y agente, con datos de ejemplo y sin LLM real)

- `src/asistente/planificador/`: `buscar_curso`, `prerrequisitos`, `cursos_habilitados`, `horarios`
  y `verificar_plan`, con entrada y salida Pydantic y sin ninguna llamada a un LLM.
- `verificar_plan` comprueba prerrequisitos, correquisitos (el mismo semestre cuenta), créditos por
  semestre y choques de horario, y devuelve **todos** los motivos. Las reglas están en
  `docs/decisiones.md`.
- `ejecutar(herramienta, argumentos)` devuelve una entrada mal formada como respuesta estructurada,
  para el agente futuro.
- El ETL rechaza ahora los ciclos de requisitos que pasan por alguna arista `pre`.
- El código de curso se estrechó a tres letras y cuatro dígitos con `E` final opcional, y acepta
  espacio o guion (`docs/decisiones.md`).

- **Agente** (`src/asistente/agente/`): bucle de tool use detrás de `ClienteLLM`. Una acción por
  llamada; máximo 3 propuestas (2 correcciones) y 10 llamadas; tres salidas ilegibles seguidas
  terminan en abstención. Solo se muestra un plan que pasó `verificar_plan`, el texto visible lo
  redacta el código, las preguntas y abstenciones salen de un catálogo cerrado, y `periodo`,
  `creditos_max` y `aprobados` son los de la solicitud. Decisiones en `docs/decisiones.md`.
- **Revisión adversarial (15-nov-2026)**: 8 fallos corregidos. El plan solo puede ser del periodo
  pedido y con oferta cargada; el NRC es obligatorio si hay secciones; la solicitud se valida al
  entrar (`error_entrada`, 0 llamadas); el plan lista los `cursos_pedidos` que no entraron con su
  motivo; ninguna salida del LLM tumba el bucle.
- Las preferencias ("sin clases los viernes") no se verifican: llegan en `Solicitud.preferencias`
  y el plan las lista como "preferencias no verificadas".
- **Evaluación** (`python -m asistente.evaluacion.agente`): 17 escenarios congelados en
  `evals/escenarios_agente.yaml` (v2) y su corrida, `evals/corridas/agente-guion-v2.resumen.json`.
  La corrida de la v1 (`agente-guion`) se conserva.

Medido con cliente de guion (cada cifra mide el bucle y el guion, no a Gemini): 17 de 17 escenarios cumplen lo
esperado; 11 terminan con plan válido (4 sin corrección, 5 con una, 2 con dos); 41 de 41 pasos con
la herramienta esperada; 2 cursos inventados atrapados en 3 intentos; 0 planes mostrados que no
pasen la re-verificación.

Sin medir: todo lo que dependa de un modelo real. El prompt del agente no lo ha visto ningún
modelo y no se ha hecho ninguna llamada a Gemini desde el agente.

No hecho: el servidor MCP y el bot, las reglas de la malla que no son prerrequisitos y los cupos.
Nada se probó con la malla real.

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
4. **Fase 3: lo que falta.** El agente y sus 17 escenarios con guion están hechos. Faltan: un
   humo del agente con Gemini (D; una solicitud gasta hasta 10 llamadas), las reglas de la malla
   que no son prerrequisitos (`CMP 5992` en el último semestre, `PAS 4000`, clase en inglés), los
   cupos y verificar por código alguna preferencia (por ejemplo, "sin clases los viernes" se puede
   comprobar con los bloques de las secciones). Necesita la malla real y la oferta de un periodo,
   copiada a mano (D).
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
| Fusionar `fase-3-herramientas` y `fase-3-agente` a `main` | Seguir con la Fase 4 desde `main` | Las dos ramas son locales. `fase-3-agente` contiene a la otra. |
| Humo del agente con Gemini | Saber si el prompt del agente funciona con un modelo real | Una solicitud gasta entre 1 y 10 llamadas. Antes hay que cerrar el humo de la Fase 2, que sigue fallando. |
| ¿Qué preferencias se verifican por código? | Que "sin clases los viernes" deje de salir como no verificada | Hoy ninguna. Días y horas se pueden comprobar con los horarios; "priorizar datos" necesita áreas más finas. |
| Quién separa las preferencias del texto | La interfaz de la Fase 4 | `Solicitud.preferencias`, `periodo`, `cursos_pedidos` y `creditos_max` los llena la interfaz, no el LLM. Falta decidir cómo: campos del bot o un paso aparte. |
| Límite de créditos por semestre | Usar `verificar_plan` con datos reales | **Sin verificar.** `creditos_max` no tiene valor por defecto; hay que confirmar el límite real de la USFQ (y si cambia en verano) en el manual vigente o en Registro. |
| Códigos de otras carreras | Abrirlo a compañeros | El patrón acepta tres letras y cuatro dígitos con `E` opcional, que es lo que respaldan la malla de CMP y el manual. Si otra carrera usa otra forma, hay que ampliarlo. |
| Prerrequisitos de la malla | El agente de la Fase 3 | Filas en `datos/malla_prerrequisitos.csv`, verificadas contra el catálogo del portal. La malla de bloques no los trae escritos: son flechas del diagrama. |
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
