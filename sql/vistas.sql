-- Vistas sobre la malla. Se recrean en cada carga.

-- Cierre transitivo del grafo de requisitos: una fila por cada par
-- (curso, requisito directo o indirecto) con la distancia mínima.
-- Sigue aristas `pre` y `co`: si A tiene como correquisito a B y B exige C,
-- A depende de C. El tope de profundidad protege ante correquisitos mutuos.
DROP VIEW IF EXISTS v_cadena_prerrequisitos;
CREATE VIEW v_cadena_prerrequisitos AS
WITH RECURSIVE cadena (curso, requisito, profundidad) AS (
    SELECT curso, requisito, 1
    FROM prerrequisitos
    UNION
    SELECT c.curso, p.requisito, c.profundidad + 1
    FROM cadena AS c
    JOIN prerrequisitos AS p ON p.curso = c.requisito
    WHERE c.profundidad < 30
      AND p.requisito <> c.curso
)
SELECT curso, requisito, MIN(profundidad) AS profundidad
FROM cadena
GROUP BY curso, requisito;

-- Cursos cuello de botella: cuántos cursos dependen de cada uno.
DROP VIEW IF EXISTS v_cuellos_de_botella;
CREATE VIEW v_cuellos_de_botella AS
SELECT
    c.codigo,
    c.nombre,
    COALESCE(d.directos, 0) AS dependientes_directos,
    COALESCE(t.totales, 0)  AS dependientes_totales
FROM cursos AS c
LEFT JOIN (
    SELECT requisito, COUNT(*) AS directos FROM prerrequisitos GROUP BY requisito
) AS d ON d.requisito = c.codigo
LEFT JOIN (
    SELECT requisito, COUNT(*) AS totales FROM v_cadena_prerrequisitos GROUP BY requisito
) AS t ON t.requisito = c.codigo;

DROP VIEW IF EXISTS v_cursos_aprobados;
CREATE VIEW v_cursos_aprobados AS
SELECT DISTINCT curso FROM historial_estudiante WHERE estado = 'aprobado';

-- Cursos que el historial cargado permite tomar: ni aprobados ni en curso,
-- y con todos sus prerrequisitos (`pre`) aprobados. Los correquisitos no
-- bloquean; se listan los pendientes para que el plan los incluya.
DROP VIEW IF EXISTS v_cursos_habilitados;
CREATE VIEW v_cursos_habilitados AS
SELECT
    c.codigo,
    c.nombre,
    c.creditos,
    c.area,
    (
        SELECT group_concat(p.requisito, ',')
        FROM prerrequisitos AS p
        WHERE p.curso = c.codigo
          AND p.tipo = 'co'
          AND p.requisito NOT IN (SELECT curso FROM v_cursos_aprobados)
    ) AS correquisitos_pendientes
FROM cursos AS c
WHERE c.codigo NOT IN (
        SELECT curso FROM historial_estudiante WHERE estado IN ('aprobado', 'cursando')
    )
  AND NOT EXISTS (
        SELECT 1
        FROM prerrequisitos AS p
        WHERE p.curso = c.codigo
          AND p.tipo = 'pre'
          AND p.requisito NOT IN (SELECT curso FROM v_cursos_aprobados)
    );

-- Créditos de la malla por área y tipo, con lo aprobado y lo que falta.
DROP VIEW IF EXISTS v_creditos_restantes_por_area;
CREATE VIEW v_creditos_restantes_por_area AS
SELECT
    m.carrera,
    c.area,
    m.tipo,
    SUM(c.creditos) AS creditos_totales,
    SUM(CASE WHEN a.curso IS NOT NULL THEN c.creditos ELSE 0 END) AS creditos_aprobados,
    SUM(CASE WHEN a.curso IS NULL THEN c.creditos ELSE 0 END)     AS creditos_restantes
FROM malla AS m
JOIN cursos AS c ON c.codigo = m.curso
LEFT JOIN v_cursos_aprobados AS a ON a.curso = c.codigo
GROUP BY m.carrera, c.area, m.tipo;
