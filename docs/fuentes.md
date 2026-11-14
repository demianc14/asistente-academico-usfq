# Fuentes y permisos (Fase 0)

Fecha de consulta: **11-nov-2026**. Los cinco PDF se descargaron ese día con
`scripts/descargar_fuentes.py` y se abrieron con `pypdf`. Lo marcado **OJO** sigue sin verificar y
lo marcado **DECIDE DEMIAN** espera respuesta.

## Documentos públicos

Todos están en `www.usfq.edu.ec`, sin inicio de sesión. El script los baja a `datos/publico/`
(ignorado por git): en el repo va el script, no los PDF.

| Documento | URL | Páginas | Texto extraíble | sha256 (inicio) |
|---|---|---|---|---|
| Manual del Estudiante de Grado, actualizado a abril de 2023 | <https://www.usfq.edu.ec/sites/default/files/2023-04/Manual_del_Estudiante_de_Grado_abril_18_0.pdf> | 109 | Sí, unos 527 000 caracteres | `ac48bf72e0f20eae` |
| Calendario, primer semestre 2026-2027 (202610) | <https://www.usfq.edu.ec/sites/default/files/2026-08/calendario_academico_usfq_primer_semestre_2026-2027_202610.pdf> | 1 | Sí | `d6e6e85e02427695` |
| Calendario, segundo semestre 2026-2027 (202620) | <https://www.usfq.edu.ec/sites/default/files/2026-07/calendario_academico_usfq_segundo_semestre_2026-2027_202620.pdf> | 1 | Sí | `06d3607d1ae50280` |
| Calendario, verano 2026-2027 (202630) | <https://www.usfq.edu.ec/sites/default/files/2026-07/calendario_academico_usfq_verano_2026-2027_202630.pdf> | 1 | Sí | `e7ce502621b3a02e` |
| Malla de bloques, Ing. en Ciencias de la Computación (06-may-2026) | <https://www.usfq.edu.ec/sites/default/files/2026-05/malla_de_bloques_ingenieria_en_ciencias_de_la_computacion_06052026.pdf> | 2 (español e inglés) | Solo las cajas; ver abajo | `7fe6d723153dfdca` |

### Manual del estudiante

**Edición vigente no confirmada.** El PDF cargado dice "Actualizado a abril 2023" (página 2) y sus
metadatos lo fechan el 14-abr-2023. Ninguna página contiene "2023-2024", así que no es la edición
2023-2024, y el propio manual remite a "la versión actualizada" de la web (páginas 2 y 109).

**OJO, vigencia.** La edición 2023-2024 que apareció en el buscador ya no existe (404), y la página
del manual también respondió 404 a la descarga automática. La de abril de 2023 es la única que
sigue publicada. Se usa como **provisional**: hay que conseguir la edición vigente (desde el
navegador o preguntando en Registro) antes de que alguien confíe en las respuestas.
**DECIDE DEMIAN.**

Lo que condiciona la Fase 2:

- No está organizado por artículos numerados. La estructura son títulos en mayúsculas (por ejemplo
  "ADMISIÓN DE ESTUDIANTES NO REGULARES") dentro de 14 secciones; la fragmentación va por título.
- El texto trae palabras cortadas con guion al final de línea ("es-" / "tudios") y títulos
  partidos ("REGULA-" / "RES"). La normalización para verificar citas tiene que reunirlas, o una
  cita literal correcta fallaría como subcadena.
- Una sola página (la portada) no tiene texto.

### Calendarios

Una página cada uno, en tablas de actividad, desde y hasta. El texto sale limpio, una fila por
línea. Cada fila es un fragmento natural.

### Malla

El PDF es un **diagrama**. Del texto salen, sin ambigüedad, el código, el nombre y los créditos de
cada curso, y de la imagen el semestre sugerido (filas 1 a 9). Los **prerrequisitos son flechas**:
no están en el texto y muchas se cruzan, así que no se transcriben automáticamente. Inventar o
leer mal una flecha es justo el error silencioso que el proyecto prohíbe.

