"""Painel de aprovação: http://localhost:8080 (abre no celular também, na mesma rede).

Lista os cortes prontos; você assiste, aprova (vai para clips/aprovados) ou rejeita,
e pode editar título/gancho e mandar renderizar de novo.
"""
import shutil
import threading
import time
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException
from pydantic import BaseModel
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment

from app import db, metricas, pipeline
from app.config import cfg, dirs, streamer

app = FastAPI(title="Cortes GTA 6")
db.iniciar()
_lock_render = threading.Lock()

FILTROS = {
    "pronto": ("Para revisar", "status='pronto'"),
    "fila": ("Na fila", "status IN ('novo','processando')"),
    "aprovado": ("Aprovados", "status='aprovado'"),
    "descartado": ("Descartados pela IA", "status='descartado'"),
    "rejeitado": ("Rejeitados", "status='rejeitado'"),
    "erro": ("Com erro", "status='erro'"),
}

PAGINA = Environment(autoescape=True).from_string("""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cortes · {{ nome_canal }}</title>
<style>
 :root{--bg:#0e0f13;--card:#181a21;--txt:#e9e9ee;--mut:#8b8d98;--ok:#2fbf71;--no:#e0464f;--ac:#ffd400}
 *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.4 system-ui,sans-serif}
 header{position:sticky;top:0;background:#0e0f13ee;padding:12px 16px;border-bottom:1px solid #262833;z-index:2}
 h1{font-size:18px;margin:0 0 8px} .vivo{font-size:13px;color:var(--mut)} .vivo b{color:var(--no)}
 nav a{display:inline-block;margin:4px 6px 0 0;padding:5px 10px;border-radius:99px;background:#22242d;color:var(--txt);text-decoration:none;font-size:13px}
 nav a.on{background:var(--ac);color:#000;font-weight:600}
 main{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;padding:16px}
 .card{background:var(--card);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
 video{width:100%;aspect-ratio:9/16;background:#000;display:block}
 .info{padding:10px 12px;display:flex;flex-direction:column;gap:6px}
 .meta{font-size:12px;color:var(--mut)} .nota{color:var(--ac);font-weight:700}
 input{width:100%;padding:7px 9px;border-radius:8px;border:1px solid #2c2f3a;background:#0f1015;color:var(--txt);font:inherit}
 .bts{display:flex;gap:6px;flex-wrap:wrap} button,.bt{flex:1;padding:9px;border:0;border-radius:9px;font-weight:600;cursor:pointer;text-align:center;text-decoration:none;font-size:14px}
 .aprovar{background:var(--ok);color:#fff} .rejeitar{background:#2a2c35;color:var(--txt)} .sec{background:#2a2c35;color:var(--txt)}
 .vazio{padding:40px;text-align:center;color:var(--mut);grid-column:1/-1}
 pre{white-space:pre-wrap;font-size:11px;color:#f99;margin:0}
 #novos{display:none;position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--ac);color:#000;padding:10px 18px;border-radius:99px;font-weight:700;cursor:pointer}
</style></head><body>
<header>
 <h1>✂️ {{ nome_canal }} <a href="/escritorio" style="float:right;font-size:13px;color:var(--ac);text-decoration:none">🏢 Escritório</a></h1>
 <div class="vivo">{% for s in status_streamers %}{% if s.vivo %}<b>● AO VIVO</b>{% else %}○{% endif %} {{ s.nome }} &nbsp; {% endfor %}</div>
 <nav>{% for k, (rot, _) in filtros.items() %}<a href="/?f={{k}}" class="{{ 'on' if k==f }}">{{ rot }} ({{ contagem[k] }})</a>{% endfor %}</nav>
</header>
<main>
{% for c in cands %}
 <div class="card">
  {% if c.arquivo %}<video src="/video/{{c.id}}?v={{c.v}}" controls playsinline preload="metadata"></video>{% endif %}
  <div class="info">
   <div class="meta">#{{c.id}} · {{ c.nome }} · {{ c.quando }} · score {{ '%.1f'|format(c.score) }}
     {% if c.nota is not none %}· <span class="nota">nota {{ '%.0f'|format(c.nota) }}</span>{% endif %}
     {% if c.origem=='backtest' %}· teste{% endif %}</div>
   {% if c.status=='erro' %}<pre>{{ (c.erro or '')[:600] }}</pre>{% endif %}
   <form method="post" action="/c/{{c.id}}/editar">
     <input name="titulo" value="{{ c.titulo or '' }}" placeholder="título">
     <input name="gancho" value="{{ c.gancho or '' }}" placeholder="gancho (texto no topo)" style="margin-top:6px">
     <div class="bts" style="margin-top:6px"><button class="sec">↻ Renderizar de novo</button></div>
   </form>
   <div class="bts">
   {% if c.status=='pronto' %}
     <form method="post" action="/c/{{c.id}}/aprovar" style="flex:1"><button class="aprovar" style="width:100%">✔ Aprovar</button></form>
     <form method="post" action="/c/{{c.id}}/rejeitar" style="flex:1"><button class="rejeitar" style="width:100%">✖ Rejeitar</button></form>
   {% elif c.status=='aprovado' %}
     <a class="bt aprovar" href="/video/{{c.id}}?download=1">⬇ Baixar</a>
     <button class="sec" onclick="copiar({{c.id}})">📋 Copiar texto</button>
   {% elif c.status in ('rejeitado','descartado') %}
     <form method="post" action="/c/{{c.id}}/forcar" style="flex:1"><button class="sec" style="width:100%">Gerar mesmo assim</button></form>
   {% endif %}
   </div>
   {% if c.texto %}<textarea id="t{{c.id}}" hidden>{{ c.texto }}</textarea>{% endif %}
  </div>
 </div>
{% else %}<div class="vazio">Nada aqui ainda.</div>{% endfor %}
</main>
<div id="novos" onclick="location.reload()"></div>
<script>
function copiar(id){navigator.clipboard.writeText(document.getElementById('t'+id).value);alert('Copiado!')}
let ultimo={{ ultimo_id }};
setInterval(async()=>{try{const r=await (await fetch('/api/resumo')).json();
 if(r.ultimo_pronto>ultimo){const n=document.getElementById('novos');n.textContent='⚡ Corte novo! Toque para ver';n.style.display='block'}}catch(e){}},20000);
</script>
</body></html>""")


