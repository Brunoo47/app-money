"""Postagem no YouTube Shorts e no TikTok (só corte que o Bruno aprovou no painel).

Fluxo: botão "Postar" no painel → linha na tabela `postagens` (status 'fila') → o carteiro
(thread dentro do painel) posta um por vez, respeitando `publicacao.intervalo_min` por plataforma.

Login: cada plataforma usa OAuth (a conta do canal autoriza o nosso app uma vez pelo painel).
Os tokens ficam na tabela `estado` (data/db.sqlite, que nunca vai para o git).

Atenção: app sem auditoria só posta em PRIVADO (YouTube trava o vídeo como privado; TikTok só
aceita SELF_ONLY). Depois da auditoria, trocar a privacidade no config.yaml.
"""
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from . import db
from .config import cfg

log = logging.getLogger("publisher")
PLATAFORMAS = ("youtube", "tiktok")
NOMES = {"youtube": "YouTube", "tiktok": "TikTok"}

YT_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
YT_TOKEN = "https://oauth2.googleapis.com/token"
YT_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
YT_API = "https://www.googleapis.com/youtube/v3"
YT_ESCOPOS = ("https://www.googleapis.com/auth/youtube.upload "
              "https://www.googleapis.com/auth/youtube.readonly")

TT_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
TT_API = "https://open.tiktokapis.com/v2"
TT_ESCOPOS = "user.info.basic,video.publish"

SCHEMA = """
CREATE TABLE IF NOT EXISTS postagens (
    id INTEGER PRIMARY KEY,
    candidato INTEGER NOT NULL,
    plataforma TEXT NOT NULL,          -- youtube | tiktok
    status TEXT NOT NULL,              -- fila | enviando | postado | erro
    url TEXT, erro TEXT,
    criado_em REAL NOT NULL, postado_em REAL,
    UNIQUE(candidato, plataforma)
);
"""

_estados_oauth: dict[str, str] = {}    # state do OAuth → plataforma (anti-CSRF)
_pausa: dict[str, float] = {}          # plataforma → só tenta de novo depois disso (10 min após erro)


class ErroPost(Exception):
    pass


def iniciar():
    with db.db() as con:
        con.executescript(SCHEMA)
        # Envio que estava no meio quando o painel caiu: pode ter subido ou não.
        # Não repete sozinho (evita vídeo duplicado): vira erro para o Bruno conferir.
        con.execute("UPDATE postagens SET status='erro', erro=? WHERE status='enviando'",
                    ("envio interrompido (painel reiniciou). Confira no canal antes de tentar de novo.",))


def _conf() -> dict:
    return cfg().get("publicacao", {})


def plataformas_ativas() -> list[str]:
    return [p for p in _conf().get("plataformas", list(PLATAFORMAS)) if p in PLATAFORMAS]


def base_painel() -> str:
    return os.environ.get("PAINEL_URL", f"http://localhost:{cfg()['painel']['porta']}").rstrip("/")


# ------------------------------------------------------------------ tokens
def _tok(plat: str) -> dict | None:
    v = db.get_estado(f"token:{plat}")
    return json.loads(v) if v else None


def _salvar_tok(plat: str, t: dict):
    db.set_estado(f"token:{plat}", json.dumps(t))


def desconectar(plat: str):
    with db.db() as con:
        con.execute("DELETE FROM estado WHERE chave=?", (f"token:{plat}",))


def credenciais_ok(plat: str) -> bool:
    if plat == "youtube":
        return bool(os.environ.get("YOUTUBE_CLIENT_ID") and os.environ.get("YOUTUBE_CLIENT_SECRET"))
    return bool(os.environ.get("TIKTOK_CLIENT_KEY") and os.environ.get("TIKTOK_CLIENT_SECRET"))


def conexoes() -> list[dict]:
    """Para o painel: cada plataforma ativa, se tem chave no .env e se está conectada."""
    out = []
    for p in plataformas_ativas():
        t = _tok(p)
        out.append({"plat": p, "nome": NOMES[p], "chaves": credenciais_ok(p),
                    "conectado": bool(t), "conta": (t or {}).get("conta", "")})
    return out


def _redirect(plat: str) -> str:
    if plat == "tiktok" and os.environ.get("TIKTOK_REDIRECT_URI"):
        return os.environ["TIKTOK_REDIRECT_URI"]
    return f"{base_painel()}/oauth/{plat}/volta"


