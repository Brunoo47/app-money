FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fonts-dejavu-core curl unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# TwitchDownloaderCLI (baixa o chat de VODs da Twitch para o backtest)
COPY tools/instalar_twitchdownloader.sh /tmp/
RUN sh /tmp/instalar_twitchdownloader.sh || echo "aviso: TwitchDownloaderCLI não instalado"

WORKDIR /app
COPY requirements.txt .
# GPU=1 instala as libs CUDA que o faster-whisper precisa (placa NVIDIA)
ARG GPU=0
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$GPU" = "1" ]; then pip install --no-cache-dir nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"; fi

COPY . .
RUN chmod +x /app/entrypoint.sh

ENV DATA_DIR=/app/data \
    PYTHONUNBUFFERED=1 \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["live"]
