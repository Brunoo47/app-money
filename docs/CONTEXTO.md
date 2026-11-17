# Contexto do projeto (o "porquê" por trás do código)

Resumo das conversas de planejamento entre o Bruno e o Claude (16–17/11/2026).
O CLAUDE.md diz **como** o código funciona; este arquivo diz **por que** ele é assim e
o que o Bruno quer. Leia antes de propor mudanças grandes.

## Quem é o Bruno

- Dev (PHP, SQL, Docker, Next.js) na ULTRA LIMS. Sabe programação e arquitetura, mas
  **entende pouco de IA**: explique termos de IA/vídeo em português simples.
- Conversa em português informal. Prefere respostas diretas e práticas.
- Tem Claude Code Pro. Roda tudo no **PC de casa (Windows)**, não em servidor. Também roda
  direto no Linux (venv + ffmpeg do apt, sem Docker Desktop).
- Preocupado com espaço em disco: gravação 1080p60 dá ~2,8 GB/h por streamer.

## O negócio

- **Canal de cortes de GTA 6**: pegar as lives de streamers grandes jogando GTA 6 e postar
  os melhores momentos no YouTube Shorts, TikTok e Instagram Reels.
- O GTA 6 lança em **19/11/2026** (PS5/Xbox; sem PC por enquanto). A meta é ser **um dos
  primeiros** canais postando cortes. Velocidade é a vantagem competitiva.
- Streamers do MVP: **Davy Jones** (twitch.tv/davyjones) e **Paulinho o LOKO**
  (twitch.tv/paulinholokobr, às vezes YouTube). **Lipão** entra depois (falta o @ dele).
- Monetização: crescer seguidores/views e monetizar nas plataformas.

## Regras de conteúdo (decididas com o Bruno)

- **Contexto** (mudou em 17/11): GTA 6 é jogo novo, o público quer VER a novidade. O corte começa quando
  a coisa aparece ou o streamer começa a notar, não só no grito. O gancho diz o que é ("OLHA A ÁGUA DO GTA 6").
- **Dopamina**: corte de 15–40s, sem enrolação nem tempo morto, legenda grande palavra por palavra, gancho na tela nos 3 primeiros segundos,
  termina logo depois da reação (ou num cliffhanger, para virar "parte 2").
- O "momento bom" em GTA quase sempre é **grito do streamer + chat explodindo** (KKKK, KEKW...),
  por isso o detector prioriza áudio + chat, não só a fala.
- **Qualidade > quantidade**: o sistema gera candidatos, o Bruno posta só os 2–3 melhores de
  cada bloco. Nunca automatizar postagem sem a aprovação dele.
- **Sempre dar crédito** ao streamer (selo no vídeo + link na descrição). O Bruno ficou de
  conferir se os streamers liberam canal de cortes. Repostar sem permissão gera strike.
- Espaçar postagens: no máximo 1 corte por plataforma a cada 1–2h.

## Postagem (por que ainda é manual)

- YouTube Data API e TikTok Content Posting API: app **sem auditoria só posta em privado**.
- Instagram Graph API exige conta **profissional (Empresa) ligada a uma página do Facebook**.
- Plano: no lançamento o Bruno posta na mão (com título/descrição prontos do painel);
  pedir as auditorias na semana 1; depois criar `publisher.py`.

## Ferramentas avaliadas (e por que não usamos sozinhas)

- **MoneyPrinterTurbo**: gera vídeo novo a partir de um tema (roteiro + banco de imagens). Não
  corta lives. Poderia servir no futuro para vídeos de "novidades do GTA 6".
- **OpenShorts, ViralCutter, Clips Kitty, OpusClip, Eklipse**: feitos para podcast (escolhem
  pela fala, enquadram rosto, só depois do VOD pronto). Não leem chat nem acompanham a live.
  Ficam como **plano B** para o dia 19 se o nosso sistema não estiver calibrado.

## Escritório (`/escritorio`)

- Inspirado num painel isométrico de "escritório de robôs de trading" que o Bruno mostrou:
  sala escura, neon amarelo, mesas com monitores, crachás compactos, lounge "em descanso".
- Cada parte do sistema é um "funcionário" com status real (vem do SQLite).
- Bonecos devem ser **redondinhos/fofos** (o Bruno achou os quadrados feios).
- Mascote: **Tesourinha** (robô redondo neon, original do canal). O Bruno pediu o caranguejo do
  Claude, mas não usamos mascote/logo de marca de terceiros: manter o Tesourinha.
- Os números de seguidores/views/receita são o que o Bruno mais quer acompanhar.

## O que foi testado e o que não foi

Testado (ambiente sem acesso à Twitch/HuggingFace): detector com VOD sintético (achou os 3
momentos plantados), render vertical + legenda ASS + layouts com/sem facecam, remoção de silêncio,
gravador com live simulada (HTTP local), worker ao vivo cruzando segmentos, painel (aprovar,
editar, re-render), escritório no Chrome (desktop e celular, clique e zoom).

Testado em 17/11 no PC Linux do Bruno (venv, sem Docker): live real da Twitch (Gaules, canal
japonês), chat IRC real, Whisper real (legenda palavra por palavra ok), facecam automática em
VODs do Davy e do Paulinho. O Bruno achou o 1º corte real fraco (granada numa reprise, sem
reação): daí vieram o filtro de categoria GTA, o pulo de reprise e a exigência de chat no pico.
Ele quer cortes de **reação do streamer + chat**, principalmente **primeiras impressões do GTA 6**.

IA Gemini testada em 17/11 (chave grátis do Bruno, no `.env`). Desde 17/11 ela recebe também 5
quadros da tela (~170 KB, ~1.300 tokens a mais por corte) para ver a novidade, não só ler a fala.

**Não testado ainda**: live de GTA de verdade, Docker build no
Windows, GPU, TwitchDownloaderCLI.

## Pendências do Bruno

- Identidade visual (nome do canal, fonte, moldura, cores) → `brand/` e `config.yaml`.
- Criar os canais (mesmo @ nas 3 plataformas).
- Dizer qual GPU o PC tem.
- @ do Lipão.
