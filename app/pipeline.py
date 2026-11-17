"""Candidato → corte pronto: transcreve a janela, IA escolhe o trecho, renderiza."""
import json
import os
import logging
import re
import shutil
import traceback
import unicodedata
from pathlib import Path

from . import db, legenda, llm, render
from .config import caminho_marca, cfg, dirs, streamer
from .detector import intervalo_gravado
from .transcribe import Palavra, texto_com_tempos, transcrever

log = logging.getLogger("pipeline")


def slug(txt: str, maximo: int = 40) -> str:
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:maximo] or "corte"


def _url_credito(s: dict) -> str:
    url = s["plataformas"][0]["url"]
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/")


def _atualizar(cid: int, **campos):
    sets = ", ".join(f"{k}=?" for k in campos)
    with db.db() as con:
        con.execute(f"UPDATE candidatos SET {sets} WHERE id=?", (*campos.values(), cid))


def processar(cid: int, forcar: bool = False) -> None:
    """Processa um candidato. forcar=True renderiza mesmo se a IA descartar,
    e reaproveita título/gancho editados no painel."""
    with db.db() as con:
        c = con.execute("SELECT * FROM candidatos WHERE id=?", (cid,)).fetchone()
    if c is None:
        return
    _atualizar(cid, status="processando", erro=None)
    tmp = dirs()["tmp"] / f"c{cid}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        _processar(c, tmp, forcar)
    except Exception as e:
        log.error("candidato %s falhou: %s", cid, e)
        _atualizar(cid, status="erro", erro=f"{e}\n{traceback.format_exc()[-1500:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _processar(c, tmp: Path, forcar: bool):
    cid, sid = c["id"], c["streamer"]
    s = streamer(sid)
    conf_c, conf_j = cfg()["corte"], cfg()["detector"]["janela"]
    gravado = intervalo_gravado(sid, c["origem"])
    if gravado is None:
        raise RuntimeError("sem gravação para esse streamer")

    # 1) Janela larga em volta do pico
    ja = max(gravado[0], c["pico"] - conf_j["antes_s"])
    jb = min(gravado[1], c["pico"] + conf_j["depois_s"])
    reaproveitar = forcar and c["inicio"] is not None
    if reaproveitar:
        decisao = {"manter": True, "inicio": c["inicio"] - ja, "fim": c["fim"] - ja,
                   "titulo": c["titulo"], "gancho": c["gancho"] or "", "nota": c["nota"]}
        palavras_j: list[Palavra] = []
    else:
        wav = render.extrair(sid, ja, jb, tmp / "janela.wav", so_audio=True)
        palavras_j = transcrever(wav)
        decisao = llm.decidir(
            nome=s["nome"], dur=jb - ja, pico=c["pico"] - ja,
            sinais=json.loads(c["detalhe"] or "{}"),
            transcricao=texto_com_tempos(palavras_j),
            dmin=conf_c["duracao_min_s"], dmax=conf_c["duracao_max_s"],
            chat=chat_da_janela(sid, ja, jb),
            quadros=quadros_da_janela(sid, ja, jb, c["pico"]),
        )

    a, b = ja + decisao["inicio"], ja + decisao["fim"]
    nota = decisao.get("nota")
    _atualizar(cid, inicio=a, fim=b, titulo=decisao["titulo"], gancho=decisao["gancho"],
               nota=nota, transcricao=" ".join(p.texto for p in palavras_j) or c["transcricao"])

    if not forcar and (not decisao.get("manter", True)
                       or (nota is not None and nota < conf_c.get("nota_minima", 0))):
        log.info("candidato %s descartado pela IA (nota %s)", cid, nota)
        _atualizar(cid, status="descartado")
        return

    # 2) Recorte final + silêncio
    bruto = render.extrair(sid, a, b, tmp / "bruto.mp4")
    editado = render.remover_silencio(bruto, tmp / "editado.mp4")
    dur = render.duracao(editado)

    # 3) Layout deste corte (acha a facecam) + legenda do trecho já editado
    layout = render.resolver_layout(editado, s.get("layout", {}))
    palavras = transcrever(editado)
    fonte_arq = caminho_marca("fonte_arquivo")
    ass = legenda.gerar(
        tmp / "legenda.ass", palavras, dur, decisao.get("gancho") or "",
        credito=_url_credito(s), fonte=legenda.nome_fonte(fonte_arq),
        # Com facecam, o gancho vai na divisa cam/gameplay para não tapar o rosto.
        margem_gancho=(render.CAM_H - 50) if layout["modo"] == "cam" else 170,
    )

    # 4) Vertical final
    nome = f"{cid:05d}_{sid}_{slug(decisao['titulo'])}.mp4"
    saida = dirs()["candidatos"] / nome
    render.compor(editado, ass, saida, layout,
                  frame=caminho_marca("frame"),
                  fontes_dir=fonte_arq.parent if fonte_arq else None)
    escrever_txt(saida, decisao["titulo"], s)
    antigo = Path(c["arquivo"]) if c["arquivo"] else None
    if antigo and antigo != saida and antigo.parent == saida.parent:   # re-render com outro título
        antigo.unlink(missing_ok=True)
        antigo.with_suffix(".txt").unlink(missing_ok=True)
    _atualizar(cid, arquivo=str(saida), status="pronto")
    log.info("✔ corte %s pronto: %s (%.0fs)", cid, saida.name, dur)


def quadros_da_janela(sid: str, ja: float, jb: float, pico: float) -> list:
    """Quadros da tela para a IA ver o que está acontecendo. Mais densos antes do pico
    (onde costuma estar a coisa que causou a reação). Tempos relativos à janela."""
    conf = cfg().get("ia", {})
    n, largura = int(conf.get("quadros", 5)), int(conf.get("quadro_largura", 640))
    if n <= 0 or os.environ.get("LLM_PROVIDER", "none").lower() == "none":
        return []
    # Do começo da janela até um pouco depois do pico, com passo encolhendo perto do pico.
    a, b = ja, min(jb, pico + 5)
    tempos = [a + (b - a) * (1 - (1 - i / max(1, n - 1)) ** 1.5) for i in range(n)]
    quadros = []
    for t in tempos:
        jpg = render.quadro_jpeg(sid, t, largura)
        if jpg:
            quadros.append((t - ja, jpg))
    log.info("%d quadros para a IA (%.0f KB)", len(quadros), sum(len(q) for _, q in quadros) / 1024)
    return quadros


def chat_da_janela(sid: str, ja: float, jb: float, maximo: int = 80) -> str:
    """Mensagens do chat na janela, com tempo relativo ao vídeo (já com o atraso do chat)."""
    off = streamer(sid).get("chat_offset_s", -8)
    with db.db() as con:
        rows = con.execute("SELECT ts, msg FROM chat WHERE streamer=? AND ts >= ? AND ts < ? "
                           "ORDER BY ts", (sid, ja - off, jb - off)).fetchall()
    if len(rows) > maximo:   # chat lotado: amostra espalhada pela janela inteira
        passo = len(rows) / maximo
        rows = [rows[int(i * passo)] for i in range(maximo)]
    return "\n".join(f"{r['ts'] + off - ja:.0f}s: {(r['msg'] or '')[:100]}" for r in rows)


def escrever_txt(video: Path, titulo: str, s: dict) -> Path:
    m = cfg()["marca"]
    tags = " ".join(m.get("hashtags", []))
    texto = (f"{titulo}\n\n"
             f"Corte da live de {s['nome']}: {s['plataformas'][0]['url']}\n"
             f"Todos os créditos ao streamer.\n\n{tags}\n")
    txt = video.with_suffix(".txt")
    txt.write_text(texto, encoding="utf-8")
    return txt
