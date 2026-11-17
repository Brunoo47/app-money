"""Montagem do corte: recorta da gravação, tira silêncio e monta o vertical 9:16."""
import logging
import re
from pathlib import Path

from . import db
from .config import cfg
from .util import ErroComando, duracao, rodar, tem_audio

log = logging.getLogger("render")

W, H = 1080, 1920
CAM_H = 672                 # altura da facecam no layout com cam
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
AAC = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]


def segmentos_cobrindo(sid: str, a: float, b: float) -> list:
    with db.db() as con:
        return con.execute(
            "SELECT caminho, inicio, duracao FROM segmentos WHERE streamer=? "
            "AND inicio < ? AND inicio + duracao > ? ORDER BY inicio",
            (sid, b, a),
        ).fetchall()


def extrair(sid: str, a: float, b: float, saida: Path, so_audio: bool = False) -> Path:
    """Recorta [a, b] (relógio absoluto) da gravação, mesmo cruzando segmentos."""
    segs = segmentos_cobrindo(sid, a, b)
    if not segs:
        raise ErroComando(f"nenhuma gravação cobre {a:.0f}–{b:.0f} de {sid}")
    a = max(a, segs[0]["inicio"])
    dur = b - a
    saida_args = (["-vn", "-ac", "1", "-ar", "16000"] if so_audio else [*X264, *AAC])
    if len(segs) == 1:
        s = segs[0]
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{a - s['inicio']:.3f}",
               "-i", s["caminho"], "-t", f"{dur:.3f}", *saida_args, saida]
    else:
        lista = saida.with_suffix(".concat.txt")
        linhas = []
        for i, s in enumerate(segs):
            linhas.append(f"file '{Path(s['caminho']).resolve().as_posix()}'")
            if i == 0:
                linhas.append(f"inpoint {a - s['inicio']:.3f}")
        lista.write_text("\n".join(linhas) + "\n", encoding="utf-8")
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lista,
               "-t", f"{dur:.3f}", *saida_args, saida]
    rodar(cmd)
    return saida


def remover_silencio(entrada: Path, saida: Path) -> Path:
    """Corta pausas longas (silencio_min_s abaixo de silencio_db). Conservador de propósito:
    no GTA o barulho do jogo conta, então só some o que é silêncio de verdade."""
    c = cfg()["corte"]
    if not c.get("remover_silencio", True) or not tem_audio(entrada):
        return entrada
    log_ff = rodar_stderr(["ffmpeg", "-v", "info", "-i", entrada, "-af",
                           f"silencedetect=noise={c['silencio_db']}dB:d={c['silencio_min_s']}",
                           "-f", "null", "-"])
    inicios = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", log_ff)]
    fins = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log_ff)]
    total = duracao(entrada)
    if not inicios:
        return entrada
    fins += [total] * (len(inicios) - len(fins))
    margem = 0.15
    manter, cursor = [], 0.0
    for si, sf in zip(inicios, fins):
        if si + margem > cursor:
            manter.append((cursor, si + margem))
        cursor = max(cursor, sf - margem)
    if cursor < total:
        manter.append((cursor, total))
    manter = [(x, y) for x, y in manter if y - x > 0.1]
    removido = total - sum(y - x for x, y in manter)
    if removido < 0.5:
        return entrada
    expr = "+".join(f"between(t,{x:.3f},{y:.3f})" for x, y in manter)
    log.info("removendo %.1fs de silêncio", removido)
    rodar(["ffmpeg", "-y", "-v", "error", "-i", entrada,
           "-vf", f"select='{expr}',setpts=N/FRAME_RATE/TB",
           "-af", f"aselect='{expr}',asetpts=N/SR/TB",
           *X264, *AAC, saida])
    return saida


def rodar_stderr(cmd: list) -> str:
    import subprocess
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stderr


def _filtro_layout(layout: dict) -> str:
    g = layout.get("gameplay") or {"x": 0, "y": 0, "w": 1920, "h": 1080}
    crop_g = f"crop={g['w']}:{g['h']}:{g['x']}:{g['y']}"
    cam = layout.get("facecam")
    if cam:
        crop_c = f"crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']}"
        gp_h = H - CAM_H
        return (
            f"[0:v]split=2[c][g];"
            f"[c]{crop_c},scale={W}:{CAM_H}:force_original_aspect_ratio=increase,crop={W}:{CAM_H}[cam];"
            f"[g]{crop_g},scale=-2:{gp_h},crop={W}:{gp_h}[gp];"
            f"[cam][gp]vstack[base]"
        )
    zoom = float(layout.get("zoom", 1.35))
    fg_w = int(round(W * zoom / 2) * 2)
    return (
        f"[0:v]{crop_g},split=2[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
        f"boxblur=24:2,eq=brightness=-0.12[bg];"
        f"[b]scale={fg_w}:-2,crop={W}:ih[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[base]"
    )


def compor(entrada: Path, ass: Path | None, saida: Path, layout: dict,
           frame: Path | None, fontes_dir: Path | None) -> Path:
    """Monta o vídeo vertical final (layout + moldura + legenda + áudio normalizado)."""
    entradas = ["-i", entrada]
    grafo = _filtro_layout(layout)
    ultimo = "base"
    if frame:
        entradas += ["-i", frame]
        grafo += f";[1:v]scale={W}:{H}[moldura];[{ultimo}][moldura]overlay=0:0[comframe]"
        ultimo = "comframe"
    if ass:
        fd = f":fontsdir={fontes_dir.as_posix()}" if fontes_dir else ""
        grafo += f";[{ultimo}]ass={ass.name}{fd}[comleg]"
        ultimo = "comleg"
    grafo += f";[{ultimo}]fps=30,format=yuv420p[v]"
    audio = []
    if tem_audio(entrada):
        audio = ["-map", "0:a", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", *AAC]
    rodar(["ffmpeg", "-y", "-v", "error", *entradas, "-filter_complex", grafo,
           "-map", "[v]", *audio, *X264, "-movflags", "+faststart",
           saida.resolve()], cwd=ass.parent if ass else None)
    return saida
