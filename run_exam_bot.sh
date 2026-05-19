#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv-bot" ]; then
  python3 -m venv .venv-bot
fi

. ".venv-bot/bin/activate"
PIP_DISABLE_PIP_VERSION_CHECK=1 python -m pip install -q -r requirements.txt

if [ ! -f ".env" ]; then
  cp telegram_exam_bot/.env.example .env
  echo "Created .env. Configure BOT_TOKEN and run again."
  exit 1
fi

PYTHONDONTWRITEBYTECODE=1 python -m telegram_exam_bot
