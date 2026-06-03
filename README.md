# Font Matcher

Identify the closest **open-source** font for any given font file. Upload a TTF/OTF/WOFF/WOFF2 font and get ranked matches from a corpus of thousands of open-source fonts.

## How it works

Each font is fingerprinted using two complementary representations:

- **Metric features** (11 values) — weight, width, italic, cap height, x-height, serif classification, etc., extracted directly from OpenType tables via `fontTools`.
- **Perceptual features** (512-dim CLIP embedding) — 27 diagnostic glyphs are rendered individually, composed into a glyph sheet, and encoded via OpenCLIP ViT-B/32. This captures visual style, stroke contrast, and letterform shapes.

Similarity is computed as a weighted blend of metric Euclidean distance (40%) and perceptual cosine distance (60%), then ranked. Results are filtered to only return fonts with a known open-source license (OFL, Apache 2.0, MIT).

## Quick start (local development)

```bash
# Clone and set up
git clone <repo-url> && cd font_simil
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install open_clip_torch

# Ingest Google Fonts corpus (one-time, ~30 min)
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/google/fonts.git google-fonts-repo
cd google-fonts-repo && git sparse-checkout set ofl apache ufl && cd ..

python -c "
from pathlib import Path
from fontmatch.index.store import FontStore
from fontmatch.index.ingest import ingest_corpus
store = FontStore('fontmatch.db')
ingest_corpus(Path('google-fonts-repo'), store)
store.build_index()
print(f'Indexed {len(store._index)} fonts')
store.close()
"

# Run the dev server
flask --app fontmatch.service.app:get_app run --host 127.0.0.1 --port 8087
```

Open http://127.0.0.1:8087 in your browser.

## Deploy to a VM

```bash
chmod +x deploy.sh
./deploy.sh
```

The script handles everything:
1. Installs system packages (Python, fonts, git)
2. Creates a virtualenv and installs dependencies
3. Clones the Google Fonts repo (sparse checkout, fonts only)
4. Downloads the CLIP model (~350 MB, one-time)
5. Ingests the font corpus into SQLite
6. Sets up a systemd service running gunicorn

After deployment:
```bash
# Check service status
sudo systemctl status fontmatch

# View logs
journalctl -u fontmatch -f

# Restart after code changes
sudo systemctl restart fontmatch
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8087` | HTTP listen port |
| `HOST` | `127.0.0.1` | Bind address |
| `WORKERS` | `2` | Gunicorn worker count |
| `SECRET_KEY` | auto-generated | Flask secret key |
| `DAILY_RATE_LIMIT` | `1000` | Max requests per IP per day |

### Nginx reverse proxy (optional)

The app includes `ProxyFix(x_for=1)` so rate limiting uses the real client IP from `X-Forwarded-For` when running behind a reverse proxy.

```nginx
server {
    listen 80;
    server_name fonts.example.com;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8087;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## API

### `POST /api/identify`

Upload a font file and get the closest open-source matches.

```bash
curl -X POST -F "font=@MyFont.ttf" http://localhost:8087/api/identify
```

Response:
```json
{
  "matches": [
    {
      "family": "Arimo",
      "distance": 0.023,
      "score": 87,
      "license_id": "Apache-2.0",
      "google_fonts_url": "https://fonts.google.com/specimen/Arimo"
    }
  ]
}
```

Query parameters:
- `n` — number of results (default: 1, max: 10)

### `GET /api/health`

Returns `200 OK` with service status.

## Running tests

```bash
source venv/bin/activate

# All tests (requires fontmatch.db with ingested corpus for ground-truth tests)
pytest tests/

# Unit tests only (no corpus needed)
pytest tests/ --ignore=tests/test_ground_truth.py
```

## Architecture

```
fontmatch/
  features/
    metrics.py       — OpenType table feature extraction (11 values)
    perceptual.py    — Multi-glyph rendering + CLIP embedding (512 values)
    fingerprint.py   — Combines metric + perceptual into Fingerprint
  fonts/
    loader.py        — TTF/OTF/WOFF/WOFF2 loading via fontTools
  index/
    store.py         — SQLite storage, in-memory index, identify() logic
    ingest.py        — Corpus ingestion pipeline
  match/
    scorer.py        — Distance computation, ranking, evaluation
  service/
    app.py           — Flask app factory
  web/
    routes.py        — HTML routes
    api.py           — JSON API endpoints
    helpers.py       — Scoring display, URL helpers
  templates/         — Jinja2 templates
  static/            — CSS, JS
```

## Requirements

- Python 3.10+
- ~2 GB disk for CLIP model + Google Fonts corpus
- ~1 GB RAM for the in-memory font index
- CPU only (no GPU required)
