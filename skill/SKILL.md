# Font Matcher Skill

**When to use:** Identify the closest open-source font for a given font file
(TTF, OTF, WOFF, WOFF2).

## Quick start

```bash
# Identify a font (requires a pre-built database)
python skill/identify_font.py path/to/font.ttf

# Get top 10 matches as JSON
python skill/identify_font.py path/to/font.woff2 --top 10 --json

# Use a custom database
python skill/identify_font.py font.otf --db /path/to/fontmatch.db
```

## API usage

The font matcher also runs as a web service:

```bash
# Start the server
make serve

# Identify via API
curl -X POST http://127.0.0.1:8087/identify \
  -F "file=@path/to/font.ttf"

# Health check
curl http://127.0.0.1:8087/health
```

## Building the database

Before using the CLI, you need a corpus database:

```bash
# Ingest Google Fonts (clone the repo first)
python -c "
from pathlib import Path
from fontmatch.index.store import FontStore
from fontmatch.index.ingest import ingest_corpus

store = FontStore('fontmatch.db')
ingest_corpus(Path('path/to/google-fonts'), store)
"
```
