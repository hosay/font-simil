# Open-Source Font Matcher — Implementation Plan (TDD, Phased)

A plan for Claude Code to implement a system that, given an input font file,
identifies the closest **open-source** font. The system pre-computes
fingerprints for a reference corpus and for fonts scraped from top websites,
so production responses are cached lookups rather than recomputes.

Deliverables: (1) a Python backend service, (2) an agent **skill** that wraps it.

---

## 0. Operating principles

**Methodology — strict TDD.** Every unit of behavior follows Red → Green →
Refactor:

1. Write a failing test that describes the desired behavior.
2. Write the minimum code to make it pass.
3. Refactor with tests green.

Do not write implementation code before a failing test exists for it. Each phase
below lists its tests *first*, then the implementation, then acceptance criteria.
A phase is "done" only when its acceptance criteria pass and the full suite is
green.

**Determinism is a hard requirement.** Perceptual fingerprints depend on glyph
rendering, and FreeType output varies across versions and settings. Therefore:

- Pin the FreeType / Pillow versions in the lockfile.
- Fix all rendering parameters (DPI, antialiasing, hinting **off**, no LCD
  filtering, fixed canvas size).
- Record a `renderer_version` and `fingerprint_schema_version` with every stored
  fingerprint so caches can be invalidated and migrated.

**Constraints to respect throughout.** Target machine is 16 GB RAM, CPU-only, no
GPU. No model larger than a few hundred MB. Prefer deterministic, classical
features over heavy ML for v1.

**Ground truth we exploit.** Several open-source families are *deliberately
metric-compatible* replacements for famous proprietary fonts. These give us hard,
objective test pairs:

| Proprietary (query) | Expected open-source match |
|---|---|
| Arial / Helvetica | Liberation Sans, Arimo |
| Times New Roman | Liberation Serif, Tinos |
| Courier New | Liberation Mono, Cousine |
| Calibri | Carlito |
| Cambria | Caladea |
| Georgia | Gelasio |

These pairs are the backbone of the matcher's evaluation tests.

**License discipline.** The system must only ever *return* OFL / Apache-2.0 (or
otherwise freely usable) fonts as matches. License metadata is tracked per font
and matches that aren't open-source are excluded from results.

---

## 1. Repository & tooling scaffold

**Tests first**
- A smoke test asserting the package imports and `pytest` discovers tests.
- A test asserting the dependency lockfile resolves (CI invariant).

**Implement**
- Project layout:
  ```
  fontmatch/
    __init__.py
    fonts/        # loading, validation, format handling
    features/     # metric + perceptual feature extraction
    index/        # storage + nearest-neighbor
    match/        # scoring + matcher orchestration
    scrape/       # web font scraper (later phase)
    service/      # FastAPI app
  tests/
    fixtures/     # bundled test fonts + sample CSS/HTML
  skill/          # agent skill package
  ```
- Use `uv` (or venv + pip) with a pinned lockfile. Core deps: `fontTools`,
  `brotli` (for WOFF2), `Pillow`, `numpy`, `pytest`, `pytest-cov`, `ruff`,
  `fastapi`, `uvicorn`, `httpx`.
- Add `ruff` lint + format and a pre-commit hook. Add a `Makefile`/`justfile`
  with `test`, `lint`, `serve` targets.

**Acceptance**
- `pytest` runs and passes the smoke test; lint is clean.

---

## 2. Font loading, validation & format handling

**Tests first** (using a handful of OFL fonts committed to `tests/fixtures/`)
- Opens TTF and OTF and reports family/subfamily/PostScript name.
- Decompresses WOFF and WOFF2 to an openable font (WOFF2 exercises `brotli`).
- Handles TrueType Collections (`.ttc`) by enumerating contained faces.
- Rejects a corrupt/non-font file with a typed error (no crash).
- For a variable font, resolves to the default instance (and can list named
  instances).

**Implement**
- `fonts.load(path_or_bytes) -> LoadedFont` wrapping `fontTools.ttLib.TTFont`,
  normalizing across TTF/OTF/WOFF/WOFF2/TTC and variable fonts.
- A stable `file_hash` (sha256 of the raw bytes) computed on load — this is the
  cache key used everywhere downstream.

**Acceptance**
- All fixture formats load; corrupt input raises `UnsupportedFontError`.

---

## 3. Metric feature extraction (fast, deterministic, from font tables)

**Tests first**
- For known fixtures, asserts extracted values within tolerance: units-per-em,
  OS/2 `usWeightClass`, `usWidthClass`, italic flag/angle, cap height, x-height,
  ascender/descender, average advance width, glyph count.
- Serif vs sans classification returns the correct label for known serif and
  sans fixtures (heuristic: stem-contrast + glyph-shape features; PANOSE byte 1
  when present, with a rendered-stroke fallback).
