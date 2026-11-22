# Evaluación del RAG

Cómo se mide la Fase 2 y qué se puede creer de cada cifra.

## Preguntas y partición

`evals/preguntas.yaml` tiene 40 preguntas: 30 con respuesta en los documentos y 10 sin ella. Cada
una lleva `conjunto: dev` o `conjunto: test` (decisión de Demian, 12-nov-2026):

| Conjunto | Con respuesta | Sin respuesta | Total | Para qué |
|---|---|---|---|---|
| dev | 20 | 10 | 30 | Ajustar el prompt y el sistema. Su cifra **no** es resultado final. |
| test | 10 (`man-03`, `06`, `09`, `12`, `15` y `cal-02`, `05`, `08`, `11`, `14`) | 0 | 10 | Medición final, una sola corrida, sin ajustar nada después. |

Las preguntas siguen siendo un borrador escrito por el agente y sin revisar (ver `ROADMAP.md`).

## Límites de la partición

- **No hay medición limpia de abstención.** Las 10 preguntas "sin respuesta" están todas en dev,
  y el prompt se ajusta en dev viendo cómo responde a esas 10. El conjunto de prueba no tiene
  ninguna y no se escribirán otras (decisión de Demian, 14-nov-2026). Por eso la abstención
  correcta se reporta solo como cifra de desarrollo, nunca como resultado final.
- **La recuperación en test no es limpia.** El buscador BM25 se ajustó una vez (quitar el
  pronombre pegado al infinitivo) después de ver la corrida de recuperación sobre las 30 preguntas
  con respuesta, incluidas las 10 que hoy son test.
- **Las citas en test sí son limpias.** La verificación de citas no se ajustó mirando ninguna
  pregunta, y el prompt solo se ajusta con dev.

## Métricas

| Métrica | Qué cuenta | Dónde |
|---|---|---|
| Citas no verificables | Citas mostradas al usuario que no aparecen literalmente en su fragmento, comprobadas otra vez contra la base, aparte del orquestador. Meta: 0. | `citas_no_verificables` |
| Abstención correcta | Preguntas sin respuesta en las que el asistente se abstuvo. Solo existe en dev y no es resultado final. | `abstenciones_correctas` |
| Respondidas | Preguntas con respuesta que terminaron con una respuesta verificada. | `respondidas` |
| Citando el fragmento esperado | De esas, cuántas citan el fragmento que contiene la evidencia etiquetada. | `respondidas_citando_el_fragmento_esperado` |
| Latencia p50/p90 | Búsqueda más llamadas al LLM (incluido el reintento), sin la pausa entre llamadas. | `latencia_p50_ms`, `latencia_p90_ms` |

Cada corrida queda en `evals/corridas/<nombre>-<conjunto>-k5.jsonl`, un caso por línea, más un
`.resumen.json`. Si se corta (por ejemplo, por un 429) se retoma con el mismo comando.

## Cómo se corre

```bash
.venv/bin/python -m asistente.evaluacion.rag --conjunto dev --humo man-01               # anuncia 1 llamada
.venv/bin/python -m asistente.evaluacion.rag --conjunto dev --humo man-01 --confirmar   # la hace
.venv/bin/python -m asistente.evaluacion.rag --conjunto dev                             # anuncia de 30 a 60
.venv/bin/python -m asistente.evaluacion.rag --conjunto dev --confirmar                 # las hace
```

Cada pregunta hace una llamada, o dos si la primera respuesta no pasa la verificación.

## Proveedor

Gemini (`gemini-3.5-flash-lite`, plan gratuito) detrás de `ClienteLLM`, en `src/asistente/llm.py`.
Una llamada es exactamente una petición HTTP: sin reintentos del SDK ni continuación automática.
Salida JSON con esquema, `thinking_level=minimal` y un tope de 2048 tokens de salida.

## Bitácora de corridas y ajustes

