"""Carrega o config.yaml e variáveis de ambiente."""
import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_env(caminho: Path) -> None:
    """Lê o .env quando roda direto no Python (no Docker quem faz isso é o env_file).
    Variável que já existe no ambiente ganha do arquivo."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        valor = valor.strip().strip('"').strip("'")
        os.environ.setdefault(chave.strip(), valor)


_carregar_env(RAIZ / ".env")
DATA_DIR = Path(os.environ.get("DATA_DIR", RAIZ / "data")).resolve()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


@lru_cache
def cfg() -> dict:
    caminho = Path(os.environ.get("CONFIG_PATH", RAIZ / "config.yaml"))
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def streamer(sid: str) -> dict:
    for s in cfg()["streamers"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"streamer '{sid}' não está no config.yaml")


def caminho_marca(chave: str) -> Path | None:
    """Resolve um arquivo da marca (fonte, frame) relativo à raiz do projeto."""
    valor = cfg()["marca"].get(chave)
    if not valor:
        return None
    p = (RAIZ / valor).resolve()
    return p if p.exists() else None


def dirs() -> dict[str, Path]:
    d = {
        "raw": DATA_DIR / "raw",
        "tmp": DATA_DIR / "tmp",
        "candidatos": DATA_DIR / "clips" / "candidatos",
        "aprovados": DATA_DIR / "clips" / "aprovados",
        "backtest": DATA_DIR / "backtest",
    }
    for p in d.values():
        p.mkdir(parents=True, exist_ok=True)
    return d