def _quando(ts: float) -> str:
    d = time.time() - ts
    if d < 3600:
        return f"há {int(d // 60)} min"
    if d < 86400:
        return f"há {int(d // 3600)} h"
    return f"há {int(d // 86400)} d"


def _get(cid: int):
    with db.db() as con:
        c = con.execute("SELECT * FROM candidatos WHERE id=?", (cid,)).fetchone()
    if c is None:
        raise HTTPException(404)
    return c


def _set(cid: int, **campos):
    sets = ", ".join(f"{k}=?" for k in campos)
    with db.db() as con:
        con.execute(f"UPDATE candidatos SET {sets} WHERE id=?", (*campos.values(), cid))


def _render_bg(cid: int):
    with _lock_render:          # um render por vez para não travar o PC
        pipeline.processar(cid, forcar=True)


@app.get("/", response_class=HTMLResponse)
def index(f: str = "pronto"):
    if f not in FILTROS:
        f = "pronto"
    with db.db() as con:
        contagem = {k: con.execute(f"SELECT count(*) FROM candidatos WHERE {w}").fetchone()[0]
                    for k, (_, w) in FILTROS.items()}
        rows = con.execute(f"SELECT * FROM candidatos WHERE {FILTROS[f][1]} "
                           "ORDER BY criado_em DESC, id DESC LIMIT 60").fetchall()
        ultimo = con.execute("SELECT coalesce(max(id),0) FROM candidatos "
                             "WHERE status='pronto'").fetchone()[0]
        vivos = {r["streamer"] for r in con.execute(
            "SELECT DISTINCT streamer FROM segmentos WHERE origem='live' "
            "AND inicio + duracao > ?", (time.time() - 600,))}
    cands = []
    for r in rows:
        c = dict(r)
        try:
            c["nome"] = streamer(c["streamer"])["nome"]
        except KeyError:
            c["nome"] = c["streamer"]
        c["quando"] = _quando(c["criado_em"])
        arq = Path(c["arquivo"]) if c["arquivo"] else None
        c["v"] = int(arq.stat().st_mtime) if arq and arq.exists() else 0
        txt = arq.with_suffix(".txt") if arq else None
        c["texto"] = txt.read_text(encoding="utf-8") if txt and txt.exists() else ""
        cands.append(c)
    status_streamers = [{"nome": s["nome"], "vivo": s["id"] in vivos} for s in cfg()["streamers"]]
    return PAGINA.render(cands=cands, f=f, filtros=FILTROS, contagem=contagem,
                         ultimo_id=ultimo, status_streamers=status_streamers,
                         nome_canal=cfg()["marca"]["nome_canal"])


