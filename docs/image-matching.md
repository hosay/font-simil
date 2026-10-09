# Image → free font matching, and the Dupefont ChatGPT app

Status: **Deployed 2026-10-03** (master `d918afc`, see README "Current deploy state"). Phases
0–3 were built and reviewed on branch `feature/image-identify`; the Phase 4 runbook below was executed
on 2026-10-03. Next: ChatGPT golden prompts (L5) and real-image evaluation.
This document is updated at the end of every phase.

## What it does

A user shows ChatGPT (or dupefont.com) an image containing text and asks "what font is this?".
We return the closest **open-source** fonts: family, license, similarity, Google Fonts link and our
`/similar-to/<font>` page. We never claim to name a commercial font; only fonts in our licensed
catalog (~3.9k files / ~2.8k families) can be answers.

## Architecture

```
ChatGPT ──HTTPS──> mr02 nginx (dupefont.com)
                     ├── /mcp ──tailnet──> a01:8088  dupefont-mcp (uvicorn, MCP SDK, no ML)
                     │                        │ fetches the image (SSRF-guarded)
                     │                        └── POST 127.0.0.1:8087/api/identify-image   (Phase 2/3)
                     └── /    ──tailnet──> a01:8087  fontmatch (gunicorn/Flask, owns the engine)
```

- **One engine, one copy of each model.** The MCP process is a thin adapter: it downloads the
  image and forwards it to Flask. It imports no torch/CLIP code.
- **Phase 0 spike (live now):** the deployed MCP tool fetches and decodes the image and reports
  what it received, to prove ChatGPT forwards chat images via `_meta["openai/fileParams"]`.
  The live process still runs the spike code until Phase 4 deploys the branch.

## How the matcher works (Phase 1)

1. **Load** (`prep.load_image`): PNG/JPEG/WebP only (Pillow `formats=`), ≤ 40 MP, EXIF
   orientation applied, transparency flattened on white, long edge ≤ 2000 px.
2. **Locate** (`locate.locate`): Tesseract finds text lines. With a `text_hint` (ChatGPT's own
   reading) we pick the line that best matches a hint segment and typeset the *hint's* wording;
   otherwise the most prominent line and Tesseract's reading. If OCR sees nothing but a hint
   exists, the whole image is treated as that line.
3. **Ink map** (`prep.ink_map`): soft text coverage in [0,1] = colour distance (CIE Lab ΔE) from the
   background (the border's most common colour), normalised per letter so multi-colour text (logos
   colour letters individually) reaches 1 everywhere; anti-aliasing kept. If the border colour makes
   most of the crop "ink" (text on a banner smaller than the crop), the whole crop's most common
   colour is tried and the reading whose ink looks like separate letters wins. Crops taller than
   400 px are downscaled first. **Deskew** by maximising projection-profile sharpness (±8°).
4. **Clean the crop**: drop ink not connected to the OCR text box (neighbouring lines, rules,
   icons). For long lines, keep only a window of whole words (≤ 40 chars) and crop to exactly
   those words' boxes. If the transcript came from the hint, pick the casing (as given / UPPER)
   whose proportions fit the image; ChatGPT may write "Welcome" for an all-caps "WELCOME".
5. **Render-and-compare** (`rank.ImageMatcher`): for every candidate face, typeset the same text
   from the **glyph atlas** (`glyphs.py`, built by `scripts/build_glyph_index.py`: ~4.6k faces,
   variable fonts expanded to Regular + Bold, memory-mapped; characters outside the atlas fall
   back to their unaccented form or a space-width gap) **at the letter-spacing that makes the
   line exactly as wide as the query** (`LineMetrics.fit_tracking`: one uniform tracking value
   per face from glyph metrics, clamped to −0.15…+0.6 em), downsample it to the query's pixel
   height (same resolution loss as the query), then score `shape − 0.5 × spacing`:
   - `shape`: Pearson correlation of the two lines at 32 px height and the query's width
   - `spacing`: how unusual the fitted letter-spacing is: free within −0.03…0 em (kerning,
     rounding), then 2 per em tighter / 1 per em looser, plus 3 × any width the clamped fit can't
     explain. Metric clones keep their advantage (their fitted spacing is ~0); a logo set 4%
     tighter than its font's default no longer loses to a font that happens to be narrower.
   A **prefilter** without rendering keeps the 1,200 most plausible faces: L1 distance between
   the vertical ink profiles of the query and each face's typesetting (`rows.npy`: per-glyph
   row-ink sums; the line profile is their sum, resampled between the 2%/98% ink-mass quantiles
   into 16 bands, so letter-spacing doesn't change it) + 0.1 × the spacing prior. The top 60 are
   re-ranked by adding `2.0 × HOG similarity` (gradient orientations). CLIP was tried as a
   re-ranker and rejected (< 1 point more, 1.4 GB model, ~3 s CPU per query).
