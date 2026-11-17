"""Gravador: fica de olho em cada streamer e grava a live em segmentos curtos.

streamlink (pega a live) → ffmpeg (corta em segmentos .ts sem re-encodar).
Cada segmento fechado entra no SQLite com o horário real (epoch) de início.
"""
import csv
import json
import logging
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import db
from .config import cfg, dirs

log = logging.getLogger("gravador")
INTERVALO_CHECAGEM_S = 60
RECHECAR_CATEGORIA_S = 120     # durante a gravação: o streamer trocou de jogo?
RX_REPRISE = re.compile(r"\b(rerun|reprise|replay)\b", re.IGNORECASE)


def _morrer_junto():
    """Roda no processo filho (Linux/Docker): se o Python morrer de vez (kill -9, travou),
    o kernel mata o streamlink/ffmpeg junto, em vez de deixá-los gravando sozinhos."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except Exception:
        pass


class Gravador(threading.Thread):
    def __init__(self, s: dict, parar: threading.Event):
        super().__init__(name=f"grav-{s['id']}", daemon=True)
        self.s, self.parar = s, parar
        self.sid = s["id"]
        self._ultimo_aviso: str | None = None

    def run(self):
        log.info("vigiando %s", self.s["nome"])
        while not self.parar.is_set():
            gravou = False
            for plat in self.s["plataformas"]:
                try:
                    gravou = self._gravar(plat)
                except Exception as e:
                    log.exception("%s: erro gravando %s: %s", self.sid, plat["url"], e)
                if gravou:
                    break
            # Saiu do ar (ou offline): espera e tenta de novo.
            self.parar.wait(5 if gravou else INTERVALO_CHECAGEM_S)

    def _info(self, plat: dict) -> dict | None:
        """Metadados da live (título, categoria) ou None se estiver offline."""
        try:
            p = subprocess.run(["streamlink", "--json", plat["url"]], capture_output=True,
                               text=True, timeout=60)
            d = json.loads(p.stdout or "{}")
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            log.warning("%s: não consegui checar a live (%s)", self.sid, e)
            return None
        if "streams" not in d:
            return None
        return d.get("metadata") or {}

    def _motivo_pular(self, info: dict) -> str | None:
        """Por que NÃO gravar essa live agora (reprise, jogo errado) ou None se pode gravar."""
        g = cfg()["gravacao"]
        titulo = info.get("title") or ""
        categoria = info.get("category") or ""
        if g.get("pular_reprise", True) and RX_REPRISE.search(titulo):
            return f"reprise ({titulo[:50]})"
        cats = [c.lower() for c in g.get("categorias") or []]
        # Sem categoria (ex.: YouTube às vezes não informa) → não bloqueia.
        if cats and categoria and not any(c in categoria.lower() for c in cats):
            return f"categoria '{categoria}' não é das configuradas"
        return None

    def _avisar(self, msg: str | None):
        """Loga só quando o motivo muda (senão vira spam a cada minuto)."""
        if msg != self._ultimo_aviso and msg:
            log.info("%s ao vivo, mas não vou gravar: %s", self.s["nome"], msg)
        self._ultimo_aviso = msg

    def _gravar(self, plat: dict) -> bool:
        g = cfg()["gravacao"]
        info = self._info(plat)
        if info is None:
            return False
        motivo = self._motivo_pular(info)
        self._avisar(motivo)
        if motivo:
            return False
        pasta = dirs()["raw"] / self.sid / datetime.now().strftime("%Y%m%d_%H%M%S")
        pasta.mkdir(parents=True, exist_ok=True)
        lista = pasta / "lista.csv"
        sl = ["streamlink", "--stdout", "--retry-open", "1", "--stream-segment-threads", "2"]
        if plat["tipo"] == "twitch":
            sl += ["--twitch-low-latency"]  # anúncios já são filtrados por padrão
        sl += [plat["url"], g.get("qualidade", "best")]
        ff = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", "pipe:0",
              "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
              "-f", "segment", "-segment_time", str(g.get("segmento_s", 120)),
              "-reset_timestamps", "1", "-segment_list", str(lista),
              "-segment_list_type", "csv", str(pasta / "%05d.ts")]

        log_sl = open(pasta / "streamlink.log", "w", encoding="utf-8")
        log_ff = open(pasta / "ffmpeg.log", "w", encoding="utf-8")
        p_sl = subprocess.Popen(sl, stdout=subprocess.PIPE, stderr=log_sl, preexec_fn=_morrer_junto)
        p_ff = subprocess.Popen(ff, stdin=p_sl.stdout, stderr=log_ff, preexec_fn=_morrer_junto)
        p_sl.stdout.close()

        inicio_epoch = None
        registrados = 0
        ultima_checagem = time.time()
        while True:
            if inicio_epoch is None and (pasta / "00000.ts").exists():
                inicio_epoch = time.time()
                log.info("🔴 %s AO VIVO (%s) → gravando em %s", self.s["nome"], plat["tipo"], pasta)
            if inicio_epoch is not None:
                registrados = self._registrar(pasta, lista, inicio_epoch, registrados)
            if p_ff.poll() is not None or self.parar.is_set():
                break
            if time.time() - ultima_checagem > RECHECAR_CATEGORIA_S:
                ultima_checagem = time.time()
                info = self._info(plat)
                motivo = self._motivo_pular(info) if info else None
                if motivo:   # trocou de jogo / virou reprise: para e volta a vigiar
                    self._avisar(motivo)
                    p_sl.terminate()
                    break
            time.sleep(2)

        if self.parar.is_set() and p_sl.poll() is None:
            p_sl.terminate()
        p_sl.wait(timeout=30)
        p_ff.wait(timeout=30)
        log_sl.close()
        log_ff.close()

        if inicio_epoch is None:      # nem começou: estava offline
            shutil.rmtree(pasta, ignore_errors=True)
            return False
        self._registrar(pasta, lista, inicio_epoch, registrados)
        log.info("⏹ %s saiu do ar", self.s["nome"])
        return True

    def _registrar(self, pasta: Path, lista: Path, epoch0: float, ja: int) -> int:
        """Lê a lista do ffmpeg (só tem segmentos fechados) e registra os novos."""
        if not lista.exists():
            return ja
        with open(lista, encoding="utf-8") as f:
            linhas = [l for l in csv.reader(f) if len(l) >= 3]
        for nome, ini, fim in linhas[ja:]:
            with db.db() as con:
                con.execute(
                    "INSERT OR IGNORE INTO segmentos(streamer, caminho, inicio, duracao, origem) "
                    "VALUES (?,?,?,?,'live')",
                    (self.sid, str(pasta / nome), epoch0 + float(ini), float(fim) - float(ini)),
                )
        return len(linhas)


def limpar_antigos():
    """Apaga gravação bruta mais velha que retencao_h (cortes prontos ficam)."""
    limite = time.time() - cfg()["gravacao"].get("retencao_h", 48) * 3600
    with db.db() as con:
        velhos = con.execute(
            "SELECT id, caminho FROM segmentos WHERE origem='live' AND inicio < ?", (limite,)
        ).fetchall()
        for r in velhos:
            Path(r["caminho"]).unlink(missing_ok=True)
            Path(r["caminho"] + ".rms.npy").unlink(missing_ok=True)
            con.execute("DELETE FROM segmentos WHERE id=?", (r["id"],))
        con.execute("DELETE FROM chat WHERE ts > 1e9 AND ts < ?", (limite,))
    for pasta in dirs()["raw"].glob("*/*"):
        if pasta.is_dir() and not any(pasta.glob("*.ts")):
            shutil.rmtree(pasta, ignore_errors=True)
    if velhos:
        log.info("limpeza: %d segmentos antigos apagados", len(velhos))
