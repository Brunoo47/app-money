"""Coleta o chat ao vivo e salva no SQLite.

Twitch: IRC anônimo (não precisa de conta nem token).
YouTube: yt-dlp baixando o "live_chat" da live (vai escrevendo um JSON por linha) e a gente
lê o arquivo enquanto cresce. A lib chat-downloader quebrou com o YouTube em 18/11/2026
("Unable to parse initial video data").
"""
import json
import logging
import random
import shutil
import socket
import ssl
import subprocess
import threading
import time

from . import db
from .config import cfg, dirs

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


def _texto_yt(runs: list) -> str:
    """Junta texto e emojis (vira o atalho, ex.: ":face-with-tears-of-joy:") de uma mensagem."""
    partes = []
    for r in runs:
        if "text" in r:
            partes.append(r["text"])
        elif "emoji" in r:
            atalhos = r["emoji"].get("shortcuts") or [""]
            partes.append(atalhos[0])
    return "".join(partes)


class ChatYouTube(threading.Thread):
    def __init__(self, sid: str, url: str, buf: Buffer, parar: threading.Event):
        super().__init__(name=f"chat-yt-{sid}", daemon=True)
        self.sid, self.url, self.buf, self.parar = sid, url, buf, parar

    def run(self):
        from .recorder import _morrer_junto
        pasta = dirs()["tmp"] / "chat_yt"
        pasta.mkdir(parents=True, exist_ok=True)
        base = pasta / self.sid
        while not self.parar.is_set():
            for f in pasta.glob(f"{self.sid}.*"):
                f.unlink(missing_ok=True)
            arq = pasta / f"{self.sid}.live_chat.json.part"
            cmd = [shutil.which("yt-dlp") or "yt-dlp", "--skip-download", "--write-subs",
                   "--sub-langs", "live_chat", "--no-part", "-o", str(base), self.url]
            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        preexec_fn=_morrer_junto)
                n = self._ler(proc, arq, pasta / f"{self.sid}.live_chat.json")
                log.info("chat YouTube %s terminou (%d msgs); tento de novo em 60s", self.sid, n)
            except Exception as e:
                log.warning("chat YouTube %s falhou (%s); tento de novo em 60s", self.sid, e)
            finally:
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            self.parar.wait(60)

    def _ler(self, proc, *candidatos) -> int:
        """Acompanha o arquivo que o yt-dlp vai escrevendo (como um tail -f)."""
        n, fh, resto = 0, None, ""
        try:
            while not self.parar.is_set():
                if fh is None:
                    achado = next((c for c in candidatos if c.exists()), None)
                    if achado:
                        fh = open(achado, encoding="utf-8", errors="replace")
                pedaco = fh.read() if fh else ""
                if not pedaco:
                    if proc.poll() is not None:
                        return n
                    self.parar.wait(1)
                    continue
                linhas = (resto + pedaco).split("\n")
                resto = linhas.pop()  # última pode estar pela metade
                for linha in linhas:
                    n += self._linha(linha)
        finally:
            if fh:
                fh.close()
        return n

    def _linha(self, linha: str) -> int:
        try:
            d = json.loads(linha)
        except json.JSONDecodeError:
            return 0
        n = 0
        for a in d.get("replayChatItemAction", {}).get("actions", []):
            r = a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
            if not r:
                continue
            ts = int(r.get("timestampUsec") or 0) / 1e6 or time.time()
            autor = (r.get("authorName") or {}).get("simpleText", "")
            self.buf.add(self.sid, ts, autor, _texto_yt((r.get("message") or {}).get("runs", [])))
            n += 1
        return n


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
