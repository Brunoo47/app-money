"""Gera o arquivo .ass com: legenda palavra por palavra, gancho no topo e crédito."""
from pathlib import Path

from .config import cfg
from .transcribe import Palavra

W, H = 1080, 1920


def cor_ass(hex_rgb: str, alfa: int = 0) -> str:
    """#RRGGBB → &HAABBGGRR (formato do ASS)."""
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alfa:02X}{b}{g}{r}".upper()


def _t(s: float) -> str:
    s = max(0.0, s)
    cs = int(round(s * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(txt: str) -> str:
    return txt.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def nome_fonte(arquivo: Path | None) -> str:
    if arquivo is None:
        return "DejaVu Sans"
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(str(arquivo), lazy=True)
        nome = f["name"].getDebugName(1)
        return nome or "DejaVu Sans"
    except Exception:
        return "DejaVu Sans"


def gerar(destino: Path, palavras: list[Palavra], duracao: float, gancho: str,
          credito: str, fonte: str, margem_gancho: int = 170) -> Path:
    m = cfg()["marca"]
    principal = cor_ass(m.get("cor_principal", "#FFFFFF"))
    destaque = cor_ass(m.get("cor_destaque", "#FFD400"))
    fundo_gancho = cor_ass(m.get("cor_gancho_fundo", "#E01E37"))
    preto = "&H00000000"

    cab = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Legenda,{fonte},92,{principal},{principal},{preto},&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,560,1
Style: Gancho,{fonte},74,&H00FFFFFF,&H00FFFFFF,{fundo_gancho},{fundo_gancho},-1,0,0,0,100,100,0,0,3,18,0,8,80,80,{margem_gancho},1
Style: Credito,{fonte},40,&H00FFFFFF,&H00FFFFFF,{preto},&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,430,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    # Legenda: blocos de até 3 palavras; a palavra falada fica na cor de destaque.
    blocos: list[list[Palavra]] = []
    for p in palavras:
        if (not blocos or len(blocos[-1]) >= 3
                or p.inicio - blocos[-1][-1].fim > 0.6
                or blocos[-1][-1].texto.endswith((".", "?", "!"))):
            blocos.append([])
        blocos[-1].append(p)
    for bi, bloco in enumerate(blocos):
        fim_bloco = bloco[-1].fim + 0.25
        if bi + 1 < len(blocos):
            fim_bloco = min(fim_bloco, blocos[bi + 1][0].inicio)
        for i, p in enumerate(bloco):
            a = p.inicio
            b = bloco[i + 1].inicio if i + 1 < len(bloco) else fim_bloco
            if b <= a:
                continue
            partes = []
            for j, q in enumerate(bloco):
                txt = _esc(q.texto.upper())
                partes.append(f"{{\\c{destaque}}}{txt}{{\\c{principal}}}" if j == i else txt)
            pop = "{\\fscx108\\fscy108\\t(0,80,\\fscx100\\fscy100)}" if i == 0 else ""
            ev.append(f"Dialogue: 0,{_t(a)},{_t(b)},Legenda,,0,0,0,,{pop}{' '.join(partes)}")

    if gancho:
        ev.append(f"Dialogue: 1,{_t(0)},{_t(min(3.0, duracao))},Gancho,,0,0,0,,"
                  f"{{\\fad(0,200)}}{_esc(gancho.upper())}")
    if credito:
        ev.append(f"Dialogue: 1,{_t(0)},{_t(duracao)},Credito,,0,0,0,,{_esc(credito)}")

    destino.write_text(cab + "\n".join(ev) + "\n", encoding="utf-8")
    return destino
