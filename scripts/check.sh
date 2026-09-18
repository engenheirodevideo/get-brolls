#!/usr/bin/env bash
# Mesma bateria de qualidade que o CI roda no job `quality`, para rodar antes do commit.
# Instale as ferramentas uma vez: python3 -m pip install -r requirements-dev.txt
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check"
ruff check .

echo "==> ruff format --check"
ruff format --check .

echo "==> pyright"
pyright

echo "==> gen_skill_mirror --check"
python3 scripts/gen_skill_mirror.py --check

echo "==> check_anchors"
python3 scripts/check_anchors.py

echo "==> unittest"
python3 -m unittest discover -s tests

echo "OK"
