"""Baixa um VOD (Twitch ou YouTube) + o chat dele, para o backtest.

Uso:
  python -m tools.baixar_vod https://www.twitch.tv/videos/123456789 --nome paulinho1
  python -m tools.baixar_vod <url> --nome teste --inicio 01:00:00 --fim 02:00:00

Gera data/backtest/<nome>.mp4 e data/backtest/<nome>.chat.json
"""
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from app.chat_import import carregar
from app.config import dirs


def _segundos(hms: str) -> float:
    s = 0.0
    for parte in hms.split(":"):
        s = s * 60 + float(parte)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--nome", required=True)
    ap.add_argument("--inicio", help="HH:MM:SS (opcional, baixa só um trecho)")
    ap.add_argument("--fim", help="HH:MM:SS")
    args = ap.parse_args()

    pasta = dirs()["backtest"]
    video = pasta / f"{args.nome}.mp4"
    chat = pasta / f"{args.nome}.chat.json"

    cmd = ["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", str(video), args.url]
    if args.inicio or args.fim:
        cmd += ["--download-sections", f"*{args.inicio or '0'}-{args.fim or 'inf'}",
                "--force-keyframes-at-cuts"]
    print("Baixando vídeo...")
    subprocess.run(cmd, check=True)

    m = re.search(r"twitch\.tv/videos/(\d+)", args.url)
    if m and shutil.which("TwitchDownloaderCLI"):
        print("Baixando chat (TwitchDownloaderCLI)...")
        c = ["TwitchDownloaderCLI", "chatdownload", "--id", m.group(1), "-o", str(chat)]
        if args.inicio:
            c += ["-b", args.inicio]
        if args.fim:
            c += ["-e", args.fim]
        subprocess.run(c, check=True)
    else:
        print("Baixando chat (chat-downloader)...")
        try:
            from chat_downloader import ChatDownloader
            msgs = ChatDownloader().get_chat(args.url, output=str(chat))
            for _ in msgs:
                pass
        except Exception as e:
            print(f"⚠ não consegui baixar o chat ({e}). O backtest roda só com áudio.")
            chat = None

    if chat and Path(chat).exists():
        # Normaliza para JSONL simples e alinha com o trecho baixado (--inicio).
        desloc = _segundos(args.inicio) if args.inicio else 0.0
        fim = _segundos(args.fim) if args.fim else float("inf")
        msgs = [(t - desloc, a, m) for t, a, m in carregar(Path(chat))
                if desloc <= t < fim]
        jsonl = pasta / f"{args.nome}.chat.jsonl"
        with open(jsonl, "w", encoding="utf-8") as f:
            for t, a, m in msgs:
                f.write(json.dumps({"t": round(t, 2), "autor": a, "msg": m},
                                   ensure_ascii=False) + "\n")
        print(f"chat: {len(msgs)} mensagens")
        chat = jsonl
    print(f"\nPronto:\n  vídeo: {video}\n  chat:  {chat or '(nenhum)'}")
    print(f"\nAgora rode:\n  python -m tools.backtest --streamer <id> --video {video}"
          + (f" --chat {chat}" if chat else "") + " --top 15 --processar 5")


if __name__ == "__main__":
    main()