- The output is a fixed-length, ordered numeric vector with documented indices,
  and is byte-for-byte reproducible across runs.

**Implement**
- `features.metrics(loaded_font) -> MetricVector`: pull from `head`, `OS/2`,
  `hhea`, `post`, `glyf`/`CFF` tables. Derive x-height/cap-height by measuring
  reference glyph bounding boxes (`x`, `H`) when table values are absent.
- Normalize all size-dependent metrics by units-per-em so the vector is
  scale-invariant.
- Define `MetricVector` as a frozen dataclass + `.to_array()` with a versioned,
  documented field order.

**Acceptance**
- Metric vectors are stable and match expected values for fixtures within
  tolerance; serif/sans classification is correct on the fixture set.

---

## 4. Glyph rendering & perceptual features

**Tests first**
- Rendering the same font twice yields an **identical** perceptual vector
  (determinism guard).
- Bold and Regular of the *same* family are closer (smaller distance) than two
  unrelated families (relative-ordering test).
- The rendered montage is normalized: two fonts set at different nominal sizes
  but equal cap-height produce comparably scaled renders (scale-invariance
  within tolerance).
- Missing-glyph handling: a font lacking a reference glyph falls back gracefully
  (substitution recorded, no crash).

**Implement**
- `features.render(loaded_font) -> ndarray`: render a fixed reference string
  (e.g. `"Hamburgefonstiv 0123 ,.?!"`) to a fixed grayscale canvas via Pillow +
  FreeType with the pinned, fixed rendering parameters from §0. Normalize by
  cap-height and align to baseline.
- `features.perceptual(render) -> ndarray`: produce a fixed-length vector —
  start with downsampled pixel intensities plus a HOG descriptor (both classical,
  CPU-cheap, deterministic). Keep this behind an interface so a small learned
  embedding can replace it later without touching callers.
- Combine metric + perceptual into a single `Fingerprint` carrying
  `fingerprint_schema_version` and `renderer_version`.

**Acceptance**
- Determinism, relative-ordering, and scale-invariance tests pass.

---

## 5. Similarity scoring & the matcher

**Tests first** — this is the most important test set; it uses the ground-truth
table from §0.
- Given a small corpus containing the open-source clones, querying with
  Arial-equivalent metrics ranks **Liberation Sans / Arimo** at position 1;
  Times → Tinos/Liberation Serif at 1; Courier → Cousine/Liberation Mono at 1;
  etc.
- An evaluation harness computes **Recall@1**, **Recall@5**, and **MRR** over all
  ground-truth pairs and asserts they exceed locked thresholds (e.g. Recall@5 =
  1.0, Recall@1 ≥ 0.8 on the seed set).
- Weight-blend tuning is captured as a regression test: the chosen
  metric-vs-perceptual weighting is asserted via golden expected scores with
  tolerance, so future changes can't silently degrade ranking.
- Matches whose license is not open-source are excluded from results.

**Implement**
- `match.distance(fp_query, fp_candidate) -> float`: normalized weighted blend of
  metric distance and perceptual distance. A two-stage option (coarse filter by
  category/weight/width, then perceptual rerank) for speed at scale.
- `match.rank(fp_query, corpus, k) -> list[Match]` returning ranked candidates
  with per-component sub-scores for explainability.
- `match.evaluate(pairs) -> {recall@k, mrr}` harness used by tests and CI.

**Acceptance**
- Ground-truth ranking tests and the Recall/MRR thresholds pass.

---

## 6. Persistence & nearest-neighbor index

**Tests first**
- Round-trip: store a fingerprint, reload it, get an identical vector.
- Cache behavior: a second `identify` of the same `file_hash` +
  `fingerprint_schema_version` returns the cached result **without** recomputing
  (assert via a spy/mock on the feature extractor).
- Schema-version mismatch invalidates cache and triggers recompute.
- Nearest-neighbor over a known small corpus returns the same ranking as the
  brute-force reference.

**Implement**
- SQLite schema:
  - `fonts(id, file_hash UNIQUE, family, subfamily, style_flags, postscript_name,
    units_per_em, source, license, file_ref, created_at)`
  - `fingerprints(font_id, schema_version, renderer_version, metric_vec BLOB,
    perceptual_vec BLOB)`
  - `match_cache(query_hash, schema_version, result_json, created_at)`
  - `sites(...)`, `site_fonts(...)` (used by §8 scraper)
- Vector search: start with brute-force numpy cosine/L2 (microsecond-fast at
  10–50K vectors). Hide it behind an `Index` interface so `faiss-cpu` or
  `hnswlib` can drop in if the corpus grows.

**Acceptance**
- Persistence round-trips; cache hit avoids recompute; index matches brute-force.

---

## 7. Reference corpus ingestion (Google Fonts)

