"""Roda o detector num VOD já baixado (sem ninguém ao vivo) e lista os melhores momentos.

Uso:
  python -m tools.backtest --streamer paulinholoko --video data/backtest/vod.mp4 \
      --chat data/backtest/chat.json --top 15 --processar 5

--processar N  gera os N melhores cortes de verdade (aparecem no painel).
"""
import argparse
import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from app import db, detector, pipeline
from app.chat_import import carregar
from app.config import dirs, streamer
from app.util import duracao, hms

log = logging.getLogger("backtest")


def preparar(sid: str, video: Path, chat: Path | None) -> int:
    """Limpa o backtest anterior desse streamer e registra VOD + chat (relógio começa em 0)."""
    dur = duracao(video)
    with db.db() as con:
        con.execute("DELETE FROM segmentos WHERE streamer=? AND origem='backtest'", (sid,))
        con.execute("DELETE FROM candidatos WHERE streamer=? AND origem='backtest'", (sid,))
        con.execute("DELETE FROM chat WHERE streamer=? AND ts < 1e9", (sid,))
        con.execute(
            "INSERT INTO segmentos(streamer, caminho, inicio, duracao, origem) "
            "VALUES (?,?,?,?,'backtest')", (sid, str(video.resolve()), 0.0, dur))
        if chat:
            msgs = carregar(chat)
            con.executemany("INSERT INTO chat(streamer, ts, autor, msg) VALUES (?,?,?,?)",
                            [(sid, t, a, m) for t, a, m in msgs])
            log.info("chat: %d mensagens", len(msgs))
        else:
            log.warning("sem chat: o detector vai usar só o áudio")
    return int(dur)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--streamer", required=True)
    ap.add_argument("--video", required=True, type=Path)
    ap.add_argument("--chat", type=Path)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--processar", type=int, default=0)
    ap.add_argument("--paralelo", type=int, default=1)
    args = ap.parse_args()

    db.iniciar()
    streamer(args.streamer)  # valida o id
    dur = preparar(args.streamer, args.video, args.chat)
    print(f"Analisando {hms(dur)} de vídeo...")
    score, z = detector.analisar(args.streamer, 0, dur)
    picos = detector.achar_picos(score, z, 0)[: args.top]

    # CSV com o score por segundo (abre no Excel/Sheets para ver o gráfico)
    saida_csv = dirs()["backtest"] / f"score_{args.streamer}.csv"
    with open(saida_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["segundo", "tempo", "score", "audio", "chat", "reacao"])
        for i in range(len(score)):
            w.writerow([i, hms(i), f"{score[i]:.2f}", f"{z['audio'][i]:.2f}",
                        f"{z['chat'][i]:.2f}", f"{z['reacao'][i]:.2f}"])

    print(f"\nTop {len(picos)} momentos (limiar atual; score médio do vídeo "
          f"{np.mean(score):.2f}):\n")
    print(f"{'#':>2}  {'tempo':>8}  {'score':>5}  {'áudio':>5}  {'chat':>5}  {'reação':>6}")
    for i, p in enumerate(picos, 1):
        print(f"{i:>2}  {hms(p.t):>8}  {p.score:5.2f}  {p.audio:5.2f}  {p.chat:5.2f}  {p.reacao:6.2f}")
    print(f"\nScore por segundo salvo em {saida_csv}")
    print("Confira os tempos no player: se pelo menos 6 dos 10 primeiros forem bons, "
          "o detector está calibrado. Senão, ajuste pesos/limiar no config.yaml.")

    if args.processar:
        ids = detector.registrar_candidatos(args.streamer, picos[: args.processar], "backtest")
        print(f"\nGerando {len(ids)} cortes...")
        with ThreadPoolExecutor(max_workers=args.paralelo) as ex:
            list(ex.map(pipeline.processar, ids))
        with db.db() as con:
            for r in con.execute("SELECT id, status, titulo, arquivo, erro FROM candidatos "
                                 "WHERE id IN (%s)" % ",".join("?" * len(ids)), ids):
                extra = r["arquivo"] or ((r["erro"] or "").splitlines() or [""])[0]
                print(f"  #{r['id']} {r['status']:<10} {r['titulo'] or ''}  {extra}")
        print("\nAbra o painel para assistir: http://localhost:8080")


if __name__ == "__main__":
    main()