Los 46 cursos y los 8 cupos están transcritos en `datos/borrador/malla_cmp.yaml`, **sin
prerrequisitos**. Cada código, crédito y fila de semestre se tomó de las coordenadas del texto del
PDF y los códigos se cruzaron contra el texto extraído. El ETL no carga ese archivo por defecto.
Los prerrequisitos van aparte, en `datos/malla_prerrequisitos.csv` (columnas `curso`, `requisito`,
`tipo` y `verificado`, que por defecto es `no`). Se versiona solo el encabezado: las filas las añade
y verifica Demian contra el catálogo del portal. El ETL no carga el CSV si tiene una sola fila sin
verificar. **DECIDE DEMIAN.**

Lo que el diagrama trae además de cursos:

- Casillas que son un cupo y no un curso (`OPT 1` a `OPT 3`, `ELECTIVA 1` y `2`, `HUM`, `CCSS`,
  `ARTE`): van a la tabla `cupos`. Falta decidir cómo se asocia un curso aprobado a un cupo, así
  que todavía no entran en las vistas de créditos.
- Cursos de 0 créditos (Inglés, `ESL 0001` a `ESL 0006`): el esquema ya los admite.
- Reglas que no son prerrequisitos entre cursos (`CMP 5992` solo en el último semestre, `PAS 4000`
  en el último verano, una clase en inglés, una con atributo Writing Intensive): están anotadas
  como comentario en el borrador y se modelan en la Fase 3, dentro de `verificar_plan`.
- `PAS 4000` está dibujada fuera de las filas; se le puso semestre 8 como aproximación.
- El área de cada curso sigue la leyenda del diagrama (carrera, colegio general, idiomas). Para
  "priorizar electivas de datos" hará falta un área más fina. **DECIDE DEMIAN.**

## Oferta de cursos y horarios

No apareció ninguna fuente pública sin inicio de sesión. La universidad publica el "catálogo de
cursos y horarios disponibles" dentro del portal. Uno de los documentos públicos que salió en la
búsqueda incluye credenciales de acceso a ese portal: **no se usan**, ni se hace scraping.

Por lo tanto la oferta es **entrada manual**: Demian copia o exporta las secciones de un periodo al
bloque `secciones` de `datos/malla.yaml` (mismo formato que `datos/ejemplo/malla.yaml`).
**DECIDE DEMIAN:** si la oferta copiada del portal puede versionarse en el repo público o se queda
en `datos/local/`.

## Syllabus

Solo los de cursos de Demian, en `datos/local/`, sin versionar, salvo que el profesor los tenga
publicados. **DECIDE DEMIAN.**

## Fechas de matrícula (el OJO del calendario del brief)

Leído del PDF del calendario 202620:

- Publicación de la oferta del segundo semestre 2026-2027: lunes 9-nov-2026.
- Pago de matrícula ordinaria: 4 al 18-nov-2026.
- Clases: 11-ene al 14-may-2027.

Del calendario de verano 202630: oferta el 15-abr-2027 y registro ordinario del 29-abr al
7-may-2027.

Es decir, la planificación del segundo semestre está pasando ahora, antes de que exista el
planificador. El piloto con compañeros apunta entonces al registro del **primer semestre 2027-2028**
(a mediados de 2027; el calendario 2027-2028 aún no está publicado) o al de verano, cuyo registro
abre el 29-abr-2027.

## Permiso

**DECIDE DEMIAN:** si se avisa a la carrera o a un profesor antes de abrirlo a compañeros. El repo
público funciona sin esa respuesta: documentos públicos descargados por script más datos de ejemplo
ficticios (`datos/ejemplo/`, códigos `EJ…`).

## Proveedor del LLM

Decidido el 12-nov-2026: Gemini (`gemini-3.5-flash-lite`, plan gratuito) detrás de `ClienteLLM`.
La clave va en `.env` y la pone Demian. Detalles de la evaluación en `docs/evaluacion.md`.

## Qué va en git y qué no

| Ruta | En git | Contenido |
|---|---|---|
| `datos/ejemplo/` | Sí | Malla, oferta e historial ficticios |
| `datos/malla.yaml` | Sí (cuando exista) | Malla real transcrita del documento público |
| `datos/publico/` | No | PDF descargados por el script |
| `datos/local/` | No | Historial real, syllabus, base SQLite |