@app.get("/video/{cid}")
def video(cid: int, download: int = 0):
    c = _get(cid)
    if not c["arquivo"] or not Path(c["arquivo"]).exists():
        raise HTTPException(404)
    p = Path(c["arquivo"])
    return FileResponse(p, media_type="video/mp4", filename=p.name if download else None)


@app.post("/c/{cid}/aprovar")
def aprovar(cid: int):
    c = _get(cid)
    origem = Path(c["arquivo"])
    destino = dirs()["aprovados"] / origem.name
    shutil.copy2(origem, destino)
    if origem.with_suffix(".txt").exists():
        shutil.copy2(origem.with_suffix(".txt"), destino.with_suffix(".txt"))
    _set(cid, status="aprovado", arquivo=str(destino))
    return RedirectResponse("/?f=pronto", status_code=303)


@app.post("/c/{cid}/rejeitar")
def rejeitar(cid: int):
    _get(cid)
    _set(cid, status="rejeitado")
    return RedirectResponse("/?f=pronto", status_code=303)


@app.post("/c/{cid}/editar")
def editar(cid: int, bg: BackgroundTasks, titulo: str = Form(""), gancho: str = Form("")):
    _get(cid)
    _set(cid, titulo=titulo.strip(), gancho=gancho.strip(), status="processando")
    bg.add_task(_render_bg, cid)
    return RedirectResponse("/?f=fila", status_code=303)


@app.post("/c/{cid}/forcar")
def forcar(cid: int, bg: BackgroundTasks):
    _get(cid)
    _set(cid, status="processando")
    bg.add_task(_render_bg, cid)
    return RedirectResponse("/?f=fila", status_code=303)


@app.get("/api/resumo")
def resumo():
    with db.db() as con:
        ultimo = con.execute("SELECT coalesce(max(id),0) FROM candidatos "
                             "WHERE status='pronto'").fetchone()[0]
    return JSONResponse({"ultimo_pronto": ultimo})


# ------------------------------------------------------------------ escritório
ESCRITORIO = Path(__file__).with_name("escritorio.html")
PLATS_OK = set(metricas.PLATAFORMAS)


class NovaMetrica(BaseModel):
    plataforma: str
    seguidores: int | None = None
    views: int | None = None
    receita_mes: float | None = None


@app.get("/escritorio", response_class=HTMLResponse)
def escritorio():
    return ESCRITORIO.read_text(encoding="utf-8")


@app.get("/api/escritorio")
def api_escritorio(bg: BackgroundTasks):
    # YouTube automático: atualiza em segundo plano se a última foto tiver mais de 1h
    if metricas.youtube_automatico():
        u = metricas.ultimo("youtube")
        if not u or u["fonte"] != "api" or time.time() - u["ts"] > 3600:
            bg.add_task(metricas.atualizar_youtube)
    return {
        "nome_canal": cfg()["marca"]["nome_canal"],
        "agentes": metricas.agentes(),
        "plataformas": metricas.resumo_plataformas(),
        "cortes_por_hora": metricas.cortes_por_hora(),
        "hoje": metricas.hoje(),
    }


@app.post("/api/metricas")
def api_metricas(m: NovaMetrica):
    if m.plataforma not in PLATS_OK:
        raise HTTPException(400, "plataforma inválida")
    metricas.registrar(m.plataforma, m.seguidores, m.views, m.receita_mes)
    return {"ok": True}
