#!/bin/bash
set -e

TOKEN_FILE=~/.automc_jupyter_token
if [ ! -f "$TOKEN_FILE" ]; then
    python3 -c 'import secrets; print(secrets.token_hex(32))' > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
fi
TOKEN=$(cat "$TOKEN_FILE")

if pgrep -f 'jupyter-notebook.*--port=8891' > /dev/null; then
    echo "Already running. URL: http://localhost:8891/?token=$TOKEN"
    exit 0
fi

source ~/miniforge3/etc/profile.d/conda.sh
conda activate automc
export OPENMC_CROSS_SECTIONS=~/endfb-viii.0-hdf5/cross_sections.xml
cd /mnt/c/UTA-Research/Projects/ZavierNdum/Code/AutoMC/Learning
nohup jupyter notebook --no-browser --ip=127.0.0.1 --port=8891 \
    --NotebookApp.token="$TOKEN" \
    > /tmp/jupyter_automc.log 2>&1 &
disown
sleep 2
echo "Started. URL: http://localhost:8891/?token=$TOKEN"
