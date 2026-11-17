# Cortes GTA 6: guia para o Claude Code

Dono: Bruno. Roda no PC de casa dele (Windows + Docker Desktop/WSL2).
Objetivo: gravar lives de GTA 6 de outros streamers, achar os melhores momentos enquanto a
live rola e gerar **cortes verticais de 15–40s, legendados, com a identidade do canal**, para
o Bruno aprovar no painel e postar (YouTube Shorts, TikTok, Reels).
O que importa: **velocidade** (corte sai com a live rolando) e **dopamina com contexto** (mostra o que causou a reação, sem tempo morto).
O README tem as instruções para o Bruno; este arquivo explica como o código funciona.

Contexto de negócio, regras de conteúdo e histórico das decisões (leia também):
@docs/CONTEXTO.md

**Mantenha os dois arquivos atualizados**: ao terminar uma tarefa que muda status, decisões ou
pendências, atualize a seção "Status" daqui e o docs/CONTEXTO.md no mesmo commit.

## Status (17/11/2026)

Implementado: gravador, chat Twitch/YouTube, detector, backtest, IA (opcional), render 9:16 com
legenda, facecam automática, remoção de silêncio, painel, escritório (/escritorio).
Testado em 17/11 no Linux do Bruno (venv, sem Docker) com **live real da Twitch**: gravação,
chat IRC, Whisper `small` na CPU (legenda ok, ~1 min por corte), facecam auto em VODs do Davy e do
Paulinho, IA Gemini real (descartou granada e trecho morto; aprovou momento bom com nota 9 e título).
**Ainda não testado**: live de GTA de verdade, Docker no Windows, GPU.

Próximos passos:
1. Backtest com VOD real do Paulinho e calibrar `detector.limiar`/`pesos`.
2. Ensaio: `docker compose up` durante uma live de GTA 5 RP por 2h.
3. Fase 2: postagem automática (`publisher.py`), depois das auditorias de API.
4. Escritório: puxar TikTok/Instagram automático quando as APIs forem aprovadas; status "postado".

## Arquitetura

```
recorder.py   streamlink → ffmpeg segment (blocos .ts de 120s) → tabela segmentos (horário epoch real)
chat_live.py  IRC anônimo da Twitch / chat-downloader no YouTube → tabela chat (epoch)
worker.py     a cada 15s: detector nos trechos novos → candidatos → pipeline (mais novo primeiro)
detector.py   score por segundo = z(áudio dB) + z(log msgs/s) + z(log reações) → picos
pipeline.py   janela do pico → Whisper + quadros da tela → llm.decidir → recorta → tira silêncio → Whisper de novo → legenda → render
render.py     extrair (cruza segmentos com concat), remover_silencio (silencedetect),
              resolver_layout (cam | cheia | gameplay), compor (layout+frame+ASS)
facecam.py    acha a cam em cada corte: rostos (Haar/OpenCV) em 8 quadros, fica com o rosto PARADO
legenda.py    .ass com legenda palavra por palavra, gancho no topo (3s) e crédito
panel/app.py  FastAPI + HTML único: revisar, aprovar, rejeitar, editar título/gancho → re-render
panel/escritorio.html  /escritorio: cena isométrica (SVG gerado em JS) com cada parte do sistema como
              "funcionário" + números por plataforma. Atualiza via /api/escritorio a cada 10s
metricas.py   status dos agentes (lido do SQLite) + tabela metricas (fotos de seguidores/views/receita
              por plataforma; YouTube automático via API key, TikTok/Instagram/receita manuais)
main.py       python -m app.main live | painel | processar <id> [--forcar]
tools/        backtest.py, baixar_vod.py, gerar_teste.py, frame.py
```

Relógio: tudo (segmentos, chat, candidatos) usa o mesmo relógio. Ao vivo = epoch.
No backtest o VOD começa em 0 (`origem='backtest'`), e por isso não colide com o ao vivo.

Status do candidato: `novo → processando → pronto | descartado | erro`, e no painel `aprovado | rejeitado`.

## Decisões já tomadas (não reverter sem motivo)

- **Remoção de silêncio é nossa** (`silencedetect` + `select`), não auto-editor: a CLI do
  auto-editor muda muito entre versões, e assim temos controle do limiar.
