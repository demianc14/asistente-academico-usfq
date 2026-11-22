# Decisiones

Decisiones de diseño que no se deducen del código, con la fuente que las respalda. Las páginas del
manual son las del PDF de abril de 2023 (ver `docs/fuentes.md`).

## Formato del código de curso (14-nov-2026)

**Qué acepta el sistema.** Tres letras y cuatro dígitos, con una `E` final opcional:
`^[A-Z]{3}\d{4}E?$`. Está en `src/asistente/etl/modelos.py` (`PATRON_CODIGO`) y lo usan el ETL y
las herramientas del planificador.

**No es un formato oficial.** Es el formato **respaldado por la malla y el manual** que tenemos.
Ningún documento público consultado fija cuántas letras o dígitos lleva un código, y otras carreras
podrían usar otra forma. Si aparece un código real que no encaja, se amplía el patrón citando dónde
se vio.

**Qué lo respalda:**

| Parte | Fuente |
|---|---|
| Tres letras y cuatro dígitos | Malla de bloques de Ciencias de la Computación (06-may-2026): las 104 apariciones de código en el PDF tienen esa forma, entre ellas los 46 cursos de `datos/borrador/malla_cmp.yaml`. |
| Qué significan las partes | Manual, pág. 35: el código indica el área académica, seguido del nivel y grado de dificultad; el ejemplo es `ADM-1001`, materia de primer año del Colegio de Administración. No dice cuántos caracteres tiene cada parte. |
| `E` final | Manual, pág. 38: los cursos dictados en inglés "se identifican con una letra “E” al final de su código, ej. REL2000E". También `ENG1001E` (págs. 38 y 60). |

**Qué se dejó fuera por no tener respaldo.** El patrón anterior (`^[A-Z]{2,4}\d{4}[A-Z]?$`, escrito
en la Fase 1) admitía dos o cuatro letras y cualquier letra final. Ninguna de las dos cosas aparece
en la malla ni en el manual. En la tabla de exámenes de inglés del manual (pág. 36) aparece
`TOFL 032`, que es un código de examen y no de curso.

**Comprobado al estrechar:** todos los códigos de `datos/ejemplo/`, `datos/borrador/` y los tests
ya tenían tres letras y cuatro dígitos; no hubo que corregir ninguno.

## Normalización del código (14-nov-2026)

Las fuentes escriben el mismo código de tres maneras:

| Escritura | Dónde |
|---|---|
| Junto: `PAS4000`, `ESP1001`, `MAT1002` | Manual, págs. 51 y 95 |
| Con espacio: `PAS 4000`, `CMP 4005` | Manual, pág. 95; toda la malla de bloques |
| Con guion: `ADM-1001` | Manual, pág. 35 |

La entrada acepta las tres, en mayúsculas o minúsculas y con espacios alrededor, y las convierte a
la forma canónica sin separador (`PAS4000`, `ADM1001`). Se admite **un** separador, espacio o guion,
entre las letras y los dígitos; cualquier otra cosa (`ABC--1001`, `ABC_1001`, `A BC1001`) se
rechaza. Tests: `test_normaliza_espacio_guion_y_mayusculas` y `test_rechaza_codigos_con_otra_forma`
en `tests/test_etl.py`.

Los identificadores de cupo (`IdCupo`) no cambian: siguen quitando espacios y nada más.

## Ciclos de requisitos (14-nov-2026)

El ETL rechaza todo ciclo que pase por al menos una arista `pre`, aunque el resto sean `co`. Un
ciclo así no se puede cumplir: `pre` pide "estrictamente antes" y `co` "antes o a la vez". Los
ciclos solo de `co` (correquisitos mutuos) siguen permitidos: se cumplen llevando los cursos juntos.

## Reglas de `verificar_plan` (14-nov-2026)

- Un prerrequisito cuenta si está aprobado o en un semestre anterior del plan. Un correquisito
  cuenta además si va en el mismo semestre.
- "Cursando" no cuenta como aprobado. Quien llama decide qué pasa en `aprobados`.
- `creditos_max` no tiene valor por defecto: **el límite real de créditos por semestre de la USFQ
  está sin verificar**, así que el sistema no trae ninguno.
- Dos bloques chocan si comparten día y se solapan al menos un minuto. Terminar a las 10:00 y
  empezar a las 10:00 no es choque.
- Un curso solo puede ir sin NRC si no tiene secciones cargadas en su periodo. Ese plan puede
  ser válido, pero el resultado lo marca (`horario_verificado = false`) y el mensaje al usuario
  dice "horario sin verificar". Si el curso tiene secciones, el NRC es obligatorio
  (`nrc_faltante`): antes, quitarlo servía para esquivar un choque (15-nov-2026).
- `Plan.periodo` es el periodo que pidió el usuario. Si viene, un semestre de otro periodo se
  rechaza (`periodo_distinto`) y también un periodo sin ninguna sección cargada
  (`periodo_sin_oferta`). Sin él, `verificar_plan` sigue aceptando planes de varios periodos,
  también futuros: eso queda para quien lo llame directamente; el agente siempre lo pone.
