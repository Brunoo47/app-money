"""Acha a facecam do streamer sozinho, em cada corte.

A posição da cam muda de live para live (e de jogo para jogo), então um retângulo fixo
no config não serve. Aqui: pega alguns quadros do corte, detecta rostos e fica com o rosto
que aparece PARADO no mesmo lugar na maioria dos quadros (webcam). Rosto de personagem do
jogo se mexe e não passa no filtro.

Resultado:
  {"tipo": "cam", "rect": {x, y, w, h}}   cam pequena num canto → cam em cima, gameplay embaixo
  {"tipo": "cheia", "cx": 960}            câmera em tela cheia (conversando) → vertical no rosto
  None                                    sem rosto estável → só gameplay
"""
import logging
import subprocess
from pathlib import Path

import numpy as np

from .util import duracao

log = logging.getLogger("facecam")

FW, FH = 1920, 1080
CAM_ASPECTO = 1080 / 672        # mesmo formato da faixa de cima do vertical (render.CAM_H)
N_QUADROS = 8
ROSTO_CHEIO = 0.20              # rosto maior que 20% da altura = câmera em tela cheia


def _quadros(video: Path, n: int) -> list[np.ndarray]:
    dur = duracao(video)
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", f"scale={FW}:{FH}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True)
        if p.returncode == 0 and len(p.stdout) == FW * FH:
            out.append(np.frombuffer(p.stdout, dtype=np.uint8).reshape(FH, FW))
    return out


def _agrupar(rostos: list[tuple[int, int, int, int]]) -> list[list]:
    """Junta detecções do mesmo rosto em quadros diferentes (centro perto e tamanho parecido)."""
    grupos: list[list] = []
    for r in rostos:
        x, y, s, _ = r
        cx, cy = x + s / 2, y + s / 2
        for g in grupos:
            gx, gy, gs = np.median([[a + b / 2, c + b / 2, b] for a, c, b, _ in g], axis=0)
            if abs(cx - gx) < gs * 0.5 and abs(cy - gy) < gs * 0.5 and 0.67 < s / gs < 1.5:
                g.append(r)
                break
        else:
            grupos.append([r])
    return grupos


def detectar(video: Path) -> dict | None:
    try:
        import cv2
    except ImportError:
        log.warning("opencv não instalado: sem facecam automática")
        return None
    cc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    quadros = _quadros(video, N_QUADROS)
    if len(quadros) < 3:
        return None
    rostos = []
    for qi, g in enumerate(quadros):
        for x, y, w, _h in cc.detectMultiScale(g, 1.1, 6, minSize=(40, 40)):
            rostos.append((int(x), int(y), int(w), qi))
    # Um grupo vale se aparece em pelo menos metade dos quadros (contando 1 vez por quadro).
    validos = [g for g in _agrupar(rostos) if len({r[3] for r in g}) >= len(quadros) / 2]
    if not validos:
        log.info("facecam: nenhum rosto estável, usando só gameplay")
        return None
    melhor = max(validos, key=lambda g: (len({r[3] for r in g}), np.median([r[2] for r in g])))
    x, y, s = (float(v) for v in np.median([[r[0], r[1], r[2]] for r in melhor], axis=0))
    cx, cy = x + s / 2, y + s / 2

    if s >= ROSTO_CHEIO * FH:
        log.info("facecam: câmera em tela cheia (rosto %.0fpx)", s)
        return {"tipo": "cheia", "cx": int(cx)}

    # Recorte em volta do rosto, no formato da faixa de cima. 2.4x o rosto pega cabeça,
    # ombros e um pouco do fundo (bate com o tamanho típico de uma cam de canto).
    h = min(FH, max(200.0, s * 2.4))
    w = min(FW, h * CAM_ASPECTO)
    h = w / CAM_ASPECTO
    rx = min(max(0.0, cx - w / 2), FW - w)
    ry = min(max(0.0, cy - h * 0.45), FH - h)
    rect = {"x": int(rx) // 2 * 2, "y": int(ry) // 2 * 2, "w": int(w) // 2 * 2, "h": int(h) // 2 * 2}
    log.info("facecam: rosto em (%.0f, %.0f) %.0fpx → cam %s", cx, cy, s, rect)
    return {"tipo": "cam", "rect": rect}