- O chat atrasa em relação ao vídeo: `chat_offset_s` (padrão -8) por streamer.
- Sem chat (backtest sem chat), o score usa só o áudio.
- Whisper roda **só nas janelas** dos candidatos, nunca na live inteira. Roda 2x: na janela
  (para a IA decidir) e no trecho final já sem silêncio (para os tempos da legenda baterem).
- IA fora do ar ou sem chave nunca trava: `llm.decidir` cai no padrão.
- Flag `--twitch-disable-ads` do streamlink foi removida (não existe mais na 8.x).
- **Facecam automática** (`facecam: auto`): a cam do Paulinho muda de lugar conforme o jogo, então
  retângulo fixo não serve. Rosto grande (>20% da altura) = câmera cheia → vertical no rosto.
  Com duas cams (convidado) pode pegar a do convidado. OpenCV fixo em <5 (o 5.x tirou o Haar).
- **Só grava GTA** (`gravacao.categorias`) e pula reprise (título com RERUN/REPRISE); re-checa a
  categoria a cada 2 min e para se o streamer trocar de jogo. Motivo: no teste, uma reprise do
  Gaules (chat parado) gerou corte de uma granada.
- **Ao vivo o pico precisa do chat** (`detector.chat_min_z`): só áudio alto (tiro, explosão) não
  vira corte. Sem chat nenhum, não sai corte. Backtest sem chat continua só com áudio.
- **Aquecimento** (`detector.aquecimento_s`): sem picos nos primeiros 5 min de cada gravação.
- A IA recebe o chat da janela junto com a transcrição, e o prompt diz o que é corte bom de GTA.
- **Corte com contexto** (17/11): o Bruno achou o corte "começa no pico" genérico. Prioridade nº 1 do
  prompt é novidade/surpresa do GTA 6, e o corte começa quando a coisa aparece (5–20s antes do pico),
  não no grito. Gancho e título dizem O QUE é. Janela da IA: 35s antes do pico.
- **IA vê a tela** (`ia.quadros`): 5 quadros JPEG 640px da janela (mais densos perto do pico) vão
  junto com a transcrição. Sem isso ela só lia a fala e não sabia se o que aparecia era impressionante.
  Quadro que falha é pulado; `LLM_PROVIDER=none` nem extrai.
- **Gemini**: chave no cabeçalho `x-goog-api-key` (chaves novas "AQ.…" não funcionam em `?key=`, e
  na URL a chave vazava no log). `GEMINI_MODEL` é uma lista em ordem: a camada grátis vive com 503
  ou lenta, então cada modelo tem 30s e cai para o próximo. `gemini-2.5-flash` não aceita conta nova.
- Rodando sem Docker, `app/config.py` lê o `.env` (importar `app` já carrega).
- **Parada limpa**: `main.live` espera os gravadores (join) fecharem streamlink/ffmpeg, e os filhos
  usam PR_SET_PDEATHSIG (morrem se o Python morrer). Antes ficavam órfãos gravando para sempre
  (no teste encheram 6 GB depois de o processo principal ter parado).

## Regras

- Comentários, logs e textos do painel em **português**.
- Config no `config.yaml`, segredos no `.env`. Nada hardcoded.
- Processo ao vivo não pode morrer: toda falha de rede/ffmpeg é logada e o loop continua.
- Antes de usar uma flag de CLI (streamlink, yt-dlp, TwitchDownloaderCLI), conferir o `--help`
  da versão instalada.
- Nunca commitar `data/`, mídia ou `.env`.
- Testar mudanças de render com `python -m tools.gerar_teste` + backtest antes de mexer no ao vivo.

## Checklist do Bruno (fora do código)

- [ ] Criar YouTube, TikTok e Instagram com o mesmo nome/@ (Instagram como conta Empresa ligada a página do Facebook).
- [ ] Identidade em `brand/`: `font.ttf`, `frame.png` (1080x1920, centro transparente) e cores no `config.yaml`.
- [ ] Dizer qual GPU o PC tem.
- [ ] Ver regras de canal de cortes do Davy Jones e do Paulinho; sempre dar crédito.
- [ ] PC sem hibernar durante as lives.