- Periodos, códigos y horas solo aceptan dígitos ASCII (`[0-9]`). `\d` acepta cualquier dígito
  Unicode, y "２０９９１０" pasaba por un periodo distinto de "209910".
- `Texto` va en una sola línea: rechaza saltos de línea, caracteres de control y de formato
  invisible. Lo que se imprime tal cual no puede imitar otra sección de la salida.
- Un código mal formado no llega a la base: la entrada se rechaza. `ejecutar` devuelve ese rechazo
  como datos (`ok = false`, con campo, mensaje y valor) en lugar de lanzar una excepción.

## Bucle del agente planificador (14-nov-2026)

Decididas por Demian sobre la propuesta del agente. Código en `src/asistente/agente/`; tests en
`tests/test_agente.py`.

- **Topes.** Como mucho 3 propuestas por solicitud (la inicial y 2 correcciones, que es lo que
  pide el brief) y 10 llamadas al LLM contando consultas. Si se agota cualquiera, el agente se
  abstiene (`no_converge` o `sin_pasos`) y no muestra ningún plan.
- **Tres salidas ilegibles seguidas terminan en abstención** (`salida_invalida`). Motivo: la
  cuota gratuita de Gemini es chica y un modelo que devuelve tres veces algo que no es una acción
  válida no tiene por qué gastar las 10 llamadas. Una salida ilegible suelta no gasta vuelta de
  corrección y el contador vuelve a cero con la primera acción legible.
- **El LLM no escribe nada que vea el usuario.** Las preguntas y las abstenciones son valores de
  un catálogo cerrado y el texto lo redacta el código; el plan visible sale del `Plan` que
  `verificar_plan` dio por válido, con nombres y créditos de la base. `falta_creditos_max`,
  `no_converge`, `sin_pasos` y `salida_invalida` solo los emite el código.
- **`creditos_max` y `aprobados` son de la solicitud.** El código los escribe en cada plan antes
  de verificarlo y pisa lo que ponga el LLM; si no, el LLM podría inventarse aprobados para
  saltarse un prerrequisito. Sin `creditos_max` el agente pregunta, con 0 llamadas al LLM.
- **Una propuesta mal formada gasta una vuelta**; una consulta mal formada o a una herramienta
  que no existe, no. Llamar a `verificar_plan` como herramienta cuenta como propuesta.
- **Las preferencias no son requisitos.** "Sin clases los viernes" o "priorizar datos" no las
  comprueba ningún código en esta fase: `verificar_plan` solo ve prerrequisitos, créditos y
  choques. Llegan en `Solicitud.preferencias`, separadas por quien llama, y el plan visible las
  lista bajo "Preferencias no verificadas", seguido de qué sí se comprobó. Un escenario de
  evaluación no cuenta el cumplimiento de una preferencia como acierto.

## Tras la revisión adversarial del bucle (15-nov-2026)

Pedidas por Demian punto por punto (F-01 a F-08). Lo marcado como "elección de implementación"
no lo decidió él: es lo que quedó y puede cambiarse.

- **El periodo es de la solicitud.** `Solicitud.periodo` lo llena la interfaz, como
  `creditos_max`, y el código lo escribe en cada plan antes de verificarlo. Sin periodo el
  agente pregunta `falta_periodo` con 0 llamadas. El plan mostrado tiene un solo semestre, el
  del periodo pedido. El LLM ya no puede preguntar por el periodo: su catálogo de preguntas
  queda en `restriccion_ambigua`, y `periodo_ambiguo` desaparece (elección de implementación).
- **La solicitud se valida al entrar.** Un código de `aprobados` o de `cursos_pedidos` que no
  existe, o un periodo sin oferta cargada, terminan con 0 llamadas. Elección de implementación:
  no es una excepción sino un estado más de `ResultadoAgente`, `error_entrada`, con la lista de
  `errores` y un texto redactado por el código, igual que `ejecutar` devuelve las entradas malas
  como datos. Va antes que las preguntas.
- **Cursos pedidos.** `Solicitud.cursos_pedidos` (opcional, lo llena la interfaz) no obliga al
  plan, pero el plan visible lista los que no entraron con su motivo. Elección de
  implementación: el motivo lo saca el código añadiendo el curso al plan ya verificado, con cada
  una de sus secciones, y volviendo a pasar `verificar_plan`. Si con alguna sección pasa, el
  motivo es `no_propuesto` ("habría pasado la verificación, pero la propuesta no lo incluyó");
  si solo falla el horario con todas, "todas sus secciones chocan"; si no, los motivos que no
  dependen de la sección. Lo pedido solo en `texto`, sin código, sigue sin comprobarse.
- **Ninguna salida del LLM tumba el bucle.** `_leer_accion` no lee más de 20 000 caracteres y
  atrapa cualquier error (JSON muy anidado, enteros enormes): es una `salida_invalida` más.
- **La re-verificación de la evaluación usa la solicitud.** Arma el plan con el periodo, el
  límite y los aprobados de la solicitud y los cursos mostrados; antes verificaba el plan contra
  sus propios datos y no podía ver un periodo o unos aprobados colados.
