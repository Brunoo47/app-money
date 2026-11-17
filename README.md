# ✂️ Cortes GTA 6

Grava as lives dos streamers, acha os melhores momentos (grito + chat explodindo),
e gera cortes verticais de 15–40s com legenda e a identidade do canal,
prontos para você aprovar e postar.

```
live (Twitch/YouTube) → grava em blocos de 2 min → detector (áudio + chat) → IA escolhe o trecho
→ corte 9:16 com legenda → painel → você aprova → posta
```

## Rodando no PC (Windows)

1. Instale o **Docker Desktop** (com WSL2). Placa NVIDIA? Atualize o driver.
2. Clone e entre na pasta:
   ```
   git clone https://github.com/Brunoo47/app-money
   cd app-money
   copy .env.example .env
   ```
3. Suba tudo:
   ```
   docker compose up -d --build
   ```
   Com placa **NVIDIA** (Whisper bem mais rápido e preciso):
   ```
   docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
   ```
4. Abra o painel: **http://localhost:8080**.
   No celular (mesmo Wi-Fi): `http://IP-DO-PC:8080` (o Windows pode pedir para liberar no firewall).

Ver o que está acontecendo: `docker compose logs -f live`

## Testar antes do GTA 6 (backtest com uma live antiga)

Baixe um trecho de uma live antiga com o chat (aqui, 1h a partir de 30 min):
```
docker compose run --rm live python -m tools.baixar_vod https://www.twitch.tv/videos/ID_DO_VOD --nome teste1 --inicio 00:30:00 --fim 01:30:00
```
Rode o detector e gere os 5 melhores cortes:
```
docker compose run --rm live python -m tools.backtest --streamer paulinholoko --video data/backtest/teste1.mp4 --chat data/backtest/teste1.chat.jsonl --top 15 --processar 5
```
Ele mostra uma lista de tempos (`00:12:34`). Confira no VOD: se 6 de 10 forem momentos bons,
está calibrado. Os cortes aparecem no painel (marcados como "teste").

Sem internet? `python -m tools.gerar_teste` cria um vídeo sintético com 3 "momentos" para testar o pipeline.

## Ajustes (config.yaml)

| O que | Onde |
|---|---|
| Adicionar streamer | copiar um bloco em `streamers` |
| Muitos cortes ruins | aumentar `detector.limiar` (ex.: 2.5) |
| Poucos cortes | diminuir `detector.limiar` (ex.: 1.6) |
| Chat importa mais que grito | mexer em `detector.pesos` |
| Duração | `corte.duracao_min_s` / `duracao_max_s` |
| Facecam do streamer | `python -m tools.frame <video> 00:10:00` → abre `data/frame.png`, mede a cam na grade e preenche `layout.facecam` |

Depois de mudar o config: `docker compose restart`.

## Identidade visual (pasta `brand/`)

- `font.ttf`: fonte da legenda (grossa e legível)
- `frame.png`: moldura 1080x1920 **transparente no meio** (opcional)
- cores em `config.yaml` → `marca` (legenda, palavra em destaque, fundo do gancho)
- `marca.nome_canal` e `marca.hashtags`

## IA (opcional, mas melhora muito)

No `.env`: `LLM_PROVIDER=gemini` + `GEMINI_API_KEY=...` (o Gemini tem camada grátis).
Com IA, ela escolhe o início/fim exatos, escreve título e gancho e dá uma nota.
Cortes com nota baixa vão para "Descartados" (dá para gerar mesmo assim pelo painel).
Sem IA (`none`), usa a janela do detector e um título padrão.
