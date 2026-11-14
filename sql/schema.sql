-- Esquema del asistente académico (SQLite).
-- Se aplica de forma idempotente: todo es CREATE ... IF NOT EXISTS.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- Malla
CREATE TABLE IF NOT EXISTS cursos (
    codigo   TEXT PRIMARY KEY,
    nombre   TEXT NOT NULL,
    creditos INTEGER NOT NULL CHECK (creditos >= 0),  -- 0: requisito de graduación sin créditos
    area     TEXT NOT NULL
);

-- Arista del grafo: `curso` requiere `requisito`.
--   pre = debe estar aprobado antes; co = puede cursarse a la vez.
CREATE TABLE IF NOT EXISTS prerrequisitos (
    curso     TEXT NOT NULL REFERENCES cursos (codigo),
    requisito TEXT NOT NULL REFERENCES cursos (codigo),
    tipo      TEXT NOT NULL CHECK (tipo IN ('pre', 'co')),
    PRIMARY KEY (curso, requisito),
    CHECK (curso <> requisito)
);
CREATE INDEX IF NOT EXISTS idx_prerrequisitos_requisito ON prerrequisitos (requisito);

CREATE TABLE IF NOT EXISTS malla (
    carrera           TEXT NOT NULL,
    curso             TEXT NOT NULL REFERENCES cursos (codigo),
    semestre_sugerido INTEGER NOT NULL CHECK (semestre_sugerido BETWEEN 1 AND 12),
    tipo              TEXT NOT NULL CHECK (tipo IN ('obligatorio', 'electiva')),
    PRIMARY KEY (carrera, curso)
);

-- Casillas de la malla sin curso fijo (optativas, electivas libres, cupos de
-- Colegio General). Todavía no entran en las vistas: falta decidir cómo se
-- asocia un curso aprobado a un cupo.
CREATE TABLE IF NOT EXISTS cupos (
    carrera           TEXT NOT NULL,
    id                TEXT NOT NULL,
    nombre            TEXT NOT NULL,
    creditos          INTEGER NOT NULL CHECK (creditos > 0),
    area              TEXT NOT NULL,
    semestre_sugerido INTEGER NOT NULL CHECK (semestre_sugerido BETWEEN 1 AND 12),
    PRIMARY KEY (carrera, id)
);

-- ---------------------------------------------------------------- Oferta
CREATE TABLE IF NOT EXISTS secciones (
    id      INTEGER PRIMARY KEY,
    curso   TEXT NOT NULL REFERENCES cursos (codigo),
    nrc     TEXT NOT NULL,
    periodo TEXT NOT NULL,
    UNIQUE (nrc, periodo)
);

-- Una sección tiene uno o más bloques semanales.
CREATE TABLE IF NOT EXISTS horarios (
    seccion_id INTEGER NOT NULL REFERENCES secciones (id) ON DELETE CASCADE,
    dia        TEXT NOT NULL CHECK (dia IN ('LU', 'MA', 'MI', 'JU', 'VI', 'SA')),
    inicio     TEXT NOT NULL CHECK (inicio GLOB '[0-2][0-9]:[0-5][0-9]'),
    fin        TEXT NOT NULL CHECK (fin GLOB '[0-2][0-9]:[0-5][0-9]'),
    PRIMARY KEY (seccion_id, dia, inicio),
    CHECK (inicio < fin)
);

-- ---------------------------------------------------------------- Documentos (RAG)
CREATE TABLE IF NOT EXISTS documentos (
    id             INTEGER PRIMARY KEY,
    titulo         TEXT NOT NULL,
    fuente_url     TEXT,
    fecha_consulta TEXT NOT NULL,
    sha256         TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS fragmentos (
    id           INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL REFERENCES documentos (id) ON DELETE CASCADE,
    seccion      TEXT,            -- p. ej. "Art. 12" o el título del apartado
    pagina       INTEGER,
    texto        TEXT NOT NULL,
    contexto     TEXT NOT NULL DEFAULT ''  -- título del documento y sección: solo para buscar
);

-- Índice BM25. `remove_diacritics 2` hace que "matricula" encuentre "matrícula".
CREATE VIRTUAL TABLE IF NOT EXISTS fragmentos_fts USING fts5 (
    texto,
    contexto,
    content = 'fragmentos',
    content_rowid = 'id',
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS fragmentos_ai AFTER INSERT ON fragmentos BEGIN
    INSERT INTO fragmentos_fts (rowid, texto, contexto) VALUES (new.id, new.texto, new.contexto);
END;
CREATE TRIGGER IF NOT EXISTS fragmentos_ad AFTER DELETE ON fragmentos BEGIN
    INSERT INTO fragmentos_fts (fragmentos_fts, rowid, texto, contexto)
    VALUES ('delete', old.id, old.texto, old.contexto);
END;
CREATE TRIGGER IF NOT EXISTS fragmentos_au AFTER UPDATE ON fragmentos BEGIN
    INSERT INTO fragmentos_fts (fragmentos_fts, rowid, texto, contexto)
    VALUES ('delete', old.id, old.texto, old.contexto);
    INSERT INTO fragmentos_fts (rowid, texto, contexto) VALUES (new.id, new.texto, new.contexto);
END;

-- ---------------------------------------------------------------- Métricas de uso (anónimas)
-- Sin identificador de usuario. `texto` queda NULL salvo opt-in explícito.
CREATE TABLE IF NOT EXISTS consultas (
    id          INTEGER PRIMARY KEY,
    fecha       TEXT NOT NULL,
    tipo        TEXT NOT NULL CHECK (tipo IN ('reglamento', 'plan', 'curso')),
    resultado   TEXT NOT NULL CHECK (resultado IN ('respondida', 'abstencion', 'plan_valido', 'plan_fallido', 'error')),
    latencia_ms INTEGER NOT NULL CHECK (latencia_ms >= 0),
    texto       TEXT
);

-- ---------------------------------------------------------------- Historial (solo local)
-- Un único estudiante por base: a propósito no hay columna de identidad.
-- Los datos reales viven en datos/local/ (ignorado por git).
CREATE TABLE IF NOT EXISTS historial_estudiante (
    curso   TEXT NOT NULL REFERENCES cursos (codigo),
    periodo TEXT NOT NULL,
    estado  TEXT NOT NULL CHECK (estado IN ('aprobado', 'cursando', 'reprobado')),
    PRIMARY KEY (curso, periodo)
);
