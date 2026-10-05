# Dupefont

Public name **Dupefont** (https://dupefont.com). Internally the Python package, systemd units, service user and database are still named `fontmatch`.

Find the closest **open-source** font for any given font file. Upload a TTF, OTF, WOFF, or WOFF2 font and get ranked matches from a corpus of thousands of open-source fonts, complete with license info and Google Fonts links.

## How it works

Each font is fingerprinted using two complementary representations:

- **Metric features** (11 values) — weight, width, italic angle, cap height, x-height, serif classification, and more, extracted from OpenType tables via `fontTools`.
- **Perceptual features** (512-dim CLIP embedding) — 27 diagnostic glyphs are rendered at 128×128 px, composed into a grid sheet, and encoded via OpenCLIP ViT-B/32. This captures visual style, stroke contrast, and letterform shapes that metrics alone miss.

Similarity is a weighted blend of metric Euclidean distance (40%) and perceptual cosine distance (60%). Results are filtered to fonts with a known open-source license (OFL, Apache 2.0, MIT, Ubuntu Font License).

**Image matching** (new): upload a screenshot or photo of text and get the closest free fonts. The text is located with Tesseract, typeset in every candidate font from a pre-rendered glyph atlas at the letter-spacing that fits the image, and compared shape-to-shape. It is exposed on the website API and to ChatGPT as an MCP app. Design, evaluation results and deploy runbook: [`docs/image-matching.md`](docs/image-matching.md).

## Production setup (start here)

> State as of 2026-10-05 (growth batch `e70c269` deployed). **Update this section whenever the setup changes.**

The public site is **https://dupefont.com**. This repo checkout **is production**: `/opt/projects/font_simil` on host `a01` is the code the live service runs.

```
Internet ──HTTPS──> mr02 (nginx + Let's Encrypt, dupefont.com)
                      ├── location /      ──Tailscale──> a01:8087  fontmatch     (gunicorn/Flask)
                      └── location = /mcp ──Tailscale──> a01:8088  dupefont-mcp  (uvicorn, ChatGPT app)
```

| Host | Role | Access |
|---|---|---|
| `a01` (this server, Tailscale 100.67.193.2) | Runs both services, DB, fonts, models | local |
| `mr02` (Tailscale 100.65.64.93) | nginx reverse proxy + TLS only; 2 GB RAM, already swapping, so don't run apps there | `ssh prod` from a01 |

nginx config on mr02: `/etc/nginx/sites-available/dupefont.com`. It has `location = /mcp` → `100.67.193.2:8088` (130 s read timeout) and `location /` → `100.67.193.2:8087`. A pre-`/mcp` backup is in mr02 `/root/dupefont.com.nginx.bak-*`.

### Services on a01

| Unit | What | Runs as | Notes |
|---|---|---|---|
| `fontmatch.service` | gunicorn, 2 workers, `--preload`, `0.0.0.0:8087`, `WorkingDirectory=/opt/projects/font_simil` | `fontmatch` | `HF_HOME=/var/lib/fontmatch/.cache/huggingface`, `HF_HUB_OFFLINE=1` (CLIP weights are cached there). Hardened: `ProtectSystem=strict`, writable paths are the repo dir + `/var/lib/fontmatch` only |
| `dupefont-mcp.service` | MCP server for ChatGPT, `uvicorn --factory fontmatch.mcp_server.server:create_app` on `100.67.193.2:8088`, `WorkingDirectory=/opt/projects/font_simil` | `fontmatch` | Calls the Flask API on 127.0.0.1:8087 with `X-Internal-Token`; refuses to start without `FONTMATCH_INTERNAL_TOKEN`. `ReadWritePaths=/var/lib/fontmatch` so it can write its usage log |

Both units load settings from **`/etc/fontmatch/env`** (root:fontmatch, 0640) via `EnvironmentFile=`: `SECRET_KEY`, `FONTMATCH_INTERNAL_TOKEN` (shared by both services), `DUPEFONT_ADMIN_PASSWORD_HASH` (werkzeug hash of the `/admin/stats` password; the plain password is not stored on the server) and `FONTMATCH_SAMPLES_DIR`. Unit backups from before each change are in `/root/*.service.bak-*`.

**Usage dashboard:** `https://dupefont.com/admin/stats` (HTTP Basic, user `dfadmin`). ChatGPT calls come from the MCP service's own SQLite log, `/var/lib/fontmatch/mcp_usage.db` (one row per tool call: hashed ChatGPT user id, tool, status, latency, top font, font-name query, locale/country; never the image or its text hint; pruned after 180 days). Website counts come from `request_log` in `fontmatch.db`; rows labelled `(chatgpt)` are the MCP service's backend calls. To change the password: `venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g('NEW'))"`, put it in the env file, restart `fontmatch`.

Code changes take effect only on restart: gunicorn `--preload` holds the code in memory. `systemctl restart fontmatch` and `journalctl -u fontmatch -f` / `journalctl -u dupefont-mcp -f` are the usual commands.

### Data on a01

| Path | What |
|---|---|
| `fontmatch.db` | SQLite (WAL). Fonts, fingerprints (one row per `schema_version`), caches, ratings, rate-limit counters. **Owned by `fontmatch`**: write to it only as that user (see gotchas) |
| `fontmatch-pre-v6.db` | Backup taken before the v6 re-embed (2026-10-03) |
| `google-fonts-repo/` | Sparse clone of google/fonts (`ofl`, `apache`, `ufl`); source of all candidate fonts |
| `glyph_atlas/` | Image-matcher atlas (~700 MB, memory-mapped, with `catalog.json`, plus `rows.npy`, an 81 MB row-profile sidecar used by the prefilter), built 2026-10-03. Rebuild as the `fontmatch` user with `scripts/build_glyph_index.py` whenever the corpus changes (atomic swap; restart afterwards so workers map the new files). `--rows-only` adds/refreshes just `rows.npy` (~20 s). Without it the service still works but computes profiles per worker on demand (~0.1 s per new character) |
| `/var/lib/fontmatch/.cache/huggingface` | OpenCLIP ViT-B/32 weights used by the service |
| `/var/lib/fontmatch/font_samples/` | Pregenerated "The quick brown fox..." PNGs (served at `/font-sample/<font name>.png?style=`, shown on the image results page and in the ChatGPT widget). Build/refresh as `fontmatch`: `runuser -u fontmatch -- venv/bin/python scripts/build_font_samples.py --out /var/lib/fontmatch/font_samples`. Missing samples render on first request; `.none` files mark fonts that can't render the sample (non-Latin) |
| `/var/lib/fontmatch/mcp_usage.db` | ChatGPT usage log (see above) |
| `/var/lib/fontmatch/user_content/` | Images visitors chose to keep: `shares/<id>.jpg` (public share links, kept until deleted) and `feedback/*.jpg` (sent with "Was this right?" + consent, deleted after 2 years at startup). Rows are in `fontmatch.db` (`shares`, `image_feedback`). **Back this up**: it's user data and can't be regenerated |
| `/var/lib/fontmatch/og_images/` | Cached social preview cards for `/similar-to` pages (regenerated on demand; safe to delete) |

Fingerprint schema versions: the code only uses rows matching `FINGERPRINT_SCHEMA_VERSION` in `fontmatch/features/perceptual.py`. Code in `master` is **v6** (CLIP on square tiles, variable fonts rendered at their Regular instance); older v2–v4 rows stay in the DB, unused. After any schema bump the corpus must be re-embedded *before* restarting the service, or every lookup returns nothing.

### Current deploy state

- **Deployed 2026-10-03** (commit `d918afc`): image matching (`/api/identify-image`), `/api/similar-to`, the ChatGPT MCP app, fingerprint schema v6, and the Dupefont rebrand. The live DB has v6 fingerprints (3,935 rows; the index loads 3,899).
- Smoke-tested after the restart: health, rebranded pages, `/similar-to/*`, image API through `https://dupefont.com` (Playfair Display screenshot → Playfair Display, 95%), MCP `tools/list` and both tools through `https://dupefont.com/mcp`.
- **ChatGPT**: the app is added in Developer mode with MCP URL `https://dupefont.com/mcp`, no auth. Next: run the golden prompts in `docs/chatgpt-golden-prompts.md` and record the results there. Directory submission needs a privacy policy page.
- **Batch 2 (2026-10-04):** image upload on `/identify` (default tab; paste/drag-drop), ChatGPT results widget with font samples (`ui://widget/dupefont-results-v1.html`; users must refresh the connector in ChatGPT to see it), MCP usage log + `/admin/stats`, `/privacy` page, Microsoft Clarity (`DUPEFONT_CLARITY_ID`, default `ys6l88q2n9`; consent banner for European time zones, honours GPC, never on upload/result/admin pages), Inter self-hosted (no Google Fonts requests).
- **Google logo fix (deployed 2026-10-03, commit `72bc19f`, merged from `fix/multicolor-image`):** letter-spacing fit, colour ink map, row-profile prefilter (`glyph_atlas/rows.npy`, built as `fontmatch` with `build_glyph_index.py --rows-only`), category vote, margin-based "likely the same font" label; `IMAGE_SCHEMA_VERSION` 7 (old cached image results unused). Only `fontmatch` restarted. Smoke-tested via `https://dupefont.com`: Google logo → Outfit 87 / League Spartan / Teachers / Red Hat Text / Padauk on `/api/identify-image` (hint and OCR-only) and the MCP image tool. Rollback: `git checkout ac23e7e && systemctl restart fontmatch` (old code ignores `rows.npy`). Pushed to `origin/master`.
- **Google Fonts link fix (commit `373c7f4`, deployed 2026-10-05, `fontmatch` restarted):** specimen links now use the family's name from `google-fonts-repo/*/<family>/METADATA.pb` instead of stripping "weight-like" words (which broke 160 of 2,687 links, e.g. Red Hat Text → `/specimen/Red+Hat`, Playfair Display → `/specimen/Playfair`). Verified after the restart: `/api/similar-to?font=Red Hat Display` links Red Hat Text to `specimen/Red+Hat+Text`. Engineering notes for both fixes: [`dev/IMPLEMENTATION_NOTES.md`](dev/IMPLEMENTATION_NOTES.md).
- **Growth batch (deployed 2026-10-05, master `e70c269`, merged from `feature/growth-batch`; only `fontmatch` restarted):** white text in transparent PNGs; sitemap cut from ~2,880 to ~90 URLs (proprietary + `/popular` pages, the rest `noindex`), fixed canonicals; proprietary pages get a Google Fonts button, CSS snippet, per-font notes, true license/metric claims, related links, 73 fonts (was 50) and corrected mappings; og:image preview cards; "Was this right?" on image results; opt-in public share links (`/r/<id>`) with delete links; privacy policy updated (images stored only on share or consented feedback). Deploy was: merge, `systemctl restart fontmatch` (the new tables are created at startup, `/var/lib/fontmatch/{user_content,og_images}` on first use). Smoke-tested via https://dupefont.com: canonicals, noindex, 88-line sitemap, og cards, a white-on-transparent logo (→ Montserrat Alternates 93%), share → view → delete (test share removed). Rollback: `git checkout 3962005 && systemctl restart fontmatch` (the old code ignores the new tables). Notes: section 6 of [`dev/IMPLEMENTATION_NOTES.md`](dev/IMPLEMENTATION_NOTES.md).
- Known limitations: photos are the weakest tier (see the eval log in `docs/image-matching.md`). The Google-logo failure (Lusitana 82%) was **not** the colours: the matcher compared lines at the font's natural letter-spacing, so tightly or loosely spaced text (logos, tracked caps) matched the wrong fonts. Fixed and deployed 2026-10-03 (see the deploy bullet above and "Google logo fix" in `docs/image-matching.md`). Remaining known weak spots: white text in a transparent PNG (flattened onto white, so no text is found), justified text (one letter-spacing per line), and perfectly clean synthetic renders lost 4 points of family@1 and 7 of family@5 (test split) in exchange for the logo/spacing gains.
- Privacy policy open items (owner decisions): name the operator (`DUPEFONT_OPERATOR` env var adds "It is operated by ..."), make sure `privacy@dupefont.com` receives mail, accept the DigitalOcean/Microsoft DPAs, and write Terms of Service.
- Rollback: `git checkout ea5b334 && systemctl restart fontmatch` (v4 rows remain in the DB; stop `dupefont-mcp` too, since the old code has no `/api/identify-image`). The DB backup from before the re-embed is `fontmatch-pre-v6.db`.

### Gotchas for operators and new sessions

- **`SECRET_KEY` must stay the same across restarts and both workers**: it signs the share/feedback tokens on results pages (valid 24 h). With the default `dev-key-change-me` the share and feedback buttons are not shown.

- **Run DB-writing scripts as the service user**, or root-owned `-wal`/`-shm` files will break the service:
  `runuser -u fontmatch -- env HOME=/var/lib/fontmatch HF_HOME=/var/lib/fontmatch/.cache/huggingface HF_HUB_OFFLINE=1 OMP_NUM_THREADS=3 nice -n 10 venv/bin/python scripts/build_corpus.py`
- **Cap torch threads** (`OMP_NUM_THREADS=1–3`) for scripts and tests. Without it, parallel pytest pushed the load average to ~37 on 6 cores and slowed the live site.
- **Do feature work in a git worktree** (e.g. `/opt/projects/font_simil-fix`, branch `fix/multicolor-image`), never in this checkout: it is production, and a restart picks up whatever is on disk. Symlink `venv` and `google-fonts-repo`; for the atlas make `glyph_atlas/` a real directory of per-file symlinks to production's files, so scripts that write into it (`rows.npy`) never touch production (details in `dev/IMPLEMENTATION_NOTES.md`).
- **Never evaluate a change on only the synthetic set.** It renders queries the same way the atlas does (natural spacing), which is why the letter-spacing bug went unnoticed. Also run `scripts/eval_browser_screenshots.py` (headless Chrome renders).
- **Pillow's `ImageFont.get_variation_names()` segfaults** on some fonts (e.g. `Jaro[opsz].ttf`). Use `fontmatch/fonts/variable.py` (fontTools `fvar`) instead. A segfault kills the process silently, which is how the first re-embed died.
- Don't `pkill -f <pattern>` / `pgrep -f` from a shell whose own command line contains the pattern: it matches itself. Wait on PIDs instead.
- The server is shared with other projects (b5m, xflippa, wp_links). Two long-running `camoufox` processes were observed at 100% CPU, which makes timing measurements noisy.
- Per-IP limits: `DAILY_RATE_LIMIT` per IP per day, 60/min default, 10/min on upload endpoints. The 10/min limits only started applying with commit `3f954c1`; before that they were silently ignored. The MCP service bypasses per-IP limits with `X-Internal-Token` (all ChatGPT traffic arrives from 127.0.0.1) and limits per ChatGPT user instead.

## Quick start

```bash
git clone <repo-url> && cd font_simil
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Ingest the Google Fonts corpus (one-time, ~30 min)
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/google/fonts.git google-fonts-repo
cd google-fonts-repo && git sparse-checkout set ofl apache ufl && cd ..
python scripts/build_corpus.py

# Start the dev server
make serve
```

Open http://localhost:8087 in your browser.

## Deploy to a VM

```bash
chmod +x deploy.sh
./deploy.sh
```

The script handles everything: system packages, virtualenv, Google Fonts clone, CLIP model download (~350 MB), corpus ingestion, and a systemd service running gunicorn.

After deployment:

```bash
systemctl status fontmatch       # check service status
journalctl -u fontmatch -f       # tail logs
systemctl restart fontmatch      # restart after code changes
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8087` | HTTP listen port |
| `HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for LAN access) |
| `WORKERS` | `2` | Gunicorn worker count |
| `SECRET_KEY` | auto-generated | Flask session secret |
| `DAILY_RATE_LIMIT` | `1000` | Max requests per IP per day |
| `FONTMATCH_INTERNAL_TOKEN` | unset | Shared secret between `fontmatch` and `dupefont-mcp`; local calls carrying it skip per-IP limits. Required by the MCP service |
| `FONTMATCH_GLYPH_ATLAS` | `./glyph_atlas` | Glyph atlas directory for image matching |
| `FONTMATCH_API_URL` | `http://127.0.0.1:8087` | (MCP service) where the Flask API lives |
| `DUPEFONT_SITE_URL` | `https://dupefont.com` | (MCP service) base for links returned to ChatGPT |
| `DUPEFONT_MCP_USAGE_DB` | `/var/lib/fontmatch/mcp_usage.db` | ChatGPT usage log (written by the MCP service, read by `/admin/stats`) |
| `DUPEFONT_ADMIN_USER` / `DUPEFONT_ADMIN_PASSWORD_HASH` | `dfadmin` / unset | `/admin/stats` login; no hash = dashboard returns 404 |
| `FONTMATCH_SAMPLES_DIR` | `./font_samples` | Font sample PNGs |
| `DUPEFONT_CLARITY_ID` | `ys6l88q2n9` | Microsoft Clarity project; empty disables it |
| `DUPEFONT_OPERATOR` | unset | Operator name/address shown in the privacy policy |
| `DUPEFONT_USER_CONTENT_DIR` | `<samples dir>/../user_content` | Share-link and feedback images |
| `DUPEFONT_OG_DIR` | `<samples dir>/../og_images` | Preview card cache |

## Web pages

| Route | Description |
|---|---|
| `/` | Homepage with search and upload |
| `/identify` | Upload an image of text (default) or a font file and see matches |
| `/privacy` | Privacy policy |
| `/admin/stats` | Usage dashboard (HTTP Basic auth) |
| `/font-sample/<name>.png` | "Quick brown fox" sample image of a corpus font |
| `/similar-to/<slug>` | Side-by-side comparison for a specific font. Only proprietary fonts and the corpus fonts on `/popular` are indexable (and in the sitemap); other corpus pages are `noindex, follow` |
| `/og/similar-to/<slug>.png` | 1200x630 social preview card for a `/similar-to` page |
| `/r/<id>` | Public share page for an image result (`noindex`); `/r/<id>.jpg` is its image; `/r/<id>/delete?token=` deletes it |
| `POST /share`, `POST /image-feedback` | Share links and "Was this right?" from the image results page (signed token; outside `/api` so no CORS) |
| `/popular` | Most-searched fonts |

## API

All API endpoints are under `/api/`. CORS is enabled for all API routes.

### `POST /api/identify`

Upload a font file and get the closest open-source matches.

```bash
curl -X POST -F "file=@MyFont.ttf" http://localhost:8087/api/identify
```

```json
{
  "matches": [
    {
      "name": "Arimo",
      "family": "Arimo",
      "distance": 0.023,
      "score": 87,
      "license_id": "Apache-2.0",
      "google_fonts_url": "https://fonts.google.com/specimen/Arimo",
      "download_url": "/api/font-file/Arimo"
    }
  ],
  "cached": false
}
```

Results are cached by file hash — repeated uploads return instantly.

### `POST /api/identify-image`

Upload an image (PNG/JPEG/WebP, ≤ 10 MB) of text; optional `text_hint` (the text in the image) greatly improves accuracy. 10/min per IP.

```bash
curl -X POST -F "image=@logo.png" -F "text_hint=Sunrise Bakery" http://localhost:8087/api/identify-image
```

Returns `{transcript, transcript_source, text_box, matches: [{name, family, style, license_id, category, score, match_label, similar_url, google_fonts_url, download_url}], cached}`. 400 = bad image, 422 = no readable text, 503 = engine unavailable (e.g. atlas not built).

### `GET /api/similar-to?font=<name>`

Free alternatives to a font by name (proprietary names such as Helvetica resolve via the alias table). Same data as the `/similar-to/<slug>` page.

### `GET /api/browse`

Search and browse the font corpus.

| Parameter | Default | Description |
|---|---|---|
| `q` | | Search query (matches family name) |
| `category` | | Filter by category |
| `page` | `1` | Page number |
| `per_page` | `40` | Results per page (max 100) |

Proprietary font names (e.g. "Helvetica") are recognized and mapped to their open-source equivalents.

### `POST /api/scores`

Submit a user rating for a match (1–5 scale).

```json
{ "query_font": "Arial", "match_font": "Arimo", "score": 4 }
```

### `POST /api/report`

Report a bad match.

```json
{ "query_font": "Arial", "match_font": "SomeFont" }
```

### `GET /api/fonts/<id>`

Get metadata for a specific font by database ID.

### `GET /api/font-file/<name>`

Serve a font file from the corpus for `@font-face` rendering in the browser.

### `GET /api/health`

Returns `200 OK` with `{"status": "ok", "font_count": N}`.

### `GET /api/docs`

Interactive API documentation page.

## Running tests

```bash
source venv/bin/activate

# Unit tests only (fast, no corpus needed)
pytest tests/ -m "not integration" -n auto

# Full suite (parallel, requires ingested corpus and glyph_atlas/)
OMP_NUM_THREADS=1 pytest tests/ -n 3
```

Tests run in parallel via `pytest-xdist`. The ground-truth test suite verifies Recall@1 and MRR against known metric-compatible font pairs (Arial→Arimo, Times→Tinos, etc.).

## Project structure

```
fontmatch/
  features/
    metrics.py         — OpenType table feature extraction (11 values)
    perceptual.py      — Multi-glyph rendering + CLIP embedding (512 values)
    fingerprint.py     — Combines metric + perceptual into a Fingerprint
  fonts/
    loader.py          — TTF/OTF/WOFF/WOFF2 loading via fontTools
    variable.py        — Variable-font named instances via fvar (crash-safe)
  image/               — Image → free font engine (see docs/image-matching.md)
    fetch.py           — SSRF-guarded download of ChatGPT file URLs
    prep.py            — Safe decode, ink maps, deskew
    locate.py          — Tesseract line finding + text_hint handling
    glyphs.py          — Memory-mapped glyph atlas (+ rows.npy row profiles)
    rank.py            — Render-and-compare ranker (letter-spacing fit, row-profile prefilter, HOG, category vote)
    service.py         — ImageIdentifier used by the API
    catalog.py, synth.py, baseline.py, paths.py, errors.py
  mcp_server/
    server.py          — MCP server (ChatGPT app): find_free_font_from_image, find_free_alternatives
  index/
    store.py           — SQLite storage, in-memory vector index, identify()
    ingest.py          — Corpus ingestion pipeline
  match/
    scorer.py          — Distance computation, ranking, evaluation
  scrape/              — Web font crawler (CSS @font-face extraction)
  service/
    app.py             — Flask app factory, rate limiting, CORS
  web/
    routes.py          — HTML page routes
    api.py             — JSON API endpoints
    helpers.py         — Scoring display, URL builders, slug utilities
    similar.py         — Free alternatives by font name (page + API + MCP)
  templates/           — Jinja2 templates
  static/              — CSS, JS, favicon
scripts/
  build_corpus.py      — Ingest fonts from google-fonts-repo into SQLite
  crawl.py             — Crawl top sites for @font-face declarations
  build_glyph_index.py — Build glyph_atlas/ for image matching (~1 min)
  eval_image_identify.py, eval_browser_screenshots.py, tune_image_ranker.py, perf_image_identify.py — evaluation (see docs)
docs/
  image-matching.md    — Image matching + ChatGPT app: design, eval log, decisions, deploy runbook
  chatgpt-golden-prompts.md — Manual ChatGPT test plan
dev/
  IMPLEMENTATION_NOTES.md — Engineering notes: how the image matcher works internally, how to tune/evaluate, pitfalls
tests/
  fixtures/            — OFL-licensed test fonts
  test_ground_truth.py — Metric-compatible pair ranking verification
  test_image_styled*.py — Letter-spacing / multi-colour logo regressions (the Google logo bug)
  ...                  — Unit and integration tests
```

## Nginx reverse proxy (optional)

The app includes `ProxyFix(x_for=1)` so rate limiting uses the real client IP behind a reverse proxy.

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

## Requirements

- Python 3.10+
- `tesseract-ocr` system package (image matching)
- ~2 GB disk for CLIP model + Google Fonts corpus, plus ~780 MB for the glyph atlas (incl. the 81 MB `rows.npy`)
- ~1 GB RAM for the in-memory font index
- CPU only (no GPU required)

## License

Fonts in `tests/fixtures/` are licensed under the SIL Open Font License. See individual font directories for details.
