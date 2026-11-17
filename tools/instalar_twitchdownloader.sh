#!/bin/sh
# Baixa a última versão Linux x64 do TwitchDownloaderCLI para /usr/local/bin.
set -e
URL=$(curl -fsSL https://api.github.com/repos/lay295/TwitchDownloader/releases/latest \
  | grep -o '"browser_download_url": *"[^"]*CLI[^"]*Linux-x64\.zip"' \
  | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')
[ -n "$URL" ] || { echo "não achei o release do TwitchDownloaderCLI"; exit 1; }
curl -fsSL "$URL" -o /tmp/tdcli.zip
unzip -o /tmp/tdcli.zip -d /tmp/tdcli
install -m 755 /tmp/tdcli/TwitchDownloaderCLI /usr/local/bin/TwitchDownloaderCLI
rm -rf /tmp/tdcli /tmp/tdcli.zip
echo "TwitchDownloaderCLI instalado"
