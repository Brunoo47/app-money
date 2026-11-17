"""Salva um frame do vídeo com grade de 100px, para medir onde fica a facecam.

  python -m tools.frame data/backtest/vod.mp4 00:10:00
→ data/frame.png. Abra, veja os números da grade e preencha layout.facecam no config.yaml.
"""
import sys

from app.config import DATA_DIR
from app.util import rodar


def main():
    video, tempo = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "00:01:00")
    saida = DATA_DIR / "frame.png"
    grade = "drawgrid=w=100:h=100:t=1:c=red@0.6"
    rotulos = ",".join(
        f"drawtext=text='{x}':x={x}+3:y=3:fontsize=18:fontcolor=yellow:box=1:boxcolor=black@0.6"
        for x in range(0, 1920, 200)
    ) + "," + ",".join(
        f"drawtext=text='{y}':x=3:y={y}+3:fontsize=18:fontcolor=yellow:box=1:boxcolor=black@0.6"
        for y in range(200, 1080, 200)
    )
    rodar(["ffmpeg", "-y", "-v", "error", "-ss", tempo, "-i", video, "-frames:v", "1",
           "-vf", f"scale=1920:1080,{grade},{rotulos}", saida])
    print(f"salvo em {saida}")


if __name__ == "__main__":
    main()
