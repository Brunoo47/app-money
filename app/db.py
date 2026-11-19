"""SQLite: segmentos gravados, mensagens de chat e candidatos a corte."""
import sqlite3
import time
from contextlib import contextmanager

from .config import DATA_DIR

DB_PATH = DATA_DIR / "db.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS segmentos (
    id INTEGER PRIMARY KEY,
    streamer TEXT NOT NULL,
    caminho TEXT NOT NULL UNIQUE,
    inicio REAL NOT NULL,          -- epoch (ou 0 no backtest) do 1º frame
    duracao REAL NOT NULL,
    origem TEXT NOT NULL DEFAULT 'live'
);
CREATE INDEX IF NOT EXISTS ix_seg ON segmentos(streamer, inicio);

CREATE TABLE IF NOT EXISTS chat (
    streamer TEXT NOT NULL,
    ts REAL NOT NULL,              -- mesmo relógio dos segmentos
    autor TEXT,
    msg TEXT
);
CREATE INDEX IF NOT EXISTS ix_chat ON chat(streamer, ts);

CREATE TABLE IF NOT EXISTS candidatos (
    id INTEGER PRIMARY KEY,
    streamer TEXT NOT NULL,
    origem TEXT NOT NULL DEFAULT 'live',
    pico REAL NOT NULL,
    score REAL NOT NULL,
    detalhe TEXT,                  -- json com audio/chat/reacao no pico
    inicio REAL, fim REAL,         -- corte final (mesmo relógio)
    titulo TEXT, gancho TEXT, nota REAL,
    transcricao TEXT,
    arquivo TEXT,
    status TEXT NOT NULL DEFAULT 'novo',  -- novo|processando|pronto|descartado|aprovado|rejeitado|erro
    erro TEXT,
    criado_em REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cand ON candidatos(status, criado_em);

CREATE TABLE IF NOT EXISTS estado (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

-- Fotos dos números de cada plataforma ao longo do tempo (para mostrar a variação).
CREATE TABLE IF NOT EXISTS metricas (
    id INTEGER PRIMARY KEY,
    plataforma TEXT NOT NULL,      -- youtube | tiktok | instagram
    ts REAL NOT NULL,
    seguidores INTEGER,
    views INTEGER,
    receita_mes REAL,              -- R$ no mês corrente
    fonte TEXT NOT NULL DEFAULT 'manual'   -- manual | api
);
CREATE INDEX IF NOT EXISTS ix_met ON metricas(plataforma, ts);
"""


def conectar() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


@contextmanager
def db():
    con = conectar()
    try:
        yield con
    finally:
        con.close()


def iniciar():
    with db() as con:
        con.executescript(SCHEMA)
        # Colunas novas em banco que já existia.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(candidatos)")}
        if "tentativas" not in cols:   # quantas vezes a IA falhou neste candidato
            con.execute("ALTER TABLE candidatos ADD COLUMN tentativas INTEGER NOT NULL DEFAULT 0")
        if "tentar_em" not in cols:    # epoch: só processar de novo a partir daqui
            con.execute("ALTER TABLE candidatos ADD COLUMN tentar_em REAL")


def get_estado(chave: str, padrao=None):
    with db() as con:
        r = con.execute("SELECT valor FROM estado WHERE chave=?", (chave,)).fetchone()
    return r["valor"] if r else padrao


def set_estado(chave: str, valor):
    with db() as con:
        con.execute(
            "INSERT INTO estado(chave, valor) VALUES(?,?) "
            "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
            (chave, str(valor)),
        )


def agora() -> float:
    return time.time()
