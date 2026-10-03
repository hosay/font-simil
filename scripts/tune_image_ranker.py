#!/usr/bin/env python3
"""Grid-search the image ranker's score weights on the eval DEV split.

Runs the full pipeline once per query, caches each face's raw signals
(shape, aspect penalty, ink penalty) and then evaluates every weight
combination offline. Never run this on the test split.

Usage:
    python scripts/tune_image_ranker.py --families 100
"""

from __future__ import annotations

import argparse
import itertools
import pickle
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_image_identify import corpus_queries, make_image  # noqa: E402

from fontmatch.image.catalog import build_catalog, family_group  # noqa: E402
from fontmatch.image.locate import locate  # noqa: E402
from fontmatch.image.paths import SEARCH_DIRS  # noqa: E402
from fontmatch.image.prep import ink_map  # noqa: E402
from fontmatch.image.rank import ImageMatcher, load_atlas  # noqa: E402

ASPECT_GRID = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0, 3.0]
INK_GRID = [0.0, 0.15, 0.3, 0.6, 1.0, 1.5]
EXTRA_GRID = [0.0, 0.25, 0.5, 1.0, 2.0]
RERANK_TOP = 60


def _extra_signals(matcher, located, faces, base_score, with_clip):
    """HOG and (optionally) CLIP similarity for the top faces by base score.
    Returns (face_ids, hog_sim, clip_sim)."""
    from skimage.feature import hog

    from fontmatch.features.perceptual import perceptual
    from fontmatch.image.prep import deskew_soft
    from fontmatch.image.rank import line_features

    query = line_features(deskew_soft(ink_map(located.crop)))
    top = faces[np.argsort(-base_score)[:RERANK_TOP]]
    q32 = query.normalized()
    width = q32.shape[1]

    def hog_vec(img):
        v = hog(
            img,
            orientations=9,
            pixels_per_cell=(8, 8),
            cells_per_block=(2, 2),
            feature_vector=True,
        )
        return v / (np.linalg.norm(v) or 1.0)

    def clip_vec(feat):
        # One square image per line: 64 px tall, padded to a square
        # (perceptual() would tile a wide line into many tiny squares).
        from PIL import Image

        from fontmatch.features.perceptual import pad_to_square

        width = max(1, int(round(feat.aspect * 64)))
        line = Image.fromarray((np.clip(feat.soft, 0, 1) * 255).astype(np.uint8)).resize(
            (width, 64), Image.Resampling.BILINEAR
        )
        return perceptual(pad_to_square(np.asarray(line)))

    qh = hog_vec(q32)
    qc = clip_vec(query) if with_clip else None
    hogs, clips = [], []
    for face in top:
        composed, _ = matcher.atlas.compose(int(face), " ".join(located.transcript.split())[:40])
        cand = line_features(composed.astype(np.float32) / 255.0).at_height(query.soft.shape[0])
        hogs.append(float(qh @ hog_vec(cand.normalized(width))))
        clips.append(float(qc @ clip_vec(cand)) if with_clip else 0.0)
    return top, np.array(hogs, dtype=np.float32), np.array(clips, dtype=np.float32)


def collect(args):
    catalog = build_catalog(Path(args.db), SEARCH_DIRS, args.catalog_schema)
    cat_of = {}
    for e in catalog:
        cat_of.setdefault(e.base_family, e.category)
    matcher = ImageMatcher(load_atlas(), {e.name: e.base_family for e in catalog})
    queries = corpus_queries(catalog, "dev", args.families, args.seed)
    rows = []
    for i, q in enumerate(queries, 1):
        img = make_image(q)
        located = locate(img, hint=q.text)
        if located is None:
            rows.append((q, None))
            continue
        comp = matcher.components(ink_map(located.crop), located.transcript)
        if comp is None:
            rows.append((q, None))
            continue
        faces, shape, ap, ip = comp
        fams = np.array([matcher.family_of[matcher.atlas.faces[f]["key"]] for f in faces])
        extra = None
        if args.extras:
            top, hogs, clips = _extra_signals(
                matcher, located, faces, shape - 0.25 * ap, args.clip
            )
            pos = {int(f): j for j, f in enumerate(faces)}
            hog_full = np.zeros(len(faces), dtype=np.float32)
            clip_full = np.zeros(len(faces), dtype=np.float32)
            in_top = np.zeros(len(faces), dtype=bool)
            for f, h, c in zip(top, hogs, clips):
                hog_full[pos[int(f)]] = h
                clip_full[pos[int(f)]] = c
                in_top[pos[int(f)]] = True
            extra = (hog_full, clip_full, in_top)
        rows.append(
            (
                q,
                (
                    fams,
                    shape.astype(np.float32),
                    ap.astype(np.float32),
                    ip.astype(np.float32),
                    extra,
                ),
            )
        )
        if i % 25 == 0:
            print(f"  {i}/{len(queries)}", file=sys.stderr)
    return rows, cat_of