**Tests first**
- Ingesting a small fixture directory of fonts produces N fingerprints and N
  `fonts` rows; re-running is idempotent (no duplicates, by `file_hash`).
- License metadata is captured per family from its `OFL.txt` / `LICENSE`.
- Integration: after ingesting the fixture corpus, a held-out query font returns
  sensible ranked matches end-to-end.

**Implement**
- `index.ingest_corpus(path)`: clone/enumerate the Google Fonts repo, walk
  families/styles, load each face, extract fingerprints, attach license, persist,
  and build the index. Resumable and idempotent.

**Acceptance**
- Idempotent ingestion; license captured; end-to-end query works on fixtures.

---

## 8. Web font scraper (top 10K sites → cached match DB)

**Tests first** (no live network in tests — use fixtures + a local mock server)
- CSS parser extracts `@font-face` `src` URLs from sample CSS, resolving relative
  → absolute URLs and selecting `woff2` when multiple formats are offered.
- Downloader fetches a font from the local fixture server, dedupes by
  `file_hash`, and skips already-seen hashes.
- Robots/rate-limit policy is honored (assert that a disallowed path is skipped).
- A crawl run is resumable from a persisted frontier/checkpoint.
- Pipeline integration: a mocked site yields a font that gets fingerprinted,
  matched, and its result written to `match_cache`.

**Implement**
- `scrape.crawl(site_list)`: load a public ranking (e.g. Tranco) as input; fetch
  pages; parse linked CSS and inline `@font-face`; download font files; dedupe by
  hash; store provenance in `sites`/`site_fonts`. Bounded concurrency, polite
  rate limiting, robots compliance, retries, checkpointing.
- After download, route each new font through §3–6 to populate the cache so
  production lookups are warm.

**Acceptance**
- Parser/downloader/dedupe/robots/resume tests pass; pipeline writes cached
  matches for fixture sites. (Run the real 10K crawl as an operational task, not
  in the test suite.)

---

## 9. Backend service (FastAPI)

**Tests first** (FastAPI `TestClient` / `httpx`)
- `GET /health` → 200.
- `POST /identify` with a fixture font upload → response contains a `matches`
  array with exactly 1 result by default; the match for a ground-truth fixture is
  the expected open-source font.
- `POST /identify` with `?n=5` returns up to 5 ranked matches in the `matches`
  array.
- Second identical upload is served from cache (assert no recompute via spy).
- Unsupported/corrupt upload → 400 with a typed error body.
- Response includes per-match sub-scores and license for explainability.

**Implement**
- Endpoints: `GET /health`, `POST /identify` (multipart font upload; optional
  query param `n` controls number of results, default 1; response always uses a
  `matches` array), `GET /fonts/{id}`. Content-hash caching in front of the
  matcher.
- Structured error handling mapping `UnsupportedFontError` → 400.

**Acceptance**
- API tests pass, including cache and error paths.

---

## 10. Agent skill packaging

**Tests first**
- The skill's CLI/script, run against a fixture font, prints the expected top
  match (smoke test of the documented invocation).
- `SKILL.md` examples are executable and produce the documented output.

