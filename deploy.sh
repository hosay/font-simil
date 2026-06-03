#!/usr/bin/env bash
#
# deploy.sh — Deploy the font matching service on a fresh VM.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
#
# Prerequisites: Ubuntu 22.04+ with git, python3.10+, and pip installed.
# The script is idempotent — safe to re-run after updates.

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$APP_DIR/venv"
DB_PATH="$APP_DIR/fontmatch.db"
GF_REPO="$APP_DIR/google-fonts-repo"
SERVICE_NAME="fontmatch"
PORT="${PORT:-8087}"
HOST="${HOST:-127.0.0.1}"
WORKERS="${WORKERS:-2}"

echo "==> Font Matcher deployment starting"
echo "    App directory: $APP_DIR"
echo "    Port: $PORT"

# --- System dependencies ---
echo "==> Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git wget \
    fonts-liberation fonts-dejavu-core fonts-ubuntu 2>/dev/null

# --- Python virtual environment ---
echo "==> Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"

echo "==> Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r "$APP_DIR/requirements.txt"
pip install --quiet gunicorn open_clip_torch

# --- Google Fonts corpus ---
if [ ! -d "$GF_REPO" ]; then
    echo "==> Cloning Google Fonts repository (sparse checkout, fonts only)..."
    git clone --depth 1 --filter=blob:none --sparse \
        https://github.com/google/fonts.git "$GF_REPO"
    cd "$GF_REPO"
    git sparse-checkout set ofl apache ufl
    cd "$APP_DIR"
else
    echo "==> Google Fonts repo already present, pulling updates..."
    cd "$GF_REPO"
    git pull --quiet || true
    cd "$APP_DIR"
fi

# --- CLIP model warm-up ---
echo "==> Downloading CLIP model (ViT-B/32, ~350MB, one-time)..."
python -c "
from fontmatch.features.perceptual import _get_clip
_get_clip()
print('CLIP model ready.')
"

# --- Corpus ingestion ---
echo "==> Ingesting font corpus (this may take a while on first run)..."
python -c "
from pathlib import Path
from fontmatch.index.store import FontStore
from fontmatch.index.ingest import ingest_corpus
from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION

store = FontStore('$DB_PATH')

# Check if we already have v4 fingerprints
with store._lock:
    v4_count = store.conn.execute(
        'SELECT COUNT(*) FROM fingerprints WHERE schema_version = ?',
        (FINGERPRINT_SCHEMA_VERSION,)
    ).fetchone()[0]

if v4_count > 100:
    print(f'Corpus already ingested ({v4_count} fingerprints). Skipping.')
else:
    for corpus in ['tests/fixtures', 'google-fonts-repo', 'corpus_fonts']:
        p = Path(corpus)
        if p.is_dir():
            n = ingest_corpus(p, store)
            print(f'  Ingested {n} fonts from {corpus}')

store.build_index()
print(f'Index ready: {len(store._index)} fonts')
store.close()
"

# --- Systemd service ---
echo "==> Setting up systemd service..."
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
sudo tee "$SYSTEMD_UNIT" > /dev/null <<UNIT
[Unit]
Description=Font Matcher Service
After=network.target

[Service]
Type=notify
User=$(whoami)
WorkingDirectory=$APP_DIR
Environment="PATH=$VENV_DIR/bin:/usr/bin:/bin"
Environment="SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')"
Environment="DAILY_RATE_LIMIT=1000"
ExecStart=$VENV_DIR/bin/gunicorn \
    --bind ${HOST}:${PORT} \
    --workers ${WORKERS} \
    --timeout 120 \
    --preload \
    "fontmatch.service.app:get_app()"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "==> Deployment complete!"
echo "    Service: systemctl status $SERVICE_NAME"
echo "    URL:     http://${HOST}:${PORT}"
echo "    Logs:    journalctl -u $SERVICE_NAME -f"
echo ""
echo "    To expose publicly, set up nginx as a reverse proxy:"
echo "      sudo apt install nginx"
echo "      # Then proxy_pass to http://127.0.0.1:${PORT}"
