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
