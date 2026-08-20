CREATE TABLE IF NOT EXISTS conta (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL UNIQUE,
    tier            TEXT    NOT NULL,
    perfil          TEXT    NOT NULL,
    saldo           INTEGER NOT NULL DEFAULT 0,
    saldo_lido_em   TEXT,
    saldo_confiavel INTEGER NOT NULL DEFAULT 0,
    reset_em        TEXT,
    saude           TEXT    NOT NULL DEFAULT 'viva',
    falhas          INTEGER NOT NULL DEFAULT 0,
    ativa           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS job (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    modo           TEXT    NOT NULL,
    modelo         TEXT    NOT NULL,
    prompt         TEXT    NOT NULL,
    destino        TEXT    NOT NULL,
    nome           TEXT    NOT NULL,
    referencias    TEXT    NOT NULL DEFAULT '[]',
    frame_inicial  TEXT,
    frame_final    TEXT,
    prioridade     INTEGER NOT NULL DEFAULT 0,
    estado         TEXT    NOT NULL DEFAULT 'pendente',
    conta_id       INTEGER REFERENCES conta(id),
    id_remoto      TEXT,
    arquivo        TEXT,
    tentativas     INTEGER NOT NULL DEFAULT 0,
    custo_estimado INTEGER,
    erro           TEXT,
    reavaliar_em   TEXT,
    criado_em      TEXT    NOT NULL,
    atualizado_em  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_fila ON job (estado, prioridade DESC, id);

CREATE TABLE IF NOT EXISTS evento (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    INTEGER REFERENCES job(id),
    conta_id  INTEGER REFERENCES conta(id),
    tipo      TEXT NOT NULL,
    payload   TEXT NOT NULL DEFAULT '{}',
    em        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evento_job ON evento (job_id, id);