**Implement**
- `skill/SKILL.md`: describe *when* to use it ("identify the closest open-source
  font for a given font file") and *how* — a CLI that calls the running service,
  or runs matching locally against the prebuilt DB when offline.
- Bundle a thin `identify_font` script + usage examples and the path to the
  prebuilt SQLite DB.

**Acceptance**
- Skill smoke test passes; documented commands work as written.

---

## 11. Perceptual upgrade: multi-glyph CLIP embeddings (COMPLETED 2026-06-03)

**Problem:** The v1–v3 perceptual pipeline rendered a single reference sentence,
downsampled to 100×15 pixels, and compared raw pixel intensities (1,615 floats).
This was dominated by string content rather than font character, sensitive to
rendering engine differences, and used cosine distance on raw pixels — a poor
geometric fit.

**Solution implemented (schema v4):**

1. **Multi-glyph rendering.** Instead of one sentence, render 27 individual
   diagnostic characters (`aegnosfilbdpqRSHOQBWM01589&@`) at 128×128 px each,
   composed into a 7-column grid sheet. Characters chosen for typographic
   distinctiveness: single/double-story a/g, stroke contrast (S, B, W), counters,
   ascenders/descenders, and numeric/symbol style.

2. **CLIP visual embeddings.** Pass the glyph sheet through OpenCLIP ViT-B/32
   (laion2b_s34b_b79k pretrained) to produce a 512-dim unit-normalized embedding.
   Cosine distance is semantically meaningful in this space — the model was trained
   on hundreds of millions of image-text pairs with strong typographic awareness.

3. **Scorer recalibration.** `PERCEPTUAL_SCALE` adjusted from 0.37 to 0.06
   (calibrated from p95 of pairwise CLIP cosine distances across the corpus).
   The 40/60 metric/perceptual weight blend is preserved.

**Results:**
- Ground-truth pairs (Liberation Sans ↔ Arimo) show cosine distances of 0.0003,
  vs unrelated fonts at 0.01–0.07 (10–100× separation improvement).
- **Recall@1 = 1.0** on all 6 core metric-compatible pairs against a 3,871-font
  corpus. All 14 ground-truth tests pass.
- 137/137 total tests pass, zero regressions.
- Thread-safe CLIP singleton with double-checked locking for Flask threading.
- Blank-sheet guard returns zero vector (filtered by `build_index`).

**Files changed:**
- `fontmatch/features/perceptual.py` — new `render_glyphs()`, `compose_sheet()`,
  CLIP `perceptual()`. Legacy `render()` kept for backward compat.
- `fontmatch/features/fingerprint.py` — updated to use new pipeline.
- `fontmatch/match/scorer.py` — `PERCEPTUAL_SCALE` = 0.06.
- `tests/test_perceptual.py` — rewritten with 14 tests covering glyphs, sheets,
  CLIP embedding, determinism, ordering, and edge cases.

---

## 12. SQLite thread-safety & hammer testing (COMPLETED 2026-06-03)

**Problem:** `FontStore` used a single `sqlite3` connection with
`check_same_thread=False`, shared across all Flask request threads. When a
browser loaded a `/similar-to/` page, it triggered multiple concurrent
`@font-face` requests to `/api/font-file/`, causing:
- `sqlite3.OperationalError: cannot start a transaction within a transaction`
- `sqlite3.InterfaceError: bad parameter or other API misuse`

**Solution implemented:**

1. **`threading.RLock` on all DB access.** Every method in `FontStore` that
   touches `self.conn` is wrapped with `with self._lock:`. Uses `RLock`
   (reentrant) because `search_font_families()` calls `has_google_fonts_source()`
   while holding the lock.

2. **`ProxyFix` for reverse-proxy support.** Added `ProxyFix(x_for=1)` so
   `X-Forwarded-For` is respected by both Flask-Limiter and the daily rate
   limiter when running behind nginx/caddy.

3. **404 for unknown font slugs.** `/similar-to/<slug>` now returns HTTP 404
   (not 200) when the font is not in the database. Slugs >100 chars or
   containing null bytes are rejected with 404.

**Hammer test results (38,066 requests, 0 5xx errors):**
- 9 phases: edge cases, cache effectiveness, uncached search, read stress
  (50 visitors), write stress (20 visitors), mixed read/write (30 visitors),
  DB locking stress (50 visitors), font-file storm (50 visitors), rate limits.
- Each thread simulates a distinct visitor via `X-Forwarded-For`.
- Zero server errors across all phases.

**Files changed:**
- `fontmatch/index/store.py` — `threading.RLock`, all methods wrapped.
- `fontmatch/service/app.py` — `ProxyFix(x_for=1)`.
- `fontmatch/web/routes.py` — 404 for unknown fonts, slug validation, `abort`.
- `fontmatch/web/api.py` — `store._lock` on direct `store.conn.execute()`.
- `tests/test_api.py` — `test_concurrent_font_file_requests`.
- `tests/test_web.py` — 404 tests for unknown/long/null-byte slugs.
- `hammer_test.py` — 9-phase multi-visitor stress test.

---

## 13. Future phases (outline only)

- **Image input / font recognition.** Detect and segment glyphs from an image,
  normalize, then match in glyph space against the existing corpus (render
  candidates and compare) rather than relying on a general image embedder.
- **New-font onboarding.** A pipeline endpoint to fingerprint and index newly
  discovered fonts on demand, reusing §3–7.
- **Contrastive fine-tuning.** Fine-tune the CLIP encoder on the `user_scores`
  table using contrastive loss (triplet/InfoNCE), learning what humans consider
  similar rather than relying on pretrained CLIP alone. Requires ~5,000+ rated
  pairs for meaningful improvement.
- **FAISS ANN index.** Replace brute-force O(n) scan with FAISS `IndexFlatIP`
  or `IndexIVFFlat` if corpus grows past ~50K fonts.

---

## Cross-cutting requirements

- **Evaluation in CI.** The §5 Recall@k / MRR harness runs on every change;
  ranking regressions fail the build.
- **Performance budget.** Assert single-font `identify` latency (cold and cached)
  stays within a target on the CPU target machine; track corpus ingestion
  throughput.
- **Reproducibility.** Pinned lockfile; pinned FreeType/Pillow; fixed rendering
  params; `renderer_version` + `fingerprint_schema_version` stored with every
  fingerprint and checked on cache reads.
- **Test data hygiene.** Only commit freely licensed fonts to `tests/fixtures/`;
  document each fixture's source and license.


Majestic URL download: https://downloads.majestic.com/majestic_million.csv