Cada ajuste del prompt o del cliente va aquí con su motivo, en orden.

| Fecha | Qué | Llamadas | Resultado | Ajuste que provocó |
|---|---|---|---|---|
| 14-nov-2026 | Humo sobre `man-01` (dev) | 1 | Falló: `status=incomplete`, sin más detalle (el cliente no lo registraba). No se corrió dev. | Cliente: el error ahora trae tokens de entrada, salida y razonamiento y la salida parcial; se fijan `thinking_level=minimal` y `max_output_tokens=2048`. Motivo: la causa más probable de un `incomplete` es que el razonamiento agote el tope de salida. **No está confirmado**: falta un segundo humo. |
| 14-nov-2026 | Segundo humo sobre `man-01` (dev), ya con `thinking_level=minimal` y tope 2048 | 1 | Falló antes de generar: 503, "gemini-3.5-flash-lite is currently experiencing high demand". No dice nada sobre el `incomplete` anterior. No se corrió dev. | Ninguno. Reintentar es otra llamada y espera autorización. |

# Evaluación del agente planificador

Cómo se mide el bucle de la Fase 3 (`src/asistente/agente/`) y qué se puede creer de cada cifra.

## Qué mide y qué no

El LLM de esta evaluación es un cliente falso que sigue un guion escrito a mano. **Toda cifra de
esta sección mide el bucle y el guion, no a Gemini.** Dice que el código hace lo correcto ante cada respuesta prevista del
modelo (propuesta válida, propuesta rechazada, curso inventado, salida ilegible...). No dice con
qué frecuencia un modelo real da cada una de esas respuestas, ni si entiende el prompt: el prompt
del agente no lo ha visto ningún modelo.

## Escenarios

`evals/escenarios_agente.yaml` tiene 17 escenarios sobre los datos de ejemplo, cada uno con su
guion y su resultado esperado. Los redactó el agente; Demian revisó la tabla de los 15 primeros y
pidió dos más (`ag-16` y `ag-17`). Los resultados esperados se dedujeron a mano de
`datos/ejemplo/malla.yaml` y el archivo se congeló (commit "escenarios congelados") antes de que
ningún test lo cargara y antes de la corrida.

| Categoría | Escenarios |
|---|---|
| Válido a la primera | `ag-01`, `ag-02` |
| Plan sin NRC (se muestra con "horario sin verificar") | `ag-03` |
| Válido tras una corrección | `ag-04`, `ag-05`, `ag-06` |
| Válido tras dos correcciones | `ag-07`, `ag-08` |
| No converge: se abstiene y no muestra plan | `ag-09`, `ag-10` |
| Curso inventado | `ag-11`, `ag-12` |
| Código mal formado | `ag-13` |
| Herramienta inexistente | `ag-14` |
| `creditos_max` ausente: pregunta | `ag-15` |
| Periodo ausente: pregunta el código | `ag-16` |
| Tres salidas ilegibles seguidas | `ag-17` |

### Escenarios v2 (15-nov-2026)

Una revisión adversarial del bucle (clientes falsos, 0 llamadas a Gemini) encontró que el LLM
controlaba el periodo y que tres escenarios medían mal. El archivo se cambió y se volvió a
congelar (commit "escenarios congelados v2") antes de ejecutar el bucle sobre él:

- Toda solicitud trae `periodo`, que llena la interfaz igual que `creditos_max`.
- `ag-06` y `ag-11` traen `cursos_pedidos`. En `ag-06` el esperado incluye `pedidos_fuera`: el
  plan visible dice que Algoritmos no entró y por qué. Antes se contaba como acierto un plan
  que omitía en silencio lo pedido.
- `ag-10` intenta colar también `aprobados` que harían pasar el plan. Antes los que inyectaba
  no cambiaban el resultado, así que solo medía `creditos_max`.
