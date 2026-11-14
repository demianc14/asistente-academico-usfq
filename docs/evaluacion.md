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
