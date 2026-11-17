# Cortes GTA 6: guia para o Claude Code

Dono: Bruno. Roda no PC de casa dele (Windows + Docker Desktop/WSL2).
Objetivo: gravar lives de GTA 6 de outros streamers, achar os melhores momentos enquanto a
live rola e gerar **cortes verticais de 15–40s, legendados, com a identidade do canal**, para
o Bruno aprovar no painel e postar (YouTube Shorts, TikTok, Reels).
O que importa: **velocidade** (corte sai com a live rolando) e **dopamina** (começa no pico, sem tempo morto).
O README tem as instruções para o Bruno; este arquivo explica como o código funciona.

Contexto de negócio, regras de conteúdo e histórico das decisões (leia também):
@docs/CONTEXTO.md

**Mantenha os dois arquivos atualizados**: ao terminar uma tarefa que muda status, decisões ou
pendências, atualize a seção "Status" daqui e o docs/CONTEXTO.md no mesmo commit.

## Status (17/11/2026)

Implementado e testado com vídeo sintético + live simulada:
gravador, chat Twitch/YouTube, detector, backtest, IA (opcional), render 9:16 com legenda,
remoção de silêncio, painel, escritório (/escritorio). **Ainda não testado com live/VOD real nem com Whisper real**
(o ambiente onde foi escrito não tinha acesso à Twitch nem ao HuggingFace).

Próximos passos:
1. Backtest com VOD real do Paulinho e calibrar `detector.limiar`/`pesos`.
2. Medir a facecam de cada streamer (`tools/frame.py`) e preencher `layout.facecam`.
3. Ensaio: `docker compose up` durante uma live de GTA 5 RP por 2h.
4. Fase 2: postagem automática (`publisher.py`), depois das auditorias de API.
5. Escritório: puxar TikTok/Instagram automático quando as APIs forem aprovadas; status "postado".

## Arquitetura

```
recorder.py   streamlink → ffmpeg segment (blocos .ts de 120s) → tabela segmentos (horário epoch real)
chat_live.py  IRC anônimo da Twitch / chat-downloader no YouTube → tabela chat (epoch)
worker.py     a cada 15s: detector nos trechos novos → candidatos → pipeline (mais novo primeiro)
detector.py   score por segundo = z(áudio dB) + z(log msgs/s) + z(log reações) → picos
pipeline.py   janela do pico → Whisper → llm.decidir → recorta → tira silêncio → Whisper de novo → legenda → render
render.py     extrair (cruza segmentos com concat), remover_silencio (silencedetect), compor (layout+frame+ASS)
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
