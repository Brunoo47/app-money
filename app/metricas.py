"""Números do canal (seguidores, views, receita) e status dos "funcionários" do escritório.

YouTube: seguidores e views vêm sozinhos pela API pública (YOUTUBE_API_KEY + YOUTUBE_CHANNEL_ID).
TikTok e Instagram: por enquanto você atualiza pelo painel (as APIs deles exigem app aprovado).
Receita: manual em todas (a do YouTube exige login OAuth + canal monetizado).
"""
import logging
import os
import time

import httpx

from . import db
from .config import cfg

log = logging.getLogger("metricas")
PLATAFORMAS = ["youtube", "tiktok", "instagram"]


# ------------------------------------------------------------------ plataformas
def registrar(plataforma: str, seguidores=None, views=None, receita_mes=None, fonte="manual"):
    """Salva uma foto nova. Campo vazio = repete o último valor conhecido."""
    ult = ultimo(plataforma) or {}
    with db.db() as con:
        con.execute(
            "INSERT INTO metricas(plataforma, ts, seguidores, views, receita_mes, fonte) "
            "VALUES (?,?,?,?,?,?)",
            (plataforma, time.time(),
             seguidores if seguidores is not None else ult.get("seguidores"),
             views if views is not None else ult.get("views"),
             receita_mes if receita_mes is not None else ult.get("receita_mes"),
             fonte),
        )


def ultimo(plataforma: str, antes_de: float | None = None) -> dict | None:
    q = "SELECT * FROM metricas WHERE plataforma=?"
    args: list = [plataforma]
    if antes_de is not None:
        q += " AND ts <= ?"
        args.append(antes_de)
    with db.db() as con:
        r = con.execute(q + " ORDER BY ts DESC LIMIT 1", args).fetchone()
    return dict(r) if r else None


def atualizar_youtube() -> bool:
    chave, canal = os.environ.get("YOUTUBE_API_KEY"), os.environ.get("YOUTUBE_CHANNEL_ID")
    if not chave or not canal:
        return False
    try:
        r = httpx.get("https://www.googleapis.com/youtube/v3/channels", timeout=20,
                      params={"part": "statistics", "id": canal, "key": chave})
        r.raise_for_status()
        st = r.json()["items"][0]["statistics"]
        registrar("youtube", seguidores=int(st.get("subscriberCount", 0)),
                  views=int(st.get("viewCount", 0)), fonte="api")
        return True
    except Exception as e:
        log.warning("não consegui atualizar o YouTube: %s", e)
        return False


def youtube_automatico() -> bool:
    return bool(os.environ.get("YOUTUBE_API_KEY") and os.environ.get("YOUTUBE_CHANNEL_ID"))


def resumo_plataformas() -> list[dict]:
    agora = time.time()
    out = []
    for p in PLATAFORMAS:
        u = ultimo(p) or {}
        ontem = ultimo(p, antes_de=agora - 86400) or {}
        def delta(campo):
            if u.get(campo) is None or ontem.get(campo) is None:
                return None
            return u[campo] - ontem[campo]
        out.append({
            "plataforma": p,
            "seguidores": u.get("seguidores"),
            "views": u.get("views"),
            "receita_mes": u.get("receita_mes"),
            "delta_seguidores": delta("seguidores"),
            "delta_views": delta("views"),
            "atualizado": u.get("ts"),
            "automatico": p == "youtube" and youtube_automatico(),
        })
    return out