6. **Dedupe** to one face per base family; return the top 5. `score` is the visual similarity
   (shape correlation, %), made non-increasing down the list. The top result is labelled
   "likely the same font" when its ranking key beats every other family's by ≥ 0.20
   (`Match.margin`, computed before the category vote; 93% precise on realistic dev queries,
   fires for 5.5% of fonts that aren't in the catalog). Variable fonts are shown under their family name ("Outfit", not the
   default-instance name "Outfit Thin" stored in name ID 1); links use the DB's name.

Known limitations: no kerning in the atlas; one uniform letter-spacing per line (word spacing
that differs from letter spacing, e.g. justified text, is absorbed by the resize); one line per
image (the hint's best-matching or the most prominent); only Regular/Bold of variable fonts;
scripts other than Latin are out of scope; white text in a transparent PNG is flattened onto
white and lost (unchanged).

**Next accuracy levers** (Phase 1 review, by expected gain per effort; letter-spacing fit done
2026-10-03): word-by-word alignment (also absorbs kerning drift); more
variable-font weights with a stroke-width estimate; kerning in the atlas.

## API and ChatGPT tools (Phases 2–3)

- `POST /api/identify-image` (multipart `image`, optional `text_hint`): `{transcript,
  transcript_source, text_box, matches[{name, family, style, license_id, category, score,
  similar_url, google_fonts_url, download_url}], cached}`. 400 bad image, 422 no text,
  503 engine unavailable. Results cached by sha256(image + hint) in `match_cache`
  (`img:` keys, `IMAGE_SCHEMA_VERSION`).
- `GET /api/similar-to?font=<name>`: free alternatives to a font by name (proprietary names such
  as Helvetica resolve through the existing alias table). Shares `fontmatch/web/similar.py` with
  the `/similar-to/<slug>` page.
- MCP tools: `find_free_font_from_image(image, text_hint)` and `find_free_alternatives(font_name)`.
  Both return structured results with absolute `dupefont_url`, `google_fonts_url` and a CSS
  `font-family` line. The MCP process calls the Flask API on localhost with
  `X-Internal-Token` (env `FONTMATCH_INTERNAL_TOKEN`, same value in both services), which skips
  the per-IP limits only for direct local calls (no `X-Forwarded-For`). The MCP process rate
  limits per ChatGPT user (`_meta["openai/subject"]`, 20/min) and globally (300/min).

### Files

| Path | Purpose |
|---|---|
| `fontmatch/image/fetch.py` | SSRF-guarded HTTPS download (see Security) |
| `fontmatch/image/catalog.py` | Candidate catalog: licensed fonts with a file on disk, base family, Google Fonts category |
| `fontmatch/image/synth.py` | Synthetic query images + degradation tiers (eval/tests) |
| `fontmatch/image/baseline.py` | B0 baseline ranker (whole-image CLIP vs glyph-sheet vectors) |
| `fontmatch/mcp_server/server.py` | MCP server (`create_server`, ASGI `create_app`) |
| `scripts/eval_image_identify.py` | Accuracy benchmark (writes `eval_reports/*.json`, gitignored) |
| `scripts/eval_browser_screenshots.py` | Chrome-rendered styled screenshots (letter-spacing, logos, dark mode): `render`, then `score` |
| `scripts/tune_image_ranker.py` | Collect raw ranker signals once, grid-search weights and calibrate the label offline (dev only) |
| `/etc/systemd/system/dupefont-mcp.service` | MCP service on a01 (user `fontmatch`, bound to the tailnet IP) |
| mr02 `/etc/nginx/sites-available/dupefont.com` | `location = /mcp` → `100.67.193.2:8088` (backup in `/root/dupefont.com.nginx.bak-*`) |

## ChatGPT / MCP contract

- Transport: Streamable HTTP at `https://dupefont.com/mcp`, stateless, JSON responses.
  DNS-rebinding protection on; allowed Hosts `dupefont.com`, `www.dupefont.com`, `127.0.0.1:*`,
  `localhost:*`; allowed Origins `https://chatgpt.com`, `https://chat.openai.com`. Requests
  without an Origin (ChatGPT's server-to-server calls) are accepted. Watch the journal for
  "Invalid Origin" during L5.
- `find_free_font_from_image(image: OpenAIFile, text_hint: str = "")`
  - `_meta: {"openai/fileParams": ["image"]}`
  - `OpenAIFile` declares `download_url`, `file_id`, `mime_type`, `file_name` (all strings),
    requires `download_url` + `file_id` (the Apps SDK "Scan Tools" step rejects anything else).
  - Annotations: `readOnlyHint: true`, `destructiveHint: false`, `openWorldHint: false`.
  - `text_hint` = ChatGPT's own reading of the text. It is the primary transcript; OCR is used
    to locate the text and as a fallback (the website has no hint).
- Errors are returned as tool errors (`isError: true`) with user-presentable text, never 500s
  (the SDK prefixes them with "Error executing tool <name>:").
- The `image` parameter's schema is inlined (no `$ref`), since the Apps SDK scanner inspects it.
- Limits: 20 calls/min per `openai/subject`; image tool 90/min globally and at most 4 in flight
  (fails fast with "busy"); other tools 300/min. Backend timeout 35 s + 15 s fetch, under nginx's
  130 s. The MCP service refuses to start without `FONTMATCH_INTERNAL_TOKEN`.
- Known risk: `/mcp` is unauthenticated, so a caller can rotate fake `openai/subject` values. The
  global caps and the in-flight limit bound the damage. nginx per-IP limits were rejected because
  all ChatGPT traffic arrives from a few OpenAI egress IPs.

## Security

- **SSRF** (`fetch.py`): https only, port 443, no credentials in URL; hostname resolved once and
  refused if *any* address is private/loopback/link-local/CGNAT (Tailscale)/multicast/reserved;
  the connection is pinned to the vetted IP (Host header + TLS SNI keep the hostname, so cert
  verification still applies); redirects followed manually (max 3) and re-checked; body streamed
  with a 10 MB cap; 15 s timeout. No host allowlist on purpose: OpenAI's file CDN can change.
- User images are not stored; caching (Phase 2) is keyed by content hash only.
- The daily per-IP rate limit in `app.py` would throttle *all* ChatGPT users together (they share
  OpenAI egress IPs). The MCP path needs its own limit (Phase 3).

## Fixes to the existing font-file matcher (fingerprint schema v5)

1. **CLIP saw only the middle of the glyph sheet.** open_clip's preprocess resizes the short side
   to 224 and center-crops; the 896×512 sheet became 392×224 and lost ~43% of its width (the
   outer glyph columns). v6 splits the sheet into square tiles (`perceptual.square_tiles`),
   embeds them in one batch and averages. Regression test: blanking the outer columns must change
   the embedding (it didn't before). (v5 padded the whole sheet instead; measured no better.)
2. **Variable fonts rendered their default instance**, which is Thin/Light for 251 of 735
   variable files (e.g. Bitter, Josefin Slab). `perceptual.pil_font` now selects the Regular
   (or Italic) named instance. Family names in the DB still come from the default instance
   (e.g. "Bitter Thin"); out of scope here.

3. **Pillow segfault.** `ImageFont.get_variation_names()` crashes on some fonts (Jaro[opsz]); this
   killed the first re-embed silently. Instance names/coordinates now come from fontTools `fvar`
   (`fontmatch/fonts/variable.py`) and are applied with `set_variation_by_axes`.

These change embeddings → `FINGERPRINT_SCHEMA_VERSION = 6`; the corpus must be re-embedded
(`python scripts/build_corpus.py`, adds v5 rows next to v4, so the running service is unaffected
until the new code is deployed).

## Validation methodology

| Layer | What | Gate? |
|---|---|---|
| L1 unit | fetch guards, image prep, normalization invariant (query pipeline on a render of font F ≈ index features of F), variable-font instancing, MCP schema | all green |
| L2 accuracy | `scripts/eval_image_identify.py` | see below |
| L3 MCP integration | in-process + real HTTP client: tools/list shape, tools/call, error paths | all green |
| L4 performance | p50/p95 latency, RSS, 4 concurrent calls | p95 < 3 s on 4 vCPU |
| L5 ChatGPT E2E | Developer mode, ~20 golden prompts incl. negatives; invocation precision/recall | manual sign-off |
| L6 security | SSRF, bombs, timeouts, rate limit, no image retention | all green |

**L2 details**

- *Query sets.* `corpus`: catalog fonts, one upright Regular per base family, stratified by
  Google Fonts category, split **by base family** (hash → 80% dev / 20% test) so weights/italics
  of one family never straddle the split. `external`: fonts not in the catalog (system fonts,
  fixtures), which is the real task; only category metrics apply there.
- *Tiers.* `clean` (diagnostic only: same renderer as the index), `screenshot` (colours,
  polarity, resampling, JPEG), `photo` (+rotation, perspective, blur, noise, lighting).
- *Scoring from one ranking.* `self` (own family allowed): family_hit@1/@5, a diagnostic that
  flatters the matcher. `lofo` (own family excluded, which simulates a commercial font that isn't in
  the corpus): category@1, category_frac@5, clone_hit@5 (Arimo↔Liberation Sans, Tinos↔Liberation
  Serif, Cousine↔Liberation Mono), file_overlap@5 (agreement with the font-file matcher;
  **diagnostic only**, because that matcher has its own biases).
- *Discipline.* Tune on `dev`; run `test` once per milestone; record every run in the log below.
- *Gates (initial, revisit after baseline).* external category@1 ≥ 0.8 (**met: 0.96**, hand
  labels, n=9); corpus LOFO category@1 ≥ 0.8 (**not met: 0.67**; the font-file matcher itself
  scores 0.78, and Google Fonts' "display" category is a grab-bag that drags this metric down);
  human "acceptable alternative" ≥ 70% on ~50 real screenshots (needs real images: L5/Phase 4).
- *External labels* live in `scripts/eval_external_labels.json` (category + known acceptable free
  substitutes, e.g. Nimbus Sans/Helvetica → Liberation Sans, Arimo, FreeSans).
- *Reference rankers.* `b0` (naive CLIP), `file` (existing matcher on the font **file**, an
  upper-bound-ish reference that also measures schema changes).

## Decision log

- Plan reviewed by a staff-engineer subagent before Phase 0. Adopted: variable-font instancing;
  whole-word render-and-compare instead of per-glyph segmentation; text_hint as the primary
  transcript; CLIP demoted to an ablation; eval redesigned around not-in-corpus queries and
  family-level splits; thin MCP adapter; IP-based SSRF guard; ChatGPT file spike first.
  Rejected: "Liberation fonts can never be answers": their `unknown` license passes the
  existing `!= ''` filter, so they are valid candidates.
- **Phase 0 review** (staff-engineer subagent). Adopted: prefer IPv4 when a host resolves to both
  (a01 has no global IPv6 route); `is_global`-based address check plus 6to4/Teredo/site-local;
  httpx errors mapped to a generic `FetchError`; total download deadline and `trust_env=False`;
  httpx/httpcore loggers at WARNING (signed URLs must not reach journald); JPEG `draft()` and
  thumbnail before mode conversions, tolerant EXIF transpose; the catalog is saved next to the
  atlas (`glyph_atlas/catalog.json`) so the service never depends on which fingerprint rows
  exist; eval groups sibling families (`family_group`: "X SC", "X Display", "X 9pt") for the
  split and LOFO exclusion; the external set's category labels come from our own classifier, so
  its numbers are **diagnostic only**. Deployment items (token in an `EnvironmentFile`, run the
  MCP service from the release checkout, not the worktree) go into Phase 4.
  Deferred, with reasons: variable-font *metric* weight still describes the default instance
  (another full re-embed; to do together with the next corpus rebuild); hostile uploaded fonts
  can segfault a gunicorn worker (this predates the feature, gunicorn respawns the worker, and
  the proper fix is a sandboxed render subprocess, which is a separate change).
- **Phase 2 review** (staff-engineer subagent). Adopted: reject queries wider than
  `MAX_QUERY_ASPECT` (a 2000×4 px strip would have allocated ~3 GB) and score candidates in
  chunks of 200; JSON 413 for `/api/*` (was a redirect to the HTML form) and the MCP fetch cap
  set to 9.5 MB so uploads fit `MAX_CONTENT_LENGTH`; 10/min limit on `/api/identify-image`;
  Tesseract timeout (20 s), `OMP_THREAD_LIMIT=1`, and OCR failures mapped to 503;
  result-cache key includes a content hash of the atlas + catalog (rebuilds never serve stale
  rankings) and URLs are derived after the cache read; `img:` cache rows pruned after 30 days at
  startup; a failed engine load is remembered for 60 s instead of retried per request; the token
  comparison is done on bytes (non-ASCII header no longer 500s).
  **Pre-existing bug found and fixed:** the `10 per minute` limits on `/api/identify` and the
  web upload form never applied, because `limiter.limit(view)` returns a new function that was
  never put back into `app.view_functions`.
  Skipped: gunicorn `post_fork` warm-up (there is no gunicorn config file; the first image request
  per worker costs ~1 s extra); caching 422 results; the 404 page's display name for a licensed
  font that has no current fingerprint (cosmetic edge case).
- **Phase 3 review** (staff-engineer subagent). Adopted: inline the `image` schema; separate
  image budget (90/min) plus a 4-slot in-flight semaphore that fails fast; backend timeout 35 s;
  limiter checks the global cap first and never stores denied subjects; MCP refuses to start
  without the internal token, and backend 429s are logged distinctly; docs aligned with the
  code; golden prompts extended (3 runs each, more edge cases). Not adopted: nginx per-IP
  limit (see risk above); custom error formatting (cosmetic). Open: commit the branch and
  run the MCP service from the release checkout (runbook step 6) before connecting ChatGPT for
  real use; L4 perf against live gunicorn at deploy.
- **Phase 1 review** (staff-engineer subagent). Adopted: HOG skipped below 16 px width (was a
  500 for "I", "!"); characters outside the atlas fall back to their base letter or a
  space-width gap and no longer count against coverage ("Škoda →" used to match nothing);
  deskew starts at 0° and needs a ≥ 2% gain, never on lines shorter than 3× their height; long
  lines are cut to a ≤ 40-character word window with the crop cut to the same words; ink not
  connected to the OCR box is dropped; hint casing is chosen to fit the image; quotes stripped
  from hints; substring matching between hint and OCR lines (OCR's text is used when the hint
  is only part of a line); displayed similarity follows the ranking and has a calibrated label.
  Not adopted: using OCR's reading when no line matches the hint (the eval showed OCR garbage
  on script/display fonts is the bigger failure); polarity from a ring outside the OCR box.
  Evaluation follow-ups: test split run once on the final code (below); the most valuable next
  evaluation is ~100 real screenshots/photos with ChatGPT-generated hints, scored by people for
  acceptable@5 (Chrome-rendered pages with known fonts are a cheap first step).
- **CLIP crop fix, measured.** File matcher, same 125 dev families, own family excluded:
  v4 (center-crop) category@1 0.784 / frac@5 0.749; v5 (pad whole sheet to square) 0.752 / 0.725,
  i.e. no better. Padding halves each glyph's resolution inside CLIP. v6 embeds the sheet as
  square tiles at full resolution and averages them; see eval log for the v4 vs v6 comparison.
- MCP SDK is v2 (`mcp==2.3.0`, `MCPServer`, protocol up to 2026-07-28). We use the SDK rather
  than a hand-rolled JSON-RPC endpoint inside Flask so protocol negotiation stays correct.

## Google logo fix (2026-10-03, branch `fix/multicolor-image`)

**Symptom.** The Google logo (and a screenshot of google.com) with hint "Google" returned Lusitana
82%, Habibi, League Spartan, Della Respira, Linden Hill: mostly serifs for a geometric sans.

**Root cause (measured, not the colours).** `shape` compared the image with each font typeset at its
*natural* letter-spacing, stretched to the image's width. The logo is set tighter than any font's
default, so every glyph drifted out of phase; round strokes then anti-correlate. Poppins Medium
(visually near-identical) scored 0.40, Lusitana 0.81. With a perfect ink mask (the PNG's alpha)
Lusitana still won, so the README's "multi-colour text breaks separation" was wrong; the yellow "o"
at half ink was a real but minor defect. Synthetic stress (65 dev fonts, family@1): plain 0.68,
Google colours 0.68, tight crop 0.68, −0.04 em spacing **0.37**, +0.12 em **0.19**. The eval never
varied spacing (synthetic queries use natural spacing, like the atlas), so it never saw this. The
prefilter (closest natural aspect) also dropped the true font for tracked text.

**Fix.** Letter-spacing fit per face + explicit spacing prior (replaces the aspect penalty);
spacing-invariant row-profile prefilter (`rows.npy`); colour (Lab ΔE) ink map with per-letter
levels; category vote among the re-ranked faces; "likely the same font" from the ranking margin;
variable fonts shown by family name. Details in "How the matcher works".

**New evaluation.** `scripts/eval_browser_screenshots.py` renders catalog fonts in headless Chrome
(Skia, kerning, CSS letter-spacing): web text, tracked caps, Google-colour logos, random-palette
logos, kerning-heavy words, dark mode. `scripts/tune_image_ranker.py` (rewritten) collects raw
signals once and grid-searches offline, including label calibration on in- and out-of-catalog
queries. Sets: tuning = 315 synthetic + 336 Chrome dev queries; validation = 384 Chrome renders
(dev split, other seed/styles); test = 450 Chrome renders + synthetic, test split, run once.

**Review decisions (staff-engineer subagent, 3 rounds).** Round 1 (plan): adopted a spacing-
invariant prefilter (the prototype's prefilter tied and kept faces in atlas order: family@1 0.24),
the explicit spacing prior, margin-based calibration, eval with out-of-catalog metrics, random
palettes, kerning words; deferred per-word tracking, local background estimation for photos.
Rejected after measuring: per-glyph elastic alignment (raised every candidate's fit, worse on the
logo); a 64 px re-rank (no gain on dev). Round 2 (mid): fixed ink-map inversion of heavy text on
tight crops, margin edge cases (one family left; rivals outside the HOG set), recalibrated the label
on out-of-catalog queries (0.06 fired for 28% of them; now 0.20: 5.5%), contiguous `rows.npy` layout,
cache key covers row-profile constants, huge crops downscaled, monkeypatchable prefilter size.
Not adopted: transparent PNG with white text (unchanged behaviour, separate fix).
Round 3 (final code review): fixed the label after the category vote (the margin now compares the
displayed winner's pre-vote key with the best other family's, via `rank.family_margins`, which the
tuner shares; a face that wins only through the vote gets no label); one vote per family
(`rank.majority_category`; keys can be negative); tuner aligned with the service (HOG set chosen by
the service score, bounded crops) and the label recalibrated on it; distinct log messages for a
stale vs missing `rows.npy`; "Thin Italic" stripped from display names; removed dead code
(`estimate_aspects`) and the rejected 64 px experiment.

## Real-image eval set: r/identifythisfont (2026-10-08, branch `feature/reddit-eval`)

Every earlier set is synthetic or Chrome-rendered. This one is real photos and screenshots that people
posted to r/identifythisfont, labelled with the font the community named. `dev/identifythisfont.py`:

- `scrape`: Camoufox at human pace (8-20 s per page, 1-3 min break every 20-30 requests, request
  budget, stops on 403/429/non-JSON and saves the page). Logged-out old.reddit redirects to login and
  `search.json` returns nothing, so it reads `www.reddit.com/r/identifythisfont/top.json` (all, year)
  and each post's `.json` inside the browser session. Needs `venv-scrape/`
  (`dev/requirements-scrape.txt`): Playwright 1.60 crashes the driver on Reddit page errors with
  Camoufox's Firefox 135; 1.51 works. Prod venv untouched. Output: `eval_reports/reddit_itf/`.
- `label`: keeps flair "Identified"; the answer is the comment the poster thanked (any depth; "thanks
  anyway", "close enough" and lookalike wording handled), else top-level agreement (two distinct
  authors, score-weighted, winner over twice the runner-up). Names come from link texts and font-site
  URL slugs, bold text, short answers, then known names (image catalog, proprietary map); one-word
  dictionary names (Play, Share) must be capitalized and not start a sentence.
- `build`: hand annotations (`scripts/eval_reddit_annotations.json`: crop box as fractions, transcript,
  category, corrected truth, free substitutes, or a skip reason) to `eval_reports/reddit_{dev,test}/`.
  `manifest.json` = truths in the catalog (tunable with `tune_image_ranker.py --browser`),
  `manifest_acceptable.json` = commercial truths scored on category and on named free substitutes.
  A row is test when its family, group or any substitute is a test family.

First scrape: 402 posts, 458 images; 208 labelled; 186 annotated (56 skipped: rotated, curved, 3D,
photo-filled or several fonts); 146 scoreable rows (dev 103, test 43). Most truths are commercial
(only 7 in the catalog), which is Dupefont's real use case.

**Baseline (master af27d08, dev, transcript as hint):** category@1 0.47 (sans 0.48, display 0.52,
handwriting 0.70, **serif 0.24**, mono 0.33 on 3), acceptable@5 0.07, catalog family@5 0/5.
Synthetic/Chrome sets gave category@1 0.69-0.78, so real photos are the main gap, and serifs most.

## Deploy runbook (Phase 4, executed 2026-10-03)

System packages: `apt install tesseract-ocr` (done on a01). Everything below runs on a01.

1. Merge `feature/image-identify` into `master`; update `/opt/projects/font_simil`.
2. `pip install -r requirements.txt` (adds pytesseract, mcp, uvicorn).
3. Re-embed the production DB **as the service user** (keeps file ownership right; the running
   service keeps serving v4 rows until restart):
   `sudo -u fontmatch env HF_HOME=/var/lib/fontmatch/.cache/huggingface OMP_NUM_THREADS=3
   nice -n 10 venv/bin/python scripts/build_corpus.py` (~30 min).
4. Build the atlas: `sudo -u fontmatch venv/bin/python scripts/build_glyph_index.py` (~2 min,
   ~700 MB on disk, memory-mapped).
5. Secrets: create `/etc/fontmatch/env` (root:fontmatch, 0640) with `SECRET_KEY=...` (moved out
   of the unit file) and `FONTMATCH_INTERNAL_TOKEN=<random 32 bytes>`; add
   `EnvironmentFile=/etc/fontmatch/env` to both `fontmatch.service` and `dupefont-mcp.service`.
6. Point `dupefont-mcp.service` `WorkingDirectory` at `/opt/projects/font_simil` (it currently
   runs the Phase 0 spike from the worktree).
7. `systemctl restart fontmatch dupefont-mcp`; smoke test: `/api/health`, `/similar-to/roboto`,
   `POST /api/identify-image` with a sample PNG, MCP `tools/list` + both tools via
   `https://dupefont.com/mcp`.
8. Run the golden prompts (`docs/chatgpt-golden-prompts.md`).

Rollback: check out the previous commit and restart. v4 fingerprint rows stay in the DB, so the
old code works immediately; the atlas and new endpoints are simply unused.

**Deploying the Google logo fix** (after merge): build the sidecar as the service user, then restart
(cached image results are invalidated by `IMAGE_SCHEMA_VERSION = 7`):
`runuser -u fontmatch -- env OMP_NUM_THREADS=2 nice -n 10 venv/bin/python scripts/build_glyph_index.py --rows-only`
→ `glyph_atlas/rows.npy` (81 MB), then `systemctl restart fontmatch`. Check the journal for
"rows.npy missing" (means the step was skipped). Rollback: previous commit + restart (the old code
ignores `rows.npy`).

## Eval log

| Date | Ranker | Set/split | Families | Notes | Key numbers |
|---|---|---|---|---|---|
| 2026-10-02 | b0 | corpus/dev | 10 | partial catalog, smoke test | category@1 0.22, family_hit@5 0.02 |
| 2026-10-02 | engine (binary masks, w=0.5/0.3) | corpus/dev | 34 | first run | family@1 clean 0.74 / screenshot 0.56 / photo 0.21; category@1 0.68 |
| 2026-10-02 | engine (soft ink maps, query-resolution matching) | corpus/dev | 34 | | family@1 clean 0.79 / screenshot 0.47 / photo 0.24; category@1 0.78 |
| 2026-10-02 | tune grid (aspect × ink) | corpus/dev | 104 | weights barely matter (objective 0.535–0.560) | best aspect 0.25, ink 0.0 → adopted |
| 2026-10-02 | file v4 / v5 / v6 | corpus/dev | 125 | same queries, family-grouped LOFO | category@1 0.776 / 0.752 / 0.760; frac@5 0.744 / 0.725 / 0.744 → crop fix is accuracy-neutral; v6 kept (all glyphs, no measured cost) |
| 2026-10-02 | engine (tuned, v6 atlas) | corpus/dev | 125 | hint=exact | family@1 clean 0.85 / screenshot 0.66 / photo 0.35; family@5 0.90 / 0.74 / 0.52; category@1 (LOFO) 0.67; p95 1.0 s |
| 2026-10-02 | b0 | corpus/dev | 125 | | family@5 0.02; category@1 0.23 |
| 2026-10-02 | engine | corpus/dev | 65 | hint=none (OCR only, website path) | family@1 0.51; transcript exact 0.68; no result 8% |
| 2026-10-02 | engine / file | external (hand-labelled) | 9 | real task: commercial clones not in corpus | category@1 **0.96** / 0.78; acceptable substitute@5 **0.59** / 0.56 (engine clean tier 0.78) |
| 2026-10-02 | perf (L4) | | 20 | one process, one core | p50 1.0 s, p95 1.2 s; private RSS ~220 MB/worker + ~640 MB shared mmap atlas |
| 2026-10-02 | ablation (re-rank top 60) | corpus/dev | 45 | shape+aspect / +HOG / +HOG+CLIP | objective 0.620 / 0.641 / 0.648 → HOG adopted, CLIP rejected |
| 2026-10-02 | engine + HOG + hint trust | corpus/dev | 125 | | family@1 clean 0.86 / screenshot 0.68 / photo 0.42; category@1 0.72 [CI 0.65–0.78]; transcript exact 1.00 |
| 2026-10-02 | **engine, final code** | **corpus/test (held out, run once)** | 106 | Phase 1 review fixes included | family@1 clean 0.76 / screenshot 0.68 / photo 0.43 (all 0.62 [CI 0.56–0.69]); family@5 0.71 [0.65–0.77]; category@1 0.69 [0.63–0.76]; p95 1.4 s |
| 2026-10-02 | casing fix | corpus/dev | 40 | test split showed transcript exact 0.85: aspect-based casing switched mixed case to UPPER | score-based casing choice: 96% correct (aspect-based: ~80%). Fixed and validated on dev only; test numbers above are as measured, with the bug |
| 2026-10-02 | engine + casing fix (final) | corpus/dev | 125 | | family@1 clean 0.88 / screenshot 0.72 / photo 0.46 (all 0.69 [CI 0.63–0.74]); family@5 0.77; category@1 0.73 [0.66–0.79]; transcript exact 0.96; p95 1.8 s |

| 2026-10-03 | stress (synthetic, letter-by-letter) | corpus/dev | 65 | Google-logo investigation, master code | family@1 plain 0.68 / Google colours 0.68 / tight crop 0.68 / −0.04 em **0.37** / +0.12 em **0.19** |
| 2026-10-03 | master vs fix | browser/dev validation (Chrome, 384) | 64 | other seed + random-palette logos + kerning words | family@1 0.57 → 0.71; @5 0.67 → 0.85; LOFO category@1 0.66 → 0.75; serif in a sans's top 5 0.048 → 0.00 |
| 2026-10-03 | master vs fix | corpus/dev (synthetic) | 125 | | family@1 0.67 → 0.69; category@1 0.72 → 0.77; photo family@1 0.43 → 0.52 |
| 2026-10-03 | **master vs fix** | **browser/test (Chrome, 450, run once)** | 75 | held out | family@1 0.55 → **0.70**; @5 0.65 → **0.84**; LOFO category@1 0.70 → **0.78**; serif in a sans's top 5 0.062 → 0.031; tracked caps family@1 0.04 → 0.60; logos 0.53/0.57 → 0.69/0.71; p95 1.94 → 2.01 s |
| 2026-10-03 | **master vs fix** | **corpus/test (synthetic, run once)** | 110 | held out | family@1 0.65 → 0.66; category@1 0.70 → **0.80** [CI 0.74–0.86 vs 0.62–0.76]; photo 0.40 → 0.46; clean tier (diagnostic) 0.79 → 0.76, @5 0.94 → 0.86: the spacing prior and category vote cost a little on perfectly spaced text |
| 2026-10-03 | real Google logo PNG | | 2 | 544×184 and 272×92, hint "Google" | master: Lusitana 78, Habibi, League Spartan, Della Respira, Linden Hill → fix: **Outfit 87**, League Spartan, Teachers, Red Hat Text, Padauk (all sans, "similar alternative") |