- `ag-16` pasa de "pregunta del catálogo elegida por el LLM" (`periodo_ambiguo`, que contradecía
  su propia descripción) a "falta el periodo": pregunta el código con 0 llamadas. La pregunta
  elegida por el LLM (`restriccion_ambigua`) queda cubierta solo por tests, sin escenario.

La corrida v1 (`agente-guion`) se conserva; corresponde al archivo anterior (su SHA-256 está en
el resumen) y ya no se puede reproducir con el código actual.

Las preferencias ("sin clases los viernes") no cuentan: ningún código las comprueba y cumplirlas
o no en un escenario no es un acierto (ver `docs/decisiones.md`).

## Métricas

| Métrica | Qué cuenta | Campo del resumen |
|---|---|---|
| Terminan con plan válido | Escenarios que acaban mostrando un plan, vuelto a pasar por `verificar_plan` fuera del bucle con el periodo, el límite y los aprobados de la solicitud (no los del plan). | `terminan_con_plan_valido` |
| Planes mostrados que no pasan | La garantía, medida aparte. Meta: 0. | `planes_mostrados_que_no_pasan_la_reverificacion` |
| Vueltas usadas | Correcciones (propuestas menos una) de los escenarios que terminan con plan. | `vueltas_de_los_que_terminan_con_plan` |
| Herramienta correcta por paso | Llamadas en las que el código hizo lo que el escenario espera en esa posición. | `pasos_con_la_herramienta_esperada` / `pasos_esperados` |
| Cursos inventados atrapados | Códigos inexistentes que el código detectó, únicos por escenario; los intentos van aparte. | `cursos_inventados_atrapados`, `intentos_con_curso_inventado` |
| Cumplen lo esperado | Escenarios cuyo resultado coincide en todos los campos con el esperado. | `cumplen_lo_esperado` |

## Cómo se corre

```bash
.venv/bin/python -m asistente.evaluacion.agente     # 0 llamadas a Gemini
```

Guarda `evals/corridas/agente-guion-v2.jsonl` y su `.resumen.json`, con el SHA-256 del archivo de
escenarios. `agente-guion` (sin sufijo) es la corrida de los escenarios v1. Si la corrida ya existe no la pisa: hay que pasar `--rehacer` y anotarlo aquí.

## Bitácora

| Fecha | Qué | Llamadas a Gemini | Resultado | Ajuste que provocó |
|---|---|---|---|---|
| 14-nov-2026 | Corrida única `agente-guion` sobre los 17 escenarios congelados | 0 | 17 de 17 cumplen lo esperado; 11 terminan con plan válido; 0 planes mostrados sin pasar la re-verificación. | Ninguno. Los escenarios no se tocaron después de congelarlos. |
| 15-nov-2026 | Revisión adversarial del bucle con clientes falsos | 0 | 8 fallos (F-01 a F-08): el LLM elegía el periodo y con eso saltaba prerrequisitos y el tope de créditos; quitar el NRC esquivaba un choque; dos salidas tumbaban el bucle. | Cambios en `verificar_plan`, en el bucle y en los tipos; escenarios v2 (ver arriba y `docs/decisiones.md`). |
| 15-nov-2026 | Corrida única `agente-guion-v2` sobre los 17 escenarios v2 | 0 | 17 de 17 cumplen lo esperado a la primera; 11 terminan con plan válido; 0 planes mostrados sin pasar la re-verificación contra la solicitud; 41 de 41 pasos. | Ninguno. Los escenarios no se tocaron después de congelarlos. |

Antes de la corrida, los tests de garantías (`tests/test_escenarios_agente.py`) ya habían
ejecutado el bucle sobre los 17 guiones y sus variantes con canario. Esos tests no comprueban el
resultado esperado de cada escenario.

En la v2 pasó lo mismo: los tests de garantías corrieron sobre los escenarios después del commit
de congelación y antes de la corrida. Los ataques de la revisión adversarial quedaron como tests
en `tests/test_agente.py` y `tests/test_planificador.py`.
