#!/bin/sh
set -e

# Se as libs CUDA (pip) existirem, aponta o LD_LIBRARY_PATH para elas (GPU NVIDIA).
CUDA_LIBS=$(python -c "import os, nvidia.cublas.lib, nvidia.cudnn.lib; print(os.path.dirname(nvidia.cublas.lib.__file__) + ':' + os.path.dirname(nvidia.cudnn.lib.__file__))" 2>/dev/null || true)
if [ -n "$CUDA_LIBS" ]; then
    export LD_LIBRARY_PATH="$CUDA_LIBS:$LD_LIBRARY_PATH"
fi

case "$1" in
    live|painel|processar) exec python -m app.main "$@" ;;
    *) exec "$@" ;;
esac
