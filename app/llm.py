"""IA que decide início/fim exatos, título e gancho do corte.

LLM_PROVIDER no .env: gemini | anthropic | none
Sem IA, usa a janela do detector e um título padrão.
"""
import base64
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
{info_quadros}
O GTA 6 acabou de lançar: ninguém viu o jogo direito ainda. O público quer VER as novidades
pela reação do streamer.

O que vira corte BOM (em ordem de prioridade):
1. Novidade/surpresa do GTA 6: o streamer descobrindo algo novo ou impressionante (gráfico, mapa,
   cidade, física, água, carro, NPC realista, detalhe absurdo, mecânica nova) e reagindo
   ("olha isso", "que isso", "não acredito", "mano do céu"). O corte precisa MOSTRAR a coisa.
2. Situação engraçada ou caótica com reação forte: fuga, perseguição, acidente, bug, NPC fazendo algo
   bizarro, plano que deu muito errado, troll, briga no RP.
3. Fala marcante / zoeira do streamer, interação engraçada com outro jogador.
O chat reagindo junto (KKKK, "?????", "QUE ISSO", "MDS") confirma que o momento foi bom.

O que NÃO é corte (nota baixa, "manter": false):
- Só barulho do jogo (tiro, explosão, granada) sem o streamer reagir ou falar algo interessante.
- Gameplay comum, menu, loading, streamer lendo doação/propaganda, conversa sem graça.
- Reação solta sem dar para entender do que ele está falando (nem pela fala nem pelo que veio antes).
- Momento que só faz sentido para quem estava assistindo a live inteira.

Regras de edição (contexto + ritmo):
- Quem assiste tem que entender O QUE o streamer está vendo. O corte começa quando a coisa
  aparece ou quando ele começa a notar ("ué", "pera", "olha ali"), não só no grito. Use a fala
  e o chat para achar esse começo; normalmente fica 5 a 20s antes do pico.
- Mostre o contexto mas sem enrolação: comece já com algo acontecendo, nunca em silêncio ou papo
  que não tem a ver.
- Duração entre {dmin} e {dmax} segundos, dentro da janela.
- Termine logo depois da reação/punchline (ou num cliffhanger).
- "o_que_acontece": 1 frase com o FATO concreto do corte (o quê, com quem/o quê, onde), tirado
  da fala, do chat e dos quadros. Ex.: "um caça do exército persegue o avião do Lipão sobre o mar".
- "titulo": até 60 caracteres, PT-BR, caixa alta. Escreva a partir do "o_que_acontece":
  NOME DO STREAMER + o fato específico + "NO GTA 6", ou "O/A [COISA] DO GTA 6 É [ADJETIVO]".
  Tem que ter o detalhe que só existe NESTE corte (o bicho, o veículo, o lugar, o NPC, a mecânica).
  Teste: se o título serviria para outro corte qualquer, está genérico, reescreva.
  PROIBIDO: "MOMENTO", "INSANO", "ÉPICO/ÉPICA", "REAGE", "REAÇÃO", "INTERAGINDO", "SOFRENDO",
  "COMEÇA", "JOGANDO", "PELA PRIMEIRA VEZ" (esses deram ~7 mil views; título com o fato deu 200 mil+).
  RUIM → BOM:
    "LIPÃO COMEÇA BRIGA ÉPICA NO GTA 6" → "LIPÃO APANHOU DE UMA VELHINHA NO GTA 6"
    "DAVY JONES REAGE AO GRÁFICO" → "O REFLEXO DA ÁGUA DO GTA 6 ENGANOU O DAVY"
    "MOMENTO INSANO DO LIPÃO 😱" → "LIPÃO SENDO INTERCEPTADO POR CAÇAS NO GTA 6"
    "DAVY JONES INTERAGINDO COM PAPAGAIO" → "O PAPAGAIO DO GTA 6 RESPONDEU O DAVY 💀"
  No máximo 1 emoji, no fim.
- "gancho": até 7 palavras, sem emoji. Fica numa faixa na tela o vídeo INTEIRO: é o que faz a
  pessoa parar de rolar. Mesmo fato do título, dito do jeito mais curto (ex.: "CAÇAS ATRÁS DO LIPÃO").
