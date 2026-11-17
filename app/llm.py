"""IA que decide início/fim exatos, título e gancho do corte.

LLM_PROVIDER no .env: gemini | anthropic | none
Sem IA, usa a janela do detector e um título padrão.
"""
import json
import logging
import os
import re

import httpx

log = logging.getLogger("llm")

PROMPT = """Você edita cortes virais de lives de GTA para TikTok, Reels e Shorts (público brasileiro).

Streamer: {nome}
Janela analisada: 0s até {dur:.0f}s. O pico de reação (grito, chat explodindo) está em ~{pico:.0f}s.
Força do pico: áudio z={audio}, chat z={chat}, reações no chat z={reacao}.

O que o streamer falou (com tempos):
{transcricao}

O que o chat mandou (tempos já alinhados com o vídeo):
{mensagens_chat}

O que vira corte BOM de GTA:
- Primeiras impressões: o streamer reagindo ao jogo pela primeira vez (gráfico, mapa, detalhe absurdo,
  "olha isso", "não acredito").
- Situação engraçada ou caótica com reação forte: fuga, perseguição, acidente, bug, NPC fazendo algo
  bizarro, plano que deu muito errado, troll, briga no RP.
- Fala marcante / zoeira do streamer, interação engraçada com outro jogador.
- O chat reagindo junto (KKKK, "?????", "MDS") confirma que o momento foi bom.

O que NÃO é corte (nota baixa, "manter": false):
- Só barulho do jogo (tiro, explosão, granada) sem o streamer reagir ou falar algo interessante.
- Gameplay comum, menu, loading, streamer lendo doação/propaganda, conversa sem graça.
- Momento que só faz sentido para quem estava assistindo a live inteira.

Regras de edição:
- O corte começa NO momento forte ou no máximo 2s antes. Nada de contexto lento no início.
- Duração entre {dmin} e {dmax} segundos, dentro da janela.
- Termine logo depois da reação/punchline (ou num cliffhanger).
- "titulo": até 60 caracteres, PT-BR, caixa alta, cara de corte (ex.: "ELE NÃO ESPERAVA ISSO 😱").
- "gancho": até 6 palavras para aparecer na tela nos 3 primeiros segundos, sem emoji.
- "nota": 0 a 10, quão bom é para viralizar. Seja exigente.
- "manter": false se não tiver nada interessante.

Responda SÓ com JSON:
{{"manter": true, "inicio": 0.0, "fim": 0.0, "titulo": "", "gancho": "", "nota": 0}}"""


def _extrair_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError(f"IA não devolveu JSON: {texto[:200]}")
    return json.loads(m.group(0))


MODELOS_GEMINI = "gemini-3.8-flash,gemini-3.1-flash-lite,gemini-3.5-flash"
TIMEOUT_MODELO_S = 30


def _gemini(prompt: str) -> str:
    """Tenta os modelos de GEMINI_MODEL em ordem (separados por vírgula). A camada grátis
    vive lotada (503) ou lenta: se um falhar ou demorar, vai para o próximo."""
    chave = os.environ["GEMINI_API_KEY"]
    modelos = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or MODELOS_GEMINI).split(",")
               if m.strip()]
    ultimo_erro: Exception | None = None
    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        try:
            # Chave no cabeçalho, não na URL: na URL ela vaza em log de erro, e as chaves
            # novas ("AQ.…") não são aceitas como ?key=.
            r = httpx.post(
                url, headers={"x-goog-api-key": chave}, timeout=TIMEOUT_MODELO_S,
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.4,
                                           "responseMimeType": "application/json"}},
            )
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            status = getattr(getattr(e, "response", None), "status_code", type(e).__name__)
            log.info("gemini %s falhou (%s), tentando o próximo", modelo, status)
            ultimo_erro = e
    raise RuntimeError(f"nenhum modelo Gemini respondeu ({ultimo_erro})")


def _anthropic(prompt: str) -> str:
    r = httpx.post(
        "https://api.anthropic.com/v1/messages", timeout=60,
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"},
        json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
              "max_tokens": 400,
              "messages": [{"role": "user", "content": prompt}]},
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def decidir(nome: str, dur: float, pico: float, sinais: dict, transcricao: str,
            dmin: float, dmax: float, chat: str = "") -> dict:
    """Retorna {manter, inicio, fim, titulo, gancho, nota} com tempos relativos à janela."""
    padrao = {
        "manter": True,
        # Sem IA: um pouco antes do pico (o chat atrasa) até o fim da janela.
        "inicio": max(0.0, pico - 8),
        "fim": min(dur, max(0.0, pico - 8) + dmax),
        "titulo": f"MOMENTO INSANO DO {nome.upper()} 😱",
        "gancho": "",
        "nota": None,
    }
    prov = os.environ.get("LLM_PROVIDER", "none").lower()
    if prov == "none" or not transcricao.strip():
        return padrao
    prompt = PROMPT.format(
        nome=nome, dur=dur, pico=pico, transcricao=transcricao, dmin=dmin, dmax=dmax,
        mensagens_chat=chat.strip() or "(sem chat)",
        **{k: sinais.get(k, "?") for k in ("audio", "chat", "reacao")},
    )
    try:
        bruto = _gemini(prompt) if prov == "gemini" else _anthropic(prompt)
        r = {**padrao, **_extrair_json(bruto)}
    except Exception as e:  # IA fora do ar não pode travar o pipeline
        log.warning("IA falhou (%s), usando padrão", e)
        return padrao
    # Garantir limites
    ini = min(max(0.0, float(r["inicio"])), max(0.0, dur - dmin))
    fim = min(dur, max(float(r["fim"]), ini + dmin))
    fim = min(fim, ini + dmax)
    r.update(inicio=ini, fim=fim)
    return r
