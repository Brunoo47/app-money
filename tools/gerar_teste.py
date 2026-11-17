"""Gera um 'VOD' sintético + chat para testar o pipeline sem internet.

Vídeo de 10 min com 3 explosões de áudio (2:00, 5:00, 7:50) e o chat
explodindo em KKKK ~8s depois de cada uma. O backtest tem que achar as 3.

  python -m tools.gerar_teste && python -m tools.backtest --streamer davyjones \
      --video data/backtest/teste.mp4 --chat data/backtest/teste.chat.jsonl --processar 3
"""
import json
import random
import subprocess

from app.config import dirs

DUR = 600
EVENTOS = [120, 300, 470]


def main():
    pasta = dirs()["backtest"]
    video = pasta / "teste.mp4"
    # Áudio: ruído baixo constante + tom alto de 4s em cada evento.
    vol = "+".join(f"between(t,{e},{e + 4})" for e in EVENTOS)
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={DUR}",
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.03:duration={DUR}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={DUR}",
        "-filter_complex",
        f"[2:a]volume='if({vol},0.9,0)':eval=frame[tom];[1:a][tom]amix=2:normalize=0[a]",
        "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "30", "-c:a", "aac", str(video),
    ], check=True)

    rnd = random.Random(42)
    msgs = []
    for s in range(DUR):
        for _ in range(rnd.randint(0, 2)):
            msgs.append({"t": s + rnd.random(), "autor": "fulano", "msg": "salve"})
    for e in EVENTOS:
        for s in range(e + 6, e + 16):
            for _ in range(rnd.randint(8, 15)):
                msgs.append({"t": s + rnd.random(), "autor": "x", "msg": "KKKKKKKK"})
    msgs.sort(key=lambda m: m["t"])
    chat = pasta / "teste.chat.jsonl"
    chat.write_text("\n".join(json.dumps(m) for m in msgs) + "\n", encoding="utf-8")
    print(f"ok: {video} e {chat} (eventos em {', '.join(f'{e // 60}:{e % 60:02d}' for e in EVENTOS)})")


if __name__ == "__main__":
    main()
