"""Helpers para rodar ffmpeg/ffprobe com log e erro legível."""
import json
import logging
import shlex
import subprocess
from pathlib import Path

log = logging.getLogger("util")


class ErroComando(RuntimeError):
    pass


def rodar(cmd: list[str], cwd: Path | None = None, timeout: int | None = None) -> str:
    log.debug("$ %s", shlex.join(map(str, cmd)))
    p = subprocess.run(
        [str(c) for c in cmd], cwd=cwd, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0:
        cauda = "\n".join(p.stderr.strip().splitlines()[-15:])
        raise ErroComando(f"falhou ({p.returncode}): {shlex.join(map(str, cmd))}\n{cauda}")
    return p.stdout


def duracao(caminho: Path) -> float:
    out = rodar(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "json", caminho])
    return float(json.loads(out)["format"]["duration"])


def tem_audio(caminho: Path) -> bool:
    out = rodar(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=index", "-of", "csv=p=0", caminho])
    return bool(out.strip())


def hms(segundos: float) -> str:
    s = int(max(0, segundos))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