# ------------------------------------------------------------------ escritório
def _inicio_do_dia() -> float:
    t = time.localtime()
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def _ha(segundos: float) -> str:
    m = int(segundos // 60)
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def agentes() -> list[dict]:
    """Cada parte do sistema vira um 'funcionário' com status real."""
    agora, hoje = time.time(), _inicio_do_dia()
    llm_ligada = os.environ.get("LLM_PROVIDER", "none").lower() != "none"
    with db.db() as con:
        def um(q, *a):
            return con.execute(q, a).fetchone()[0]
        gravando = {}
        for s in cfg()["streamers"]:
            r = con.execute(
                "SELECT max(inicio + duracao) fim FROM segmentos WHERE streamer=? AND origem='live'",
                (s["id"],)).fetchone()
            ativo = r["fim"] is not None and agora - r["fim"] < 240
            desde = None
            if ativo:   # início da sessão atual = primeiro segmento sem buraco grande
                segs = con.execute(
                    "SELECT inicio, duracao FROM segmentos WHERE streamer=? AND origem='live' "
                    "AND inicio > ? ORDER BY inicio DESC", (s["id"], agora - 86400)).fetchall()
                desde = segs[0]["inicio"]
                for a, b in zip(segs, segs[1:]):
                    if a["inicio"] - (b["inicio"] + b["duracao"]) > 300:
                        break
                    desde = b["inicio"]
            gravando[s["id"]] = (ativo, desde)
        msgs_5min = um("SELECT count(*) FROM chat WHERE ts > ?", agora - 300)
        momentos_hoje = um("SELECT count(*) FROM candidatos WHERE origem='live' AND criado_em >= ?", hoje)
        processando = con.execute(
            "SELECT id, streamer FROM candidatos WHERE status='processando' LIMIT 1").fetchone()
        fila = um("SELECT count(*) FROM candidatos WHERE status IN ('novo','processando')")
        prontos_hoje = um("SELECT count(*) FROM candidatos WHERE status IN ('pronto','aprovado','rejeitado') "
                          "AND criado_em >= ?", hoje)
        titulos_hoje = um("SELECT count(*) FROM candidatos WHERE nota IS NOT NULL AND criado_em >= ?", hoje)
        esperando = um("SELECT count(*) FROM candidatos WHERE status='pronto'")
        aprovados_hoje = um("SELECT count(*) FROM candidatos WHERE status='aprovado' AND criado_em >= ?", hoje)

    alguem_ao_vivo = any(a for a, _ in gravando.values())
    lista = []
    for s in cfg()["streamers"]:
        ativo, desde = gravando[s["id"]]
        lista.append({
            "id": f"grav-{s['id']}", "nome": s["nome"].split()[0], "papel": "Gravador",
            "trabalhando": ativo,
            "linha": f"AO VIVO · {_ha(agora - desde)}" if ativo else "offline",
            "valor": f"AO VIVO {_ha(agora - desde)}" if ativo else "offline",
            "tom": "alerta" if ativo else "neutro", "alerta": ativo,
        })
    lista += [
        {"id": "chat", "nome": "Chat", "papel": "Ouvinte do chat", "trabalhando": msgs_5min > 0,
         "linha": f"{msgs_5min // 5} msgs/min" if msgs_5min else "silêncio",
         "valor": f"{msgs_5min // 5}/min", "tom": "ok" if msgs_5min else "neutro"},
        {"id": "detector", "nome": "Radar", "papel": "Detector", "trabalhando": alguem_ao_vivo,
         "linha": f"{momentos_hoje} momentos hoje",
         "valor": f"+{momentos_hoje}", "tom": "ok" if momentos_hoje else "neutro"},
        {"id": "ia", "nome": "Roteirista", "papel": "IA de títulos",
         "trabalhando": bool(processando) and llm_ligada,
         "linha": f"{titulos_hoje} títulos hoje" if llm_ligada else "IA desligada",
         "valor": f"+{titulos_hoje}" if llm_ligada else "off", "tom": "ok" if llm_ligada else "neutro"},
        {"id": "editor", "nome": "Editor", "papel": "Render",
         "trabalhando": bool(processando),
         "linha": (f"editando #{processando['id']}" if processando else f"{prontos_hoje} cortes hoje")
                  + (f" · fila {fila}" if fila > 1 else ""),
         "valor": f"#{processando['id']}" if processando else f"+{prontos_hoje}",
         "tom": "ok" if (processando or prontos_hoje) else "neutro"},
        {"id": "voce", "nome": "Você", "papel": "Aprovação", "chefe": True,
         "trabalhando": esperando > 0,
         "linha": f"{esperando} esperando você" if esperando else f"{aprovados_hoje} aprovados hoje",
         "valor": f"{esperando} na fila" if esperando else f"+{aprovados_hoje}",
         "tom": "alerta" if esperando else "ok", "alerta": esperando > 0},
    ]
    return lista


def cortes_por_hora() -> list[int]:
    """Cortes gerados em cada hora de hoje (para as telas da parede)."""
    hoje = _inicio_do_dia()
    horas = [0] * 24
    with db.db() as con:
        for r in con.execute("SELECT criado_em FROM candidatos WHERE criado_em >= ? "
                             "AND status IN ('pronto','aprovado','rejeitado')", (hoje,)):
            horas[time.localtime(r["criado_em"]).tm_hour] += 1
    return horas


def hoje() -> dict:
    h = _inicio_do_dia()
    with db.db() as con:
        def um(q):
            return con.execute(q, (h,)).fetchone()[0]
        return {
            "gerados": um("SELECT count(*) FROM candidatos WHERE criado_em >= ? "
                          "AND status IN ('pronto','aprovado','rejeitado')"),
            "aprovados": um("SELECT count(*) FROM candidatos WHERE criado_em >= ? AND status='aprovado'"),
            "descartados": um("SELECT count(*) FROM candidatos WHERE criado_em >= ? AND status='descartado'"),
        }


def historico_seguidores(dias: int = 14) -> list[dict]:
    """Total de seguidores (somando plataformas) no fim de cada dia, para o gráfico do telão."""
    fim_hoje = _inicio_do_dia() + 86400
    out = []
    for i in range(dias - 1, -1, -1):
        fim = fim_hoje - i * 86400
        total, achou = 0, False
        for p in PLATAFORMAS:
            u = ultimo(p, antes_de=fim)
            if u and u.get("seguidores") is not None:
                total += u["seguidores"]
                achou = True
        if achou:
            out.append({"dia": time.strftime("%d/%m", time.localtime(fim - 1)), "seguidores": total})
    return out
