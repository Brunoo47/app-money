"""Lê arquivos de chat de VOD (para o backtest) em vários formatos.

Suporta:
  - JSON do TwitchDownloaderCLI (chatdownload)     → comments[].content_offset_seconds
  - JSON do chat-downloader (lista de mensagens)    → time_in_seconds
  - JSONL/CSV simples: segundos, autor, mensagem
Retorna lista de (segundos_desde_inicio_do_vod, autor, mensagem).
"""
import csv
import json
from pathlib import Path


def carregar(caminho: Path) -> list[tuple[float, str, str]]:
    texto = Path(caminho).read_text(encoding="utf-8-sig")
    out: list[tuple[float, str, str]] = []
    try:
        dados = json.loads(texto)
    except json.JSONDecodeError:
        dados = None

    if isinstance(dados, dict) and "comments" in dados:          # TwitchDownloader
        for c in dados["comments"]:
            out.append((float(c["content_offset_seconds"]),
                        (c.get("commenter") or {}).get("display_name", ""),
                        (c.get("message") or {}).get("body", "")))
    elif isinstance(dados, list):                                 # chat-downloader
        for m in dados:
            t = m.get("time_in_seconds")
            if t is None:
                continue
            out.append((float(t), (m.get("author") or {}).get("name", ""),
                        m.get("message", "")))
    else:
        linhas = [l for l in texto.splitlines() if l.strip()]
        if linhas and linhas[0].lstrip().startswith("{"):         # JSONL
            for l in linhas:
                m = json.loads(l)
                out.append((float(m["t"]), m.get("autor", ""), m.get("msg", "")))
        else:                                                     # CSV
            for row in csv.reader(linhas):
                try:
                    out.append((float(row[0]), row[1], ",".join(row[2:])))
                except (ValueError, IndexError):
                    continue
    out.sort(key=lambda x: x[0])
    return out
