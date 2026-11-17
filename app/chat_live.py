"""Coleta o chat ao vivo e salva no SQLite.

Twitch: IRC anônimo (não precisa de conta nem token).
YouTube: lib chat-downloader na URL da live.
"""
import logging
import random
import socket
import ssl
import threading
import time

from . import db
from .config import cfg

log = logging.getLogger("chat")


class Buffer:
    """Junta mensagens e grava em lote (SQLite agradece)."""

    def __init__(self):
        self.itens: list[tuple] = []
        self.lock = threading.Lock()

    def add(self, sid: str, ts: float, autor: str, msg: str):
        with self.lock:
            self.itens.append((sid, ts, autor, msg))

    def gravar(self):
        with self.lock:
            itens, self.itens = self.itens, []
        if itens:
            with db.db() as con:
                con.executemany("INSERT INTO chat(streamer, ts, autor, msg) VALUES (?,?,?,?)", itens)


class ChatTwitch(threading.Thread):
    def __init__(self, buf: Buffer, parar: threading.Event):
        super().__init__(name="chat-twitch", daemon=True)
        self.buf, self.parar = buf, parar
        self.canais = {
            p["canal_chat"].lower(): s["id"]
            for s in cfg()["streamers"] for p in s["plataformas"]
            if p["tipo"] == "twitch" and p.get("canal_chat")
        }

    def run(self):
        if not self.canais:
            return
        espera = 2
        while not self.parar.is_set():
            try:
                self._conectar()
                espera = 2
            except Exception as e:
                log.warning("twitch IRC caiu (%s), reconectando em %ss", e, espera)
                self.parar.wait(espera)
                espera = min(espera * 2, 60)

    def _conectar(self):
        ctx = ssl.create_default_context()
        with socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=360) as raw, \
                ctx.wrap_socket(raw, server_hostname="irc.chat.twitch.tv") as sock:
            def enviar(l): sock.sendall((l + "\r\n").encode())
            enviar(f"NICK justinfan{random.randint(10000, 99999)}")
            for c in self.canais:
                enviar(f"JOIN #{c}")
            log.info("chat Twitch conectado: %s", ", ".join(self.canais))
            resto = ""
            while not self.parar.is_set():
                dados = sock.recv(65536)
                if not dados:
                    raise ConnectionError("conexão fechada")
                resto += dados.decode("utf-8", "replace")
                *linhas, resto = resto.split("\r\n")
                agora = time.time()
                for l in linhas:
                    if l.startswith("PING"):
                        enviar("PONG" + l[4:])
                    elif " PRIVMSG #" in l:
                        # :autor!autor@autor.tmi.twitch.tv PRIVMSG #canal :mensagem
                        prefixo, _, msg = l.partition(" :")
                        autor = prefixo[1:].split("!", 1)[0]
                        canal = prefixo.rsplit("#", 1)[-1].strip().lower()
                        if canal in self.canais:
                            self.buf.add(self.canais[canal], agora, autor, msg)


class ChatYouTube(threading.Thread):
    def __init__(self, sid: str, url: str, buf: Buffer, parar: threading.Event):
        super().__init__(name=f"chat-yt-{sid}", daemon=True)
        self.sid, self.url, self.buf, self.parar = sid, url, buf, parar

    def run(self):
        from chat_downloader import ChatDownloader
        while not self.parar.is_set():
            try:
                for m in ChatDownloader().get_chat(self.url, timeout=None):
                    if self.parar.is_set():
                        return
                    ts = m.get("timestamp")
                    self.buf.add(self.sid, ts / 1e6 if ts else time.time(),
                                 (m.get("author") or {}).get("name", ""), m.get("message", ""))
            except Exception as e:
                log.debug("youtube %s sem live/chat (%s)", self.sid, e)
            self.parar.wait(60)


def iniciar(parar: threading.Event) -> Buffer:
    buf = Buffer()
    ChatTwitch(buf, parar).start()
    for s in cfg()["streamers"]:
        for p in s["plataformas"]:
            if p["tipo"] == "youtube":
                ChatYouTube(s["id"], p["url"], buf, parar).start()

    def gravar_loop():
        while not parar.is_set():
            parar.wait(2)
            try:
                buf.gravar()
            except Exception as e:
                log.warning("erro gravando chat: %s", e)
    threading.Thread(target=gravar_loop, name="chat-db", daemon=True).start()
    return buf