- Atenção: a transcrição mistura a fala do streamer (português) com diálogo dos NPCs do jogo
  (em inglês, às vezes traduzido errado). Use o diálogo só como contexto; a reação é do streamer.
- "nota": 0 a 10, quão bom é para viralizar. Seja exigente.
- "manter": false se não tiver nada interessante.

Responda SÓ com JSON:
{{"o_que_acontece": "", "manter": true, "inicio": 0.0, "fim": 0.0, "titulo": "", "gancho": "", "nota": 0}}"""


def _extrair_json(texto: str) -> dict:
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError(f"IA não devolveu JSON: {texto[:200]}")
    return json.loads(m.group(0))


MODELOS_GEMINI = "gemini-3.8-flash,gemini-3.1-flash-lite,gemini-3.5-flash"
TIMEOUT_MODELO_S = 30


Quadro = tuple[float, bytes]   # (segundo dentro da janela, JPEG)

INFO_QUADROS = """
Junto vão {n} quadros (imagens) da tela, nos tempos {tempos}. Use-os para ver O QUE está
acontecendo: se é algo novo/impressionante do jogo, em que momento a coisa aparece na tela
(para começar o corte ali) e se dá para entender o momento sem ter visto a live.
"""


def _gemini(prompt: str, quadros: list[Quadro] = ()) -> str:
    """Tenta os modelos de GEMINI_MODEL em ordem (separados por vírgula). A camada grátis
    vive lotada (503) ou lenta: se um falhar ou demorar, vai para o próximo."""
    chave = os.environ["GEMINI_API_KEY"]
    modelos = [m.strip() for m in (os.environ.get("GEMINI_MODEL") or MODELOS_GEMINI).split(",")
               if m.strip()]
    partes: list[dict] = [{"text": prompt}]
    for t, jpg in quadros:
        partes += [{"text": f"Quadro em {t:.0f}s:"},
                   {"inline_data": {"mime_type": "image/jpeg",
                                    "data": base64.b64encode(jpg).decode()}}]
    ultimo_erro: Exception | None = None
    for modelo in modelos:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"
        try:
            # Chave no cabeçalho, não na URL: na URL ela vaza em log de erro, e as chaves
            # novas ("AQ.…") não são aceitas como ?key=.
            r = httpx.post(
                url, headers={"x-goog-api-key": chave}, timeout=TIMEOUT_MODELO_S,
                json={"contents": [{"parts": partes}],
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


def _anthropic(prompt: str, quadros: list[Quadro] = ()) -> str:
    conteudo: list[dict] = [{"type": "text", "text": prompt}]
    for t, jpg in quadros:
        conteudo += [{"type": "text", "text": f"Quadro em {t:.0f}s:"},
                     {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                  "data": base64.b64encode(jpg).decode()}}]
    r = httpx.post(
        "https://api.anthropic.com/v1/messages", timeout=60,
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"},
        json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
              "max_tokens": 400,
              "messages": [{"role": "user", "content": conteudo}]},
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"]


def decidir(nome: str, dur: float, pico: float, sinais: dict, transcricao: str,
            dmin: float, dmax: float, chat: str = "", quadros: list[Quadro] = ()) -> dict:
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
        info_quadros=INFO_QUADROS.format(
            n=len(quadros), tempos=", ".join(f"{t:.0f}s" for t, _ in quadros)) if quadros else "",
        **{k: sinais.get(k, "?") for k in ("audio", "chat", "reacao")},
    )
    try:
        bruto = _gemini(prompt, quadros) if prov == "gemini" else _anthropic(prompt, quadros)
        r = {**padrao, **_extrair_json(bruto)}
    except Exception as e:  # IA fora do ar não pode travar o pipeline
        log.warning("IA falhou (%s), usando padrão", e)
        return {**padrao, "ia_falhou": True}
    # Garantir limites
    ini = min(max(0.0, float(r["inicio"])), max(0.0, dur - dmin))
    fim = min(dur, max(float(r["fim"]), ini + dmin))
    fim = min(fim, ini + dmax)
    r.update(inicio=ini, fim=fim)
    return r
