#!/bin/bash
set -e

echo "🚀 Launching Crypto Arena Telegram Bot…"

# 1) pārej uz projekta mapi
cd ~/Arenapay

# 2) ielādē .env vides mainīgos
set -a
source .env
set +a

# 3) aktivē virtuālo vidi
source venv/bin/activate

# 4) palaiž botu
exec python bot.py
