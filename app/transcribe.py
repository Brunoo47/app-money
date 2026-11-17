"""Transcrição com faster-whisper, só das janelas dos candidatos (nunca da live inteira)."""
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("whisper")


@dataclass
class Palavra:
    inicio: float
    fim: float
    texto: str


@lru_cache
def _modelo():
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        log.warning("faster-whisper não instalado: cortes sairão sem legenda")
        return None
    device = os.environ.get("WHISPER_DEVICE", "auto")
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"
    padrao = "large-v3" if device == "cuda" else "small"
    nome = os.environ.get("WHISPER_MODEL", padrao)
    compute = "float16" if device == "cuda" else "int8"
    log.info("carregando Whisper %s em %s (%s)", nome, device, compute)
    return WhisperModel(nome, device=device, compute_type=compute)


def transcrever(audio_ou_video: Path) -> list[Palavra]:
    modelo = _modelo()
    if modelo is None:
        return []
    segs, _ = modelo.transcribe(
        str(audio_ou_video), language="pt", word_timestamps=True,
        vad_filter=False, beam_size=5,
    )
    palavras = []
    for s in segs:
        for w in s.words or []:
            t = w.word.strip()
            if t:
                palavras.append(Palavra(float(w.start), float(w.end), t))
    return palavras


def texto_com_tempos(palavras: list[Palavra], passo: float = 2.0) -> str:
    """Transcrição compacta com marcação de tempo, para mandar à IA."""
    linhas, atual, marca = [], [], None
    for p in palavras:
        if marca is None:
            marca = p.inicio
        atual.append(p.texto)
        if p.fim - marca >= passo:
            linhas.append(f"[{marca:5.1f}s] {' '.join(atual)}")
            atual, marca = [], None
    if atual:
        linhas.append(f"[{marca:5.1f}s] {' '.join(atual)}")
    return "\n".join(linhas)
