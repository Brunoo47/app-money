"""Carrega o config.yaml e variáveis de ambiente."""
import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
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
