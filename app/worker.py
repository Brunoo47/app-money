"""Worker ao vivo: analisa a gravação conforme ela chega e processa os candidatos."""
import logging
import threading
import time

from . import db, detector, pipeline, recorder
from .config import cfg

log = logging.getLogger("worker")
CICLO_S = 15


def _inicio_sessao(sid: str) -> float:
    """Início da gravação atual: volta pelos segmentos enquanto não houver buraco grande.
    Reiniciar o app deixa um buraco de 1–2 min; isso não é live nova, então não refaz o aquecimento."""
    buraco = cfg()["detector"].get("buraco_sessao_s", 300)
    with db.db() as con:
        segs = con.execute("SELECT inicio, duracao FROM segmentos WHERE streamer=? "
                           "AND origem='live' ORDER BY inicio DESC LIMIT 500", (sid,)).fetchall()
    inicio = segs[0]["inicio"]
    for ant in segs[1:]:
        if inicio - (ant["inicio"] + ant["duracao"]) > buraco:
            break
        inicio = ant["inicio"]
    return inicio


def _analisar_streamer(sid: str):
    d = cfg()["detector"]
    with db.db() as con:
        r = con.execute("SELECT max(inicio + duracao) fim FROM segmentos "
                        "WHERE streamer=? AND origem='live'", (sid,)).fetchone()
    if r["fim"] is None:
        return
    fim_disp = int(r["fim"])
    limite = fim_disp - d["janela"]["depois_s"]       # precisa ter o "depois" do pico gravado
    chave = f"analisado:{sid}"
    ultimo = float(db.get_estado(chave, 0))
    ultimo = max(ultimo, limite - 3600)               # não reprocessar o dia inteiro
    if limite - ultimo < 20:
        return
    # Aquecimento: no começo da live ainda não há base para saber o que é "normal".
    lim_a = max(ultimo, _inicio_sessao(sid) + d.get("aquecimento_s", 300))
    if limite <= lim_a:
        return
    t0 = int(ultimo - d.get("janela_z_s", 600))       # contexto para o z-score
    score, z = detector.analisar(sid, t0, fim_disp)
    picos = detector.achar_picos(score, z, t0, lim_a=int(lim_a), lim_b=limite,
                                 chat_min_z=d.get("chat_min_z"))
    ids = detector.registrar_candidatos(sid, picos, "live")
    if ids:
        log.info("⚡ %s: %d momento(s) novo(s) → candidatos %s", sid, len(ids), ids)
    db.set_estado(chave, limite)


def loop(parar: threading.Event):
    ultima_limpeza = 0.0
    while not parar.is_set():
        try:
            for s in cfg()["streamers"]:
                _analisar_streamer(s["id"])
            # Mais novo primeiro: corte rápido vale mais que corte antigo.
            with db.db() as con:
                prox = con.execute(
                    "SELECT id FROM candidatos WHERE status='novo' AND origem='live' "
                    "AND (tentar_em IS NULL OR tentar_em <= ?) "
                    "ORDER BY pico DESC LIMIT 1", (time.time(),)).fetchone()
            if prox:
                pipeline.processar(prox["id"])
                continue                               # já checa o próximo
            if time.time() - ultima_limpeza > 3600:
                recorder.limpar_antigos()
                ultima_limpeza = time.time()
        except Exception:
            log.exception("erro no ciclo do worker")
        parar.wait(CICLO_S)
