# Implementation notes: image → free font matcher

Engineering notes for whoever changes `fontmatch/image/` next. They cover how the matcher
works internally, why it is built this way, how to measure a change, and the traps found so
far. The design doc is [`docs/image-matching.md`](../docs/image-matching.md): architecture,
the full eval log, the review decision log, the deploy runbook. This file doesn't repeat those
numbers except where they explain a decision.

Last updated 2026-10-05, after the Google logo fix (`72bc19f`) and the Google Fonts link fix
(`373c7f4`).

## 1. Pipeline, by file

```
bytes ─ prep.load_image ─ locate.locate ─ rank.rank_located ─────────────── service.ImageIdentifier
        (decode, EXIF,    (Tesseract     ├ _bounded_crop (≤ 400 px tall)    (labels, display names,
         flatten alpha,    lines + hint)  ├ prep.ink_map  (Lab ΔE, per-letter) dedupe already done)
         ≤ 2000 px)                       ├ keep_components_touching(OCR box)
                                          ├ ImageMatcher.best_casing (hint only)
                                          └ ImageMatcher.rank_mask
                                              components(): metrics → fit_tracking → prefilter_order
                                                            → typeset at fitted tracking → shape
                                              _hog_rerank(): top 60 + HOG
                                              _off_category(): category vote
                                              family_margins(): confidence
```

| Step | Code | What it does |
|---|---|---|
| Line geometry without rendering | `ImageMatcher.line_metrics` → `LineMetrics` | Per face: ink width/height of the typeset text and `steps` (advances between first and last drawn glyph), all from `atlas.meta` in one vectorised pass over the text |
| Letter-spacing fit | `LineMetrics.fit_tracking(query_aspect)` | `t = (A_q·h − w) / steps`, in em, clamped to `[TRACK_MIN_EM, TRACK_MAX_EM]`; returns `(tracking_em, residual)` |
| Prefilter | `prefilter_order` | Keeps `PREFILTER_FACES` (1200) of ~4.6k faces by `L1(profile) + PREFILTER_SPACING_WEIGHT × spacing_penalty`. Rendering costs; this doesn't |
| Row profiles | `glyphs.row_profiles(ci)`, `ImageMatcher.line_profiles`, `mass_profiles`, `query_profile` | Line profile = sum of per-glyph row-ink profiles (baseline-relative, from `rows.npy`), resampled between the 2%/98% ink-mass quantiles into 16 bands. Spacing-invariant |
| Shape | `components` | Each candidate is composed with `atlas.compose(face, text, tracking=px)`, downsampled to the query's pixel height, resized to the query's width at 32 px, blurred σ=1, then Pearson-correlated (batched in chunks of 200) |
| Score | `Signals.score()` | `shape − SPACING_WEIGHT·spacing_penalty − INK_WEIGHT·ink_penalty` |
| Spacing prior | `spacing_penalty(t, residual)` | Free inside `TRACK_FREE_EM` = (−0.03, 0) em; `TRACK_SLOPE` = 2 per em tighter, 1 per em looser; plus `RESIDUAL_WEIGHT` (3) × the width the clamped fit couldn't explain |
| Re-rank | `_hog_rerank` → `(lifted, key, reranked)` | Top `RERANK_TOP` (60) by score get `+ HOG_WEIGHT × HOG similarity`. `lifted` (ordering) also floats them above everyone else. `key` has no lift, so differences between re-ranked faces mean something |
| Category vote | `_off_category`, `majority_category` | One vote per family among the top 3 by `key`; re-ranked faces outside the majority Google Fonts category lose `CATEGORY_VOTE` (0.1). Ties go to the top family's category. Needs `ImageMatcher(category_of=...)`; without it, no vote |
| Confidence | `family_margins(families, key, reranked)` → `Match.margin` | A face's pre-vote `key` minus the best pre-vote key of any other family, among re-ranked faces only. `service.SAME_FONT_MARGIN` (0.20) turns it into "likely the same font" |
| Display | `service.display_family` | Variable fonts store their default instance in name ID 1 ("Outfit Thin"); the atlas renders Regular/Bold, so a trailing weight word (+ optional "Italic") is stripped when `style != "default"` |
| Links | `api._add_image_urls`, `store.get_font_family`, `store.google_fonts_source`, `helpers.google_fonts_name` | URLs are built from the DB family (lookups match it exactly), and the Google Fonts name comes from `METADATA.pb` |

### Data files

| File | Shape / format | Built by | Notes |
|---|---|---|---|
| `glyph_atlas/pixels.bin` | uint8, glyph bitmaps concatenated | `build_glyph_index.py` | memory-mapped (~670 MB, shared by workers) |
| `glyph_atlas/meta.npy` | int32 (faces, chars, 5): offset, w, h, x, y | same | `w = -1` = glyph missing |
| `glyph_atlas/rows.npy` | float16 (**chars**, faces, 56): ink per 2 px band from 72 px above to 40 px below the baseline (EM = 48 px) | `build_atlas` writes it; `--rows-only` for an existing atlas (~20 s) | Character-major so one character's row is one contiguous read. Wrong shape → ignored with a "doesn't match" warning; missing → computed lazily per character per worker (~0.1 s each, up to ~50 MB per worker) with a "missing" warning |

