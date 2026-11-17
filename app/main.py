"""Ponto de entrada.

  python -m app.main live        grava + lê chat + detecta + gera cortes (deixar rodando)
  python -m app.main painel      painel web em http://localhost:8080
  python -m app.main processar 12   (re)processa o candidato 12
"""
import logging
import signal
import sys
import threading

from . import chat_live, db, worker
from .config import cfg
from .recorder import Gravador

log = logging.getLogger("main")


def live():
    parar = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: parar.set())
    signal.signal(signal.SIGINT, lambda *_: parar.set())
    chat_live.iniciar(parar)
    gravadores = [Gravador(s, parar) for s in cfg()["streamers"]]
    for g in gravadores:
        g.start()
    log.info("rodando ao vivo com %d streamer(s). Ctrl+C para parar.", len(cfg()["streamers"]))
    worker.loop(parar)
    log.info("parando...")
    # Espera cada gravador fechar o streamlink/ffmpeg. Sem isso o Python sai antes,
    # e os dois ficam órfãos gravando para sempre (aconteceu no teste: 6 GB).
    for g in gravadores:
        g.join(timeout=70)
    log.info("parado.")


def main():
    db.iniciar()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "live"
    if cmd == "live":
        live()
    elif cmd == "painel":
        import uvicorn
        uvicorn.run("app.panel.app:app", host="0.0.0.0", port=cfg()["painel"]["porta"])
    elif cmd == "processar":
        from . import pipeline
        pipeline.processar(int(sys.argv[2]), forcar="--forcar" in sys.argv)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