def url_login(plat: str) -> str:
    estado = secrets.token_urlsafe(16)
    _estados_oauth[estado] = plat
    if plat == "youtube":
        q = {"client_id": os.environ["YOUTUBE_CLIENT_ID"], "redirect_uri": _redirect(plat),
             "response_type": "code", "scope": YT_ESCOPOS, "access_type": "offline",
             "prompt": "consent", "state": estado}
        return f"{YT_AUTH}?{urlencode(q)}"
    q = {"client_key": os.environ["TIKTOK_CLIENT_KEY"], "redirect_uri": _redirect(plat),
         "response_type": "code", "scope": TT_ESCOPOS, "state": estado}
    return f"{TT_AUTH}?{urlencode(q)}"


def codigo_de_url(url: str) -> tuple[str, str]:
    """Link colado pelo Bruno (TikTok com redirect fora do painel) → (code, state)."""
    q = parse_qs(urlparse(url.strip()).query)
    if "code" not in q:
        raise ErroPost("esse link não tem '?code=': copie o endereço inteiro da página que abriu depois de autorizar")
    return q["code"][0], q.get("state", [""])[0]


def concluir_login(plat: str, code: str, estado: str):
    if _estados_oauth.pop(estado, None) != plat:
        raise ErroPost("login expirado ou de outra aba: clique em conectar de novo")
    if plat == "youtube":
        r = httpx.post(YT_TOKEN, timeout=30, data={
            "code": code, "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "redirect_uri": _redirect(plat), "grant_type": "authorization_code"})
        j = r.json()
        if r.status_code != 200 or "refresh_token" not in j:
            raise ErroPost(f"Google recusou o login: {j.get('error_description') or j}")
        t = {"access_token": j["access_token"], "refresh_token": j["refresh_token"],
             "expira": time.time() + j.get("expires_in", 3600) - 60}
        try:
            c = httpx.get(f"{YT_API}/channels", params={"part": "snippet", "mine": "true"},
                          headers={"Authorization": f"Bearer {t['access_token']}"}, timeout=20).json()
            t["conta"] = c["items"][0]["snippet"]["title"]
        except Exception:
            t["conta"] = "canal conectado"
    else:
        r = httpx.post(f"{TT_API}/oauth/token/", timeout=30, data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "code": code, "grant_type": "authorization_code", "redirect_uri": _redirect(plat)})
        j = r.json()
        if "access_token" not in j:
            raise ErroPost(f"TikTok recusou o login: {j.get('error_description') or j}")
        t = {"access_token": j["access_token"], "refresh_token": j["refresh_token"],
             "expira": time.time() + j.get("expires_in", 86400) - 60}
        try:
            u = httpx.get(f"{TT_API}/user/info/", params={"fields": "display_name"},
                          headers={"Authorization": f"Bearer {t['access_token']}"}, timeout=20).json()
            t["conta"] = u["data"]["user"]["display_name"]
        except Exception:
            t["conta"] = "conta conectada"
    _salvar_tok(plat, t)
    log.info("✔ %s conectado (%s)", NOMES[plat], t["conta"])


def _access(plat: str) -> str:
    """Token válido, renovando com o refresh_token quando venceu."""
    t = _tok(plat)
    if not t:
        raise ErroPost(f"{NOMES[plat]} não está conectado (painel → Conectar {NOMES[plat]})")
    if time.time() < t["expira"]:
        return t["access_token"]
    if plat == "youtube":
        r = httpx.post(YT_TOKEN, timeout=30, data={
            "client_id": os.environ["YOUTUBE_CLIENT_ID"],
            "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
            "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    else:
        r = httpx.post(f"{TT_API}/oauth/token/", timeout=30, data={
            "client_key": os.environ["TIKTOK_CLIENT_KEY"],
            "client_secret": os.environ["TIKTOK_CLIENT_SECRET"],
            "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    j = r.json()
    if "access_token" not in j:
        raise ErroPost(f"login do {NOMES[plat]} venceu, conecte de novo ({j.get('error_description') or j.get('error')})")
    t["access_token"] = j["access_token"]
    t["expira"] = time.time() + j.get("expires_in", 3600) - 60
    if j.get("refresh_token"):            # TikTok gira o refresh_token
        t["refresh_token"] = j["refresh_token"]
    _salvar_tok(plat, t)
    return t["access_token"]


# ------------------------------------------------------------------ envio
def _textos(c: dict, plat: str) -> tuple[str, str]:
    """(título, descrição). A descrição é o .txt do corte (título + crédito + hashtags);
    se a plataforma tem hashtags próprias no config (publicacao.<plat>.hashtags), troca as do .txt."""
    arq = Path(c["arquivo"])
    titulo = (c["titulo"] or arq.stem).strip()
    txt = arq.with_suffix(".txt")
    desc = txt.read_text(encoding="utf-8").strip() if txt.exists() else titulo
    tags = _conf().get(plat, {}).get("hashtags")
    if tags:
        linhas = desc.splitlines()
        if linhas and linhas[-1].lstrip().startswith("#"):
            linhas = linhas[:-1]
        desc = "\n".join(linhas).rstrip() + "\n\n" + " ".join(tags)
    return titulo, desc + "\n"


def postar_youtube(c: dict) -> str:
    tok = _access("youtube")
    titulo, desc = _textos(c, "youtube")
    titulo = titulo.replace("<", "").replace(">", "")[:100]
    desc = desc.replace("<", "").replace(">", "")[:4900]
    tags = [h.lstrip("#") for h in cfg()["marca"].get("hashtags", [])]
    meta = {"snippet": {"title": titulo, "description": desc, "tags": tags,
                        "categoryId": "20", "defaultLanguage": "pt-BR"},   # 20 = Jogos
            "status": {"privacyStatus": _conf().get("youtube", {}).get("privacidade", "private"),
                       "selfDeclaredMadeForKids": False}}
    dados = Path(c["arquivo"]).read_bytes()
    r = httpx.post(YT_UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"},
                   json=meta, timeout=60,
                   headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": "video/mp4",
                            "X-Upload-Content-Length": str(len(dados))})
    if r.status_code != 200:
        raise ErroPost(f"YouTube recusou ({r.status_code}): {r.text[:400]}")
    r = httpx.put(r.headers["Location"], content=dados, timeout=900,
                  headers={"Authorization": f"Bearer {tok}", "Content-Type": "video/mp4"})
    if r.status_code not in (200, 201):
        raise ErroPost(f"upload no YouTube falhou ({r.status_code}): {r.text[:400]}")
    return f"https://youtube.com/shorts/{r.json()['id']}"


def _tt(tok: str, caminho: str, corpo: dict) -> dict:
    r = httpx.post(f"{TT_API}{caminho}", json=corpo, timeout=60,
                   headers={"Authorization": f"Bearer {tok}",
                            "Content-Type": "application/json; charset=UTF-8"})
    j = r.json()
    err = j.get("error", {})
    if err.get("code", "ok") != "ok":
        raise ErroPost(f"TikTok: {err.get('code')} {err.get('message')}")
    return j.get("data", {})


def postar_tiktok(c: dict) -> str:
    tok = _access("tiktok")
    _, desc = _textos(c, "tiktok")
    criador = _tt(tok, "/post/publish/creator_info/query/", {})
    opcoes = criador.get("privacy_level_options", [])
    priv = _conf().get("tiktok", {}).get("privacidade", "SELF_ONLY")
    if priv not in opcoes:
        log.warning("TikTok não deixa privacidade %s (opções: %s); usando SELF_ONLY", priv, opcoes)
        priv = "SELF_ONLY"
    dados = Path(c["arquivo"]).read_bytes()
    n = len(dados)
    ini = _tt(tok, "/post/publish/video/init/", {
        "post_info": {"title": desc[:2200], "privacy_level": priv,
                      "disable_duet": False, "disable_comment": False, "disable_stitch": False,
                      "video_cover_timestamp_ms": 1000},
        # corte tem poucos MB: vai num pedaço só (TikTok aceita até 64 MB por pedaço)
        "source_info": {"source": "FILE_UPLOAD", "video_size": n, "chunk_size": n,
                        "total_chunk_count": 1}})
    r = httpx.put(ini["upload_url"], content=dados, timeout=900,
                  headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{n - 1}/{n}"})
    if r.status_code not in (200, 201):
        raise ErroPost(f"upload no TikTok falhou ({r.status_code}): {r.text[:400]}")
    # O TikTok processa depois do upload: espera até 5 min pelo resultado.
    for _ in range(60):
        time.sleep(5)
        st = _tt(tok, "/post/publish/status/fetch/", {"publish_id": ini["publish_id"]})
        if st.get("status") == "PUBLISH_COMPLETE":
            ids = st.get("publicaly_available_post_id") or []
            return f"https://www.tiktok.com/video/{ids[0]}" if ids else "postado (privado)"
        if st.get("status") == "FAILED":
            raise ErroPost(f"TikTok recusou o vídeo: {st.get('fail_reason')}")
    return "enviado (TikTok ainda processando)"


ENVIAR = {"youtube": postar_youtube, "tiktok": postar_tiktok}


# ------------------------------------------------------------------ fila
def enfileirar(cid: int, plats: list[str] | None = None) -> list[str]:
    """Põe o corte na fila das plataformas (as que já foram postadas são ignoradas)."""
    novas = []
    with db.db() as con:
        for p in plats or plataformas_ativas():
            r = con.execute("SELECT status FROM postagens WHERE candidato=? AND plataforma=?",
                            (cid, p)).fetchone()
            if r is None:
                con.execute("INSERT INTO postagens(candidato, plataforma, status, criado_em) "
                            "VALUES (?,?,'fila',?)", (cid, p, time.time()))
            elif r["status"] == "erro":     # tentar de novo
                con.execute("UPDATE postagens SET status='fila', erro=NULL, criado_em=? "
                            "WHERE candidato=? AND plataforma=?", (time.time(), cid, p))
            else:
                continue
            novas.append(p)
    return novas


def cancelar(cid: int):
    with db.db() as con:
        con.execute("DELETE FROM postagens WHERE candidato=? AND status='fila'", (cid,))


def _ultimo_post(plat: str) -> float:
    with db.db() as con:
        r = con.execute("SELECT max(postado_em) FROM postagens WHERE plataforma=? "
                        "AND status='postado'", (plat,)).fetchone()
    return r[0] or 0.0


def postagens_de(cids: list[int]) -> dict[int, list[dict]]:
    """Para o painel: status por plataforma de cada corte, com a hora prevista de quem está na fila."""
    if not cids:
        return {}
    intervalo = _conf().get("intervalo_min", 90) * 60
    with db.db() as con:
        fila = con.execute("SELECT candidato, plataforma FROM postagens WHERE status='fila' "
                           "ORDER BY criado_em, id").fetchall()
        rows = con.execute(f"SELECT * FROM postagens WHERE candidato IN ({','.join('?' * len(cids))})",
                           cids).fetchall()
    previsto: dict[tuple, float] = {}
    for p in PLATAFORMAS:
        t = max(time.time(), _ultimo_post(p) + intervalo)
        for f in fila:
            if f["plataforma"] == p:
                previsto[(f["candidato"], p)] = t
                t += intervalo
    out: dict[int, list[dict]] = {}
    for r in rows:
        d = dict(r)
        d["nome"] = NOMES.get(d["plataforma"], d["plataforma"])
        if d["status"] == "fila":
            d["previsto"] = time.strftime("%H:%M", time.localtime(previsto.get((d["candidato"], d["plataforma"]), time.time())))
        out.setdefault(d["candidato"], []).append(d)
    return out


def _proximo(plat: str):
    with db.db() as con:
        return con.execute(
            "SELECT p.id pid, c.* FROM postagens p JOIN candidatos c ON c.id = p.candidato "
            "WHERE p.plataforma=? AND p.status='fila' ORDER BY p.criado_em, p.id LIMIT 1",
            (plat,)).fetchone()


def ciclo():
    """Uma passada do carteiro: no máximo um envio por plataforma, se o intervalo deixar."""
    intervalo = _conf().get("intervalo_min", 90) * 60
    for plat in plataformas_ativas():
        if time.time() - _ultimo_post(plat) < intervalo or not _tok(plat):
            continue
        if time.time() < _pausa.get(plat, 0):      # falhou há pouco: não queima a fila inteira
            continue
        c = _proximo(plat)
        if c is None:
            continue
        c = dict(c)
        with db.db() as con:
            con.execute("UPDATE postagens SET status='enviando' WHERE id=?", (c["pid"],))
        log.info("📤 postando corte #%d no %s...", c["id"], NOMES[plat])
        try:
            if c["status"] != "aprovado" or not c["arquivo"] or not Path(c["arquivo"]).exists():
                raise ErroPost("corte não está aprovado ou o arquivo sumiu")
            url = ENVIAR[plat](c)
            with db.db() as con:
                con.execute("UPDATE postagens SET status='postado', url=?, postado_em=? WHERE id=?",
                            (url, time.time(), c["pid"]))
            log.info("✔ corte #%d no %s: %s", c["id"], NOMES[plat], url)
        except Exception as e:
            log.warning("✖ corte #%d no %s falhou: %s", c["id"], NOMES[plat], e)
            _pausa[plat] = time.time() + 600
            with db.db() as con:
                con.execute("UPDATE postagens SET status='erro', erro=? WHERE id=?",
                            (str(e)[:800], c["pid"]))


def carteiro(parar: threading.Event, passo_s: int = 30):
    """Thread do painel: confere a fila a cada 30s. Nunca morre por erro."""
    log.info("carteiro ligado: %s, 1 post a cada %d min por plataforma",
             ", ".join(NOMES[p] for p in plataformas_ativas()), _conf().get("intervalo_min", 90))
    while not parar.is_set():
        try:
            ciclo()
        except Exception:
            log.exception("erro no carteiro")
        parar.wait(passo_s)