def evaluate(rows, cat_of, a, b, h=0.0, c=0.0):  # noqa: PLR0913
    hits, cats, n = 0, 0, 0
    by_tier = {}
    for q, comp in rows:
        n += 1
        tier = by_tier.setdefault(q.tier, [0, 0, 0])
        tier[2] += 1
        if comp is None:
            continue
        fams, shape, ap, ip, extra = comp
        score = shape - a * ap - b * ip
        if extra is not None and (h or c):
            hog_s, clip_s, in_top = extra
            # Re-rank only the top candidates; everything else stays below them.
            boosted = score + h * hog_s + c * clip_s
            score = np.where(in_top, boosted + 10.0, score)
        order = np.argsort(-score)
        ranked = list(dict.fromkeys(fams[order[:200]]))
        if ranked[:1] == [q.base_family]:
            hits += 1
            tier[0] += 1
        group = getattr(q, "group", None) or family_group(q.base_family)  # old pickles
        lofo = [f for f in ranked if family_group(f) != group]
        if lofo and cat_of.get(lofo[0]) == q.category:
            cats += 1
            tier[1] += 1
    return hits / n, cats / n, {t: (v[0] / v[2], v[1] / v[2]) for t, v in by_tier.items()}


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--families", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--db", default=str(ROOT / "fontmatch.db"))
    ap.add_argument("--catalog-schema", type=int, default=None)
    ap.add_argument("--cache", default=None, help="pickle file to reuse collected signals")
    ap.add_argument(
        "--extras", action="store_true", help="also collect HOG re-rank signals for the top faces"
    )
    ap.add_argument(
        "--clip", action="store_true", help="with --extras: also CLIP re-rank signals (slow)"
    )
    args = ap.parse_args()

    if args.cache and Path(args.cache).exists():
        rows, cat_of = pickle.loads(Path(args.cache).read_bytes())
    else:
        t0 = time.time()
        rows, cat_of = collect(args)
        print(f"collected {len(rows)} queries in {time.time() - t0:.0f}s", file=sys.stderr)
        if args.cache:
            Path(args.cache).write_bytes(pickle.dumps((rows, cat_of)))

    has_extra = any(comp is not None and comp[4] is not None for _, comp in rows)
    extra_grid = EXTRA_GRID if has_extra else [0.0]
    results = []
    for a, b, h, c in itertools.product(ASPECT_GRID, INK_GRID, extra_grid, extra_grid):
        fam1, cat1, tiers = evaluate(rows, cat_of, a, b, h, c)
        # Objective: real-task proxy (category@1, LOFO) + identification on the
        # realistic tiers; the clean tier is excluded on purpose.
        realistic = np.mean([tiers[t][0] for t in ("screenshot", "photo") if t in tiers])
        objective = 0.5 * cat1 + 0.5 * realistic
        results.append((objective, a, b, h, c, fam1, cat1, tiers))
    results.sort(key=lambda r: -r[0])
    print(
        "| objective | aspect_w | ink_w | hog_w | clip_w | family@1 | category@1 "
        "| screenshot fam@1 | photo fam@1 |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    best_without = max((r for r in results if r[3] == 0 and r[4] == 0), key=lambda r: r[0])
    best_hog = max((r for r in results if r[4] == 0), key=lambda r: r[0])
    for objective, a, b, h, c, fam1, cat1, tiers in results[:8] + [best_without, best_hog]:
        print(
            f"| {objective:.3f} | {a} | {b} | {h} | {c} | {fam1:.3f} | {cat1:.3f} | "
            f"{tiers.get('screenshot', (0, 0))[0]:.3f} | {tiers.get('photo', (0, 0))[0]:.3f} |"
        )


if __name__ == "__main__":
    main()
