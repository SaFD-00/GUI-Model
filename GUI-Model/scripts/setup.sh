#!/usr/bin/env bash
set -euo pipefail

# GUI-Model 초기 환경 설정 스크립트
# 사용법: bash scripts/setup.sh   (repo 루트에서 실행)

# conda create -n gui-model python=3.12 -y
conda activate gui-model

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$BASE_DIR"

echo "[setup] (1/2) Installing Python dependencies from requirements.txt"
pip install -r requirements.txt

echo "[setup] (2/2) Installing LlamaFactory + metrics/deepspeed/vllm"
if [ ! -d "LlamaFactory" ]; then
  git clone https://github.com/hiyouga/LlamaFactory.git
fi
cd LlamaFactory
pip install -e ".[torch,metrics]"
pip install -r requirements/metrics.txt -r requirements/deepspeed.txt
pip install vllm
pip install beautifulsoup4 munkres lxml

echo "[setup] Done."