The result cache key (`ImageIdentifier.version`) hashes `IMAGE_SCHEMA_VERSION` (7), the atlas
face list, the catalog and the `ROW_*` constants. Bump the schema version whenever ranking code
changes; atlas and catalog rebuilds invalidate the cache on their own.

## 2. Why it is built this way

**The Google logo bug was spacing, not colour.** Before the fix, every candidate was typeset at its
natural spacing and stretched to the image's width. The logo is set about 4% tighter than any
font's default, so glyph positions drift and round strokes anti-correlate: Poppins Medium scored
0.40, Lusitana 0.81. Measured on 65 dev fonts (family@1): plain 0.68, −0.04 em **0.37**, +0.12 em
**0.19**, Google colours 0.68. With a perfect ink mask (the PNG's alpha), Lusitana still won.

**One uniform tracking value per face, solved in closed form.** Compose is linear in tracking, so
the right value comes straight from the metrics: no search, no extra render. A uniform value
can't absorb kerning pairs or justified text; the remaining width mismatch is absorbed by the
resize, as before.

**The aspect penalty became an explicit spacing prior.** `|log(A_q / A_natural)|` is roughly
`t / mean advance`, so the old penalty was a hidden tracking penalty that punished the true font
for exactly the spacing the image used. Metric clones (Arimo vs Liberation Sans) keep their
advantage because their fitted tracking is about 0. Clone hit@5 went from 0.67 to 0.87 on dev.

**The prefilter had to become spacing-invariant.** After the fit, every face fits the width, so
"closest natural aspect" ties. The first prototype kept faces in atlas order and scored 0.24
family@1. Row profiles don't move when letters move sideways. They are compared between
ink-*mass* quantiles, not the ink box: one faint anti-aliased row (rasterisers differ) moved a
box-based profile as much as a different font.

**The colour ink map** uses Lab ΔE from the border's most common colour, normalised per connected
component (floor 0.5 × global level, specks under 12 px use the global level). This lets
multi-colour logos reach full ink. A banner smaller than the crop makes "background" the
majority, so the whole-image mode colour is also tried. The reading whose ink looks like several
letters wins (`_largest_share`). A naive "smaller ink area wins" inverted bold, tightly cropped
text.

**The category vote and the margin label** came from measurement. Shape correlation stopped
predicting correctness once spacing was fitted (about 75% precision at any threshold). The
ranking margin works: at 0.20 it is right 93% of the time and fires for 5.5% of out-of-catalog
fonts. The vote halves "a serif among a sans font's alternatives", for −0 to −4 points of
family@1.

**Rejected after measuring** (don't retry without new evidence):
- Per-glyph elastic alignment: raised every candidate's fit; worse on the logo.
- A 64 px re-rank: no gain on dev.
- More blur: erases serifs, which are the very thing being missed.
- A 2000-face prefilter: +1 to 3 points (noise level) for +0.6 s at p95.
- CLIP as a re-ranker (earlier): < 1 point for a 1.4 GB model and ~3 s on CPU.

## 3. Measuring a change

All evaluation is dev-split only. The test split was used once (2026-10-03); don't tune on it.

```bash
source venv/bin/activate; export OMP_NUM_THREADS=1   # always cap threads on this shared box

# Chrome-rendered styled screenshots (needs playwright + /opt/google/chrome/chrome)
python scripts/eval_browser_screenshots.py render --families 80 --out eval_reports/browser_dev
python scripts/eval_browser_screenshots.py score  --dir eval_reports/browser_dev --report eval_reports/x.json

# Synthetic tiers (clean / screenshot / photo). Point --db at a DB copy, never production's
python scripts/eval_image_identify.py --ranker engine --split dev --families 125 --db <copy>

# Tuning: collect raw signals once (~10 min with 3 workers), then grid-search offline (seconds)
python scripts/tune_image_ranker.py collect --families 100 --browser eval_reports/browser_dev \
    --out eval_reports/signals.pkl --workers 3 --prefilter 1200      # 0 = render every face
python scripts/tune_image_ranker.py grid --signals eval_reports/signals.pkl            # + calibrate()
python scripts/tune_image_ranker.py refresh-prefilter --signals ... --browser ...      # prefilter code changed
```

- Browser styles: `web`, `tracked_caps`, `logo` (Google colours, −0.05…−0.01 em, tight crop),
  `logo_palette`, `kerned` (AV/To/Ya pairs), `dark`.
- Metrics:
  - `family_hit1` / `family_hit5`: the font itself (flattering, since it's in the corpus).
  - `category1`: LOFO, own family removed. This is the real task, where the font isn't in the
    corpus.
  - `serif_in5`: a serif among a sans query's LOFO top 5.
- `calibrate()` prints precision, share labelled, and how often the label fires out-of-catalog,
  per margin threshold.
- Existing eval sets (gitignored) live in `/opt/projects/font_simil-fix/eval_reports/`:
  `browser_dev` (tuning), `browser_val` (validation), `browser_test` (test split, used once).
- 84 families per style gives about ±5 points of noise on hit@1. The weight grid's top settings
  differ by less than that, so pick the simplest values on the plateau.
- Baseline comparisons: a detached worktree of `master` (`git worktree add --detach <dir> master`)
  with the eval script copied in. Remove it afterwards.
- Latency: time `EvalAdapter.rank` on ~48 browser images, single thread. On 2026-10-03: p95 1.85 s
  (master before the fix: 1.75 s). The gate is p95 < 3 s.

## 4. Traps found (each one cost time)

1. **Default arguments bind at definition time.** `def components(..., prefilter=PREFILTER_FACES)`
   ignored monkeypatching, and a latency comparison silently measured the same setting twice. It
   now uses a `"default"` sentinel resolved at call time. Watch for this with any tunable.
2. **Never write into production's atlas from a worktree.** A symlinked `glyph_atlas` directory
   means `--rows-only` writes into production. In a worktree, make `glyph_atlas/` a real
   directory: `mkdir glyph_atlas && for f in /opt/projects/font_simil/glyph_atlas/*; do ln -s $f glyph_atlas/; done`,
   then build a local `rows.npy`.
3. **Tiny fixture corpora don't share the real corpus's calibration.** On 14 fixture fonts,
   DejaVu Serif is a fair runner-up for a geometric sans, and a margin of 0.165 is honest. Assert
   mechanisms in unit tests (`tests/test_image_styled.py`). Assert thresholds and "no serif in the
   top 5" on the real atlas (`tests/test_image_styled_corpus.py`, integration).
4. **Calibration code must be the service's code.** An ad-hoc margin (with faces outside the HOG
   set) claimed 93% precision at 0.06; the real figure was 88%, and it fired for 28% of
   out-of-catalog fonts. The tuner now imports `rank.family_margins` and `rank.majority_category`,
   selects HOG faces by `Signals.score()`, and crops with `rank._bounded_crop`. Keep it that way.
5. **The vote changes the order but not the keys.** The margin compares the *displayed* winner's
   pre-vote key with the best *other* family's pre-vote key. A face that wins only through the
   vote gets a negative margin and no label (`TestReview3`).
6. **Catalog categories are noisy outside Google Fonts.** Non-Google fonts get a default category
   (Linux Libertine O is "sans"), and Google files some geometric sans under "display" (Funnel
   Display). Read `category1` changes of a few queries as noise. Assert "no serif or handwriting"
   rather than "all sans".
7. **Google Fonts names aren't derivable from family names.** "Playfair Display", "Red Hat Text",
   "Archivo Black" and "DM Sans 9pt" (an opsz instance of "DM Sans") all defeat suffix stripping.
   Read `METADATA.pb` (`helpers.google_fonts_name`, cached; rejects absolute and `..` paths).
8. **Most DB `source` paths lack the license prefix** (`redhattext/RedHatText[wght].ttf`, not
   `ofl/redhattext/...`). Anything that maps sources to repo paths must try `ofl/`, `apache/` and
   `ufl/`.
9. **Production code changes need a restart** (gunicorn `--preload`). A merged commit isn't live
   until `systemctl restart fontmatch`; check with a request, not with `git log`.

## 5. Open follow-ups

- ~~Restart `fontmatch` for `373c7f4`~~ done 2026-10-05.
- **White text in a transparent PNG** is flattened onto white, so no text is found (same as
  before). Fix: flatten onto a contrasting colour when the alpha channel carries the content.
- **Per-word tracking** for justified text and odd OCR word gaps (`Line.words` already exists but
  isn't passed into `Located`).
- **Photos** are the weakest tier (family@1 ≈ 0.46 test). Candidates: a local background estimate
  (large median filter) instead of one border colour; JPEG chroma down-weighting in the ink map.
- **Clean synthetic renders** lost 4/7 points of family@1/@5 (test split) to the spacing prior and
  the vote. That tier is diagnostic only (same renderer as the atlas), but watch it.
- **Kerning** isn't in the atlas. Kerning-heavy words are slightly worse at @5 than before
  (0.80 → 0.77 test, within noise).
- **Google Sans (Product Sans' successor) isn't in the corpus.** If the google/fonts repo now
  carries an open version, adding it would give logos like Google's an exact answer. Check before
  the next corpus rebuild.
