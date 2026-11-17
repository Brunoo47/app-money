"""Detector de momentos: dá uma nota para cada segundo da live e acha os picos.

Sinais (1 valor por segundo, no relógio absoluto dos segmentos):
  - audio:  volume em dB (RMS)
  - chat:   mensagens por segundo (log)
  - reacao: mensagens com KKKK, KEKW, 😂... (log)
Cada sinal vira z-score em relação aos últimos ~10 min do próprio streamer,
porque cada um tem um volume e um chat de base diferente.
"""
import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import db
from .config import cfg, streamer

log = logging.getLogger("detector")
SR = 16000


# ---------------------------------------------------------------- áudio
def rms_por_segundo(caminho: Path) -> np.ndarray:
    """Volume (dB) de cada segundo do arquivo. Fica em cache ao lado do vídeo."""
    cache = Path(str(caminho) + ".rms.npy")
    if cache.exists() and cache.stat().st_mtime >= Path(caminho).stat().st_mtime:
        return np.load(cache)
    cmd = ["ffmpeg", "-v", "error", "-i", str(caminho), "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        log.warning("sem áudio em %s", caminho)
        return np.zeros(0, dtype=np.float32)
    amostras = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    n = len(amostras) // SR
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    blocos = amostras[: n * SR].reshape(n, SR)
    rms = np.sqrt(np.mean(blocos ** 2, axis=1) + 1e-12)
    db_ = (20 * np.log10(rms)).astype(np.float32)
    np.save(cache, db_)
    return db_


def serie_audio(sid: str, t0: int, t1: int) -> np.ndarray:
    """Volume por segundo em [t0, t1), juntando os segmentos que cobrem o intervalo."""
    n = t1 - t0
    out = np.full(n, np.nan, dtype=np.float32)
    with db.db() as con:
        segs = con.execute(
            "SELECT caminho, inicio, duracao FROM segmentos "
            "WHERE streamer=? AND inicio < ? AND inicio + duracao > ? ORDER BY inicio",
            (sid, t1, t0),
        ).fetchall()
    for s in segs:
        if not Path(s["caminho"]).exists():
            continue
        v = rms_por_segundo(Path(s["caminho"]))
        ini = int(round(s["inicio"]))
        a, b = max(t0, ini), min(t1, ini + len(v))
        if b > a:
            out[a - t0 : b - t0] = v[a - ini : b - ini]
    return out


# ---------------------------------------------------------------- chat
def _regex_reacoes() -> re.Pattern:
    termos = cfg()["detector"]["reacoes"]
    partes = []
    for t in termos:
        if t.upper() == "KKKK":
            partes.append(r"k{4,}")          # kkkk, KKKKKKK...
        elif t.upper() == "AAAA":
            partes.append(r"a{4,}")
        else:
            partes.append(re.escape(t))
    return re.compile("|".join(partes), re.IGNORECASE)


def series_chat(sid: str, t0: int, t1: int) -> tuple[np.ndarray, np.ndarray]:
    off = streamer(sid).get("chat_offset_s", -8)
    n = t1 - t0
    total = np.zeros(n, dtype=np.float32)
    reac = np.zeros(n, dtype=np.float32)
    rx = _regex_reacoes()
    with db.db() as con:
        rows = con.execute(
            "SELECT ts, msg FROM chat WHERE streamer=? AND ts >= ? AND ts < ?",
            (sid, t0 - off, t1 - off),
        ).fetchall()
    for r in rows:
        i = int(r["ts"] + off) - t0
        if 0 <= i < n:
            total[i] += 1
            if r["msg"] and rx.search(r["msg"]):
                reac[i] += 1
    return np.log1p(total), np.log1p(reac)


# ---------------------------------------------------------------- matemática
def _media_movel(x: np.ndarray, w: int) -> np.ndarray:
    """Média em janela centrada de tamanho w (cortada nas bordas)."""
    n = len(x)
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    i = np.arange(n)
    a = np.clip(i - w // 2, 0, n)
    b = np.clip(i + w // 2 + 1, 0, n)
    return ((c[b] - c[a]) / np.maximum(b - a, 1)).astype(np.float32)


def zscore_movel(x: np.ndarray, w: int, desvio_min: float) -> np.ndarray:
    x = x.astype(np.float32).copy()
    faltando = np.isnan(x)
    if faltando.all():
        return np.zeros_like(x)
    x[faltando] = np.nanmedian(x)
    m = _media_movel(x, w)
    v = _media_movel((x - m) ** 2, w)
    z = (x - m) / np.maximum(np.sqrt(v), desvio_min)
    z[faltando] = 0
    return z


@dataclass
class Pico:
    t: float          # segundo do pico (relógio absoluto)
    score: float
    audio: float
    chat: float
    reacao: float


def analisar(sid: str, t0: int, t1: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Score por segundo em [t0, t1) + os sinais separados (para debug/backtest)."""
    d = cfg()["detector"]
    w = int(d.get("janela_z_s", 600))
    audio = serie_audio(sid, t0, t1)
    chat, reac = series_chat(sid, t0, t1)
    z = {
        "audio": zscore_movel(audio, w, 1.5),   # 1.5 dB de desvio mínimo
        "chat": zscore_movel(chat, w, 0.3),
        "reacao": zscore_movel(reac, w, 0.3),
    }
    p = d["pesos"]
    # Sem chat nenhum (ex.: backtest só com vídeo), o áudio carrega o peso todo.
    if chat.sum() == 0:
        score = z["audio"]
    else:
        score = p["audio"] * z["audio"] + p["chat"] * z["chat"] + p["reacao"] * z["reacao"]
    score = _media_movel(score, int(d.get("suavizar_s", 3)))
    return score, z


def achar_picos(score: np.ndarray, z: dict, t0: int, lim_a: int | None = None,
                lim_b: int | None = None, chat_min_z: float | None = None) -> list[Pico]:
    """Máximos acima do limiar, do maior para o menor, separados por separacao_min_s.
    lim_a/lim_b: só aceita picos nesse intervalo (relógio absoluto).
    chat_min_z: exige que o chat (mensagens ou reações) tenha subido perto do pico.
    Sem isso, um tiro ou uma granada (só áudio alto) vira corte. Sem chat nenhum
    (reprise, chat fora do ar), nenhum pico passa."""
    d = cfg()["detector"]
    limiar, sep = d["limiar"], d["separacao_min_s"]
    social = None
    if chat_min_z is not None:
        # ±5s: o offset do chat é uma média, a reação real espalha alguns segundos.
        social = np.maximum(z["chat"], z["reacao"])
        social = np.array([social[max(0, i - 5): i + 6].max() for i in range(len(social))])
    ordem = np.argsort(-score)
    escolhidos: list[int] = []
    for i in ordem:
        if score[i] < limiar:
            break
        t = t0 + int(i)
        if lim_a is not None and t < lim_a:
            continue
        if lim_b is not None and t >= lim_b:
            continue
        if social is not None and social[i] < chat_min_z:
            continue
        if all(abs(int(i) - j) >= sep for j in escolhidos):
            escolhidos.append(int(i))
    return [
        Pico(t=t0 + i, score=float(score[i]), audio=float(z["audio"][i]),
             chat=float(z["chat"][i]), reacao=float(z["reacao"][i]))
        for i in escolhidos
    ]


def registrar_candidatos(sid: str, picos: list[Pico], origem: str) -> list[int]:
    """Salva picos novos como candidatos (ignora os muito perto de um já existente)."""
    sep = cfg()["detector"]["separacao_min_s"]
    ids = []
    with db.db() as con:
        for p in picos:
            perto = con.execute(
                "SELECT 1 FROM candidatos WHERE streamer=? AND origem=? AND abs(pico-?) < ?",
                (sid, origem, p.t, sep),
            ).fetchone()
            if perto:
                continue
            detalhe = json.dumps({k: round(getattr(p, k), 2) for k in ("audio", "chat", "reacao")})
            cur = con.execute(
                "INSERT INTO candidatos(streamer, origem, pico, score, detalhe, criado_em) "
                "VALUES (?,?,?,?,?,?)",
                (sid, origem, p.t, round(p.score, 3), detalhe, db.agora()),
            )
            ids.append(cur.lastrowid)
    return ids


def intervalo_gravado(sid: str, origem: str | None = None) -> tuple[int, int] | None:
    q = "SELECT min(inicio) a, max(inicio+duracao) b FROM segmentos WHERE streamer=?"
    args: list = [sid]
    if origem:
        q += " AND origem=?"
        args.append(origem)
    with db.db() as con:
        r = con.execute(q, args).fetchone()
    if r["a"] is None:
        return None
    return int(math.floor(r["a"])), int(math.ceil(r["b"]))
