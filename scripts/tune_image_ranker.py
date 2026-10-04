#!/usr/bin/env python3
"""Tune the image ranker's score weights on DEV queries (never the test split).

Collects each query's raw per-face signals once (fontmatch.image.rank.Signals,
for every candidate face, no prefilter), plus HOG similarity for the top
faces, then evaluates weight combinations offline.

Query sources (both dev only):
    synthetic  eval_image_identify corpus queries: clean / screenshot / photo
    browser    a directory rendered by eval_browser_screenshots.py (Chrome:
               web text, tracked caps, multi-colour logos, dark mode)

Usage:
    python scripts/tune_image_ranker.py collect --families 100 --browser eval_reports/browser_dev \
        --out eval_reports/tune_signals.pkl
    python scripts/tune_image_ranker.py grid --signals eval_reports/tune_signals.pkl
"""

from __future__ import annotations

import argparse
import itertools
import json
import pickle
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fontmatch.image import rank as R  # noqa: E402
from fontmatch.image.catalog import family_group, load_catalog_json  # noqa: E402

HOG_TOP = 150  # faces (by the reference score) that get a HOG similarity


@dataclass
class Item:
    query_id: str
    source: str  # synthetic tier or browser style
    base_family: str
    group: str
    category: str
    faces: np.ndarray | None = None
    families: np.ndarray | None = None
    shape: np.ndarray | None = None
    tracking_em: np.ndarray | None = None
    residual: np.ndarray | None = None
    ink_penalty: np.ndarray | None = None
    hog: np.ndarray | None = None  # NaN where not computed
    profile_dist: np.ndarray | None = None  # prefilter: row-profile L1 distance
    metric_spacing: np.ndarray | None = None  # prefilter: spacing penalty of the metric fit


_MATCHER = None
_PREFILTER = [None]  # collect --prefilter (None = every face)


def args_prefilter():
    return _PREFILTER[0]


def _matcher(catalog_path):
    global _MATCHER
    if _MATCHER is None:
        catalog = load_catalog_json(Path(catalog_path))
        _MATCHER = R.ImageMatcher(R.load_atlas(), {e.name: e.base_family for e in catalog})
    return _MATCHER


def _hog_sims(matcher, soft, text, sig, idx):
    from skimage.feature import hog

    query = matcher._query(soft)
    q = query.normalized()
    width = q.shape[1]
    out = np.full(len(sig.faces), np.nan, dtype=np.float32)
    if width < R.HOG_MIN_WIDTH:
        return out

    def vec(img):
        v = hog(img, orientations=9, pixels_per_cell=(8, 8), cells_per_block=(2, 2))
        return v / (np.linalg.norm(v) or 1.0)

    qh = vec(q)
    for i in idx:
        cand = matcher._typeset(int(sig.faces[i]), text, sig.tracking_px(i))
        cand = cand.at_height(query.soft.shape[0])
        out[i] = float(qh @ vec(cand.normalized(width)))
    return out


def _prefilter_terms(matcher, soft, transcript):
    """The prefilter's two terms per atlas face (no rendering), so its
    weighting can be tuned offline."""
    query = matcher._query(soft)
    metrics = matcher.line_metrics(transcript)
    em, residual = metrics.fit_tracking(query.aspect)
    dist = np.abs(matcher.line_profiles(transcript) - R.query_profile(query.soft)).sum(axis=1)
    return dist, R.spacing_penalty(em, residual)


def _load_query(image_spec, text):
    from PIL import Image

    from fontmatch.image.locate import locate
    from fontmatch.image.prep import ink_map, keep_components_touching

    if isinstance(image_spec, str):
        img = Image.open(image_spec).convert("RGB")
    else:
        from eval_image_identify import make_image

        img = make_image(image_spec)
    located = locate(img, hint=text)
    if located is None:
        return None, None
    crop, ink_box = R._bounded_crop(located)  # exactly as rank_located
    soft = keep_components_touching(ink_map(crop), ink_box)
    return soft, " ".join(located.transcript.split())[: R.MAX_CHARS]


def _refresh_one(job):
    catalog_path, item, image_spec, text = job
    if item.faces is None:
        return item
    matcher = _matcher(catalog_path)
    soft, transcript = _load_query(image_spec, text)
    dist, metric_sp = _prefilter_terms(matcher, soft, transcript)
    pos = {int(f): j for j, f in enumerate(matcher.faces)}
    idx = np.array([pos[int(f)] for f in item.faces])
    item.profile_dist = dist[idx].astype(np.float32)
    item.metric_spacing = metric_sp[idx].astype(np.float32)
    return item


def _collect_one(job):
    catalog_path, item, image_spec, text = job
    matcher = _matcher(catalog_path)
    soft, transcript = _load_query(image_spec, text)
    if soft is None:
        return item
    sig = matcher.components(soft, transcript, prefilter=args_prefilter())
    if sig is None:
        return item
    top = np.argsort(-sig.score())[:HOG_TOP]  # the service's own score picks its top 60
    dist, metric_sp = _prefilter_terms(matcher, soft, transcript)
    face_pos = np.array(
        [{int(f): j for j, f in enumerate(matcher.faces)}[int(f)] for f in sig.faces]
    )
    item.faces = sig.faces
    item.families = np.array([matcher.family_of[matcher.atlas.faces[f]["key"]] for f in sig.faces])
    item.shape = sig.shape.astype(np.float32)
    item.tracking_em = sig.tracking_em
    item.residual = sig.residual
    item.ink_penalty = sig.ink_penalty.astype(np.float32)
    item.hog = _hog_sims(matcher, soft, transcript, sig, top)
    item.profile_dist = dist[face_pos].astype(np.float32)
    item.metric_spacing = metric_sp[face_pos].astype(np.float32)
    return item


def _jobs(args, catalog):
    from eval_image_identify import corpus_queries

    jobs = []
    if args.families:
        for q in corpus_queries(catalog, "dev", args.families, args.seed):
            item = Item(q.query_id, q.tier, q.base_family, q.group, q.category)
            jobs.append((args.catalog, item, q, q.text))
    if args.browser:
        d = Path(args.browser)
        for m in json.loads((d / "manifest.json").read_text()):
            item = Item(m["file"], m["style"], m["base_family"], m["group"], m["category"])
            jobs.append((args.catalog, item, str(d / m["file"]), m["text"]))
    return jobs


def refresh(args):
    """Recompute only the prefilter terms of collected signals (after a
    prefilter code change), without re-rendering every face."""
    from multiprocessing import get_context

    catalog = load_catalog_json(Path(args.catalog))
    data = pickle.loads(Path(args.signals).read_bytes())
    by_id = {(it.source, it.query_id): it for it in data["items"]}
    jobs = [(c, by_id[(it.source, it.query_id)], spec, text)
            for c, it, spec, text in _jobs(args, catalog)]  # fmt: skip
    with get_context("fork").Pool(args.workers) as pool:
        data["items"] = pool.map(_refresh_one, jobs, chunksize=4)
    Path(args.signals).write_bytes(pickle.dumps(data))
    print(f"refreshed {len(jobs)} queries -> {args.signals}")


def collect(args):
    from multiprocessing import get_context

    _PREFILTER[0] = args.prefilter or None
    catalog = load_catalog_json(Path(args.catalog))
    cat_of = {}
    for e in catalog:
        cat_of.setdefault(e.base_family, e.category)
    jobs = _jobs(args, catalog)
    t0 = time.time()
    with get_context("fork").Pool(args.workers) as pool:
        items = []
        for i, it in enumerate(pool.imap_unordered(_collect_one, jobs, chunksize=4), 1):
            items.append(it)
            if i % 50 == 0:
                print(f"  {i}/{len(jobs)} {time.time() - t0:.0f}s", file=sys.stderr)
    Path(args.out).write_bytes(pickle.dumps({"items": items, "cat_of": cat_of}))
    print(f"collected {len(items)} queries in {time.time() - t0:.0f}s -> {args.out}")


@dataclass(frozen=True)
class Weights:
    spacing: float = R.SPACING_WEIGHT
    free_lo: float = R.TRACK_FREE_EM[0]
    free_hi: float = R.TRACK_FREE_EM[1]
    slope_neg: float = R.TRACK_SLOPE[0]
    slope_pos: float = R.TRACK_SLOPE[1]
    residual: float = R.RESIDUAL_WEIGHT
    ink: float = R.INK_WEIGHT
    hog: float = R.HOG_WEIGHT
    prefilter: int = R.PREFILTER_FACES
    prefilter_spacing: float = R.PREFILTER_SPACING_WEIGHT
    vote: float = R.CATEGORY_VOTE


SERIFISH = {"serif"}
_KEEP_CACHE: dict = {}


def _keep_mask(it, prefilter, lam):
    key = (id(it), prefilter, lam)
    if key not in _KEEP_CACHE:
        pf_key = it.profile_dist + lam * it.metric_spacing
        keep = np.zeros(len(pf_key), dtype=bool)
        keep[np.argsort(pf_key, kind="stable")[:prefilter]] = True
        _KEEP_CACHE[key] = keep
    return _KEEP_CACHE[key]


def _category_vote(families, score, key, top, cat_of, weight):
    """As ImageMatcher._off_category: lower the re-ranked faces outside the
    majority category of the top 3 families (by key)."""
    top3: dict[str, str] = {}
    for i in top[np.argsort(-key[top])]:
        top3.setdefault(families[i], cat_of.get(families[i], ""))
        if len(top3) == 3:
            break
    majority = R.majority_category(list(top3.values()))
    out = score.copy()
    for i in top:
        if cat_of.get(families[i], "") != majority:
            out[i] -= weight
    return out


def _leader_margin(families, key, reranked, order, exclude=None):
    """Margin (R.family_margins, as the service) of the top face in ``order``,
    optionally without one family group (a font not in the catalog)."""
    if exclude is not None:
        own = np.array([family_group(f) == exclude for f in families])
        reranked = reranked & ~own
        order = [i for i in order if not own[i]]
    if not len(order):
        return 0.0
    return float(R.family_margins(families, key, reranked)[order[0]])


def _top(score, k):
    """Indices of the k highest scores, best first."""
    k = min(k, len(score))
    part = np.argpartition(-score, k - 1)[:k]
    return part[np.argsort(-score[part], kind="stable")]


def evaluate(items, cat_of, w: Weights) -> dict:
    stats: dict[str, list] = {}

    def add(source, key, val):
        stats.setdefault(source, {}).setdefault(key, []).append(float(val))

    for it in items:
        src = it.source
        if it.faces is None:
            for key in ("fam1", "fam5", "cat1"):
                add(src, key, 0.0)
            continue
        t = it.tracking_em
        pen = (
            np.maximum(0.0, w.free_lo - t) * w.slope_neg
            + np.maximum(0.0, t - w.free_hi) * w.slope_pos
            + w.residual * it.residual
        )
        score = it.shape - w.spacing * pen - w.ink * it.ink_penalty
        # metric_spacing was collected with the code's free band/slopes; the
        # tuned band only changes the rendered-stage penalty below.
        keep = _keep_mask(it, w.prefilter, w.prefilter_spacing)
        score = np.where(keep, score, -np.inf)
        top = _top(score, R.RERANK_TOP)
        key = score.copy()  # rank_mask's margin key: score + HOG on the re-ranked faces
        if w.hog:
            key[top] += w.hog * np.nan_to_num(it.hog[top], nan=0.0)
            score = key.copy()
            score[top] += 100.0
        if w.vote:
            score = _category_vote(it.families, score, key, top, cat_of, w.vote)
        order = _top(score, 300)
        ranked = list(dict.fromkeys(it.families[order]))
        reranked = np.zeros(len(key), dtype=bool)
        reranked[top] = True
        add(src, "_margin", _leader_margin(it.families, key, reranked, order))
        add(src, "_lofo_margin", _leader_margin(it.families, key, reranked, order, it.group))
        add(src, "fam1", ranked[:1] == [it.base_family])
        add(src, "fam5", it.base_family in ranked[:5])
        lofo = [f for f in ranked if family_group(f) != it.group][:5]
        add(src, "cat1", bool(lofo) and cat_of.get(lofo[0]) == it.category)
        if it.category == "sans":
            add(src, "serif_in5", any(cat_of.get(f) in SERIFISH for f in lofo))
        add(src, "prefilter_recall", bool(keep[it.families == it.base_family].any()))
    out = {}
    for src, d in stats.items():
        out[src] = {k: float(np.mean(v)) for k, v in d.items() if not k.startswith("_")}
        out[src]["n"] = len(d["fam1"])
        hits = [h for h, m in zip(d["fam1"], d.get("_margin", []))]
        out[src]["_pairs"] = list(zip(d.get("_margin", []), hits))
        out[src]["_lofo"] = d.get("_lofo_margin", [])
    return out


def calibrate(res: dict) -> None:
    """The "likely the same font" label (top-1 margin >= threshold) on the
    realistic sources: precision when the font is in the catalog, and how
    often it would (wrongly) fire when it isn't (own family removed)."""
    pairs = [p for s in REALISTIC if s in res for p in res[s]["_pairs"]]
    lofo = np.array([v for s in REALISTIC if s in res for v in res[s]["_lofo"]])
    if not pairs:
        return
    margin = np.array([p[0] for p in pairs])
    hit = np.array([p[1] for p in pairs], dtype=bool)
    print("\nmargin threshold | precision | share labelled | fires when font not in catalog")
    for t in (0.05, 0.1, 0.15, 0.2, 0.25, 0.3):
        sel = margin >= t
        if sel.any():
            print(
                f"  {t:.2f} | {hit[sel].mean():.3f} | {sel.mean():.2f} | {(lofo >= t).mean():.3f}"
            )


SYNTH = ("clean", "screenshot", "photo")
REALISTIC = ("screenshot", "photo", "web", "tracked_caps", "logo", "dark")


def objective(res: dict) -> float:
    srcs = [s for s in REALISTIC if s in res]
    fam1 = np.mean([res[s]["fam1"] for s in srcs])
    cat1 = np.mean([res[s]["cat1"] for s in srcs])
    serif = np.mean([res[s].get("serif_in5", 0.0) for s in srcs])
    return float(0.45 * fam1 + 0.45 * cat1 + 0.1 * (1 - serif))


def _fmt(res, w, obj):
    cols = " | ".join(
        f"{res[s]['fam1']:.2f}/{res[s]['cat1']:.2f}" for s in SYNTH + REALISTIC[2:] if s in res
    )
    return (
        f"| {obj:.3f} | {w.spacing} | {w.free_lo},{w.free_hi} | {w.slope_neg},{w.slope_pos} | "
        f"{w.residual} | {w.ink} | {w.hog}+v{w.vote} | {w.prefilter} | {cols} |"
    )


def grid(args):
    data = pickle.loads(Path(args.signals).read_bytes())
    items, cat_of = data["items"], data["cat_of"]
    base = Weights(prefilter=args.prefilter, prefilter_spacing=args.prefilter_spacing)
    results = [(objective(r := evaluate(items, cat_of, base)), base, r)]
    if not args.only_current:
        # Stage 1: spacing prior (ink/HOG at their current values).
        for sp, band, slopes, res_w in itertools.product(
            [0.0, 0.25, 0.5, 1.0, 2.0],
            [(-0.03, 0.0), (-0.06, 0.03), (-0.1, 0.05), (-0.06, 0.1), (-0.1, 0.2)],
            [(1.0, 1.0), (1.0, 0.5), (2.0, 1.0), (1.0, 0.25), (0.5, 0.25)],
            [0.25, 1.0, 3.0],
        ):
            w = Weights(**{**base.__dict__, "spacing": sp, "free_lo": band[0],
                           "free_hi": band[1], "slope_neg": slopes[0],
                           "slope_pos": slopes[1], "residual": res_w})  # fmt: skip
            results.append((objective(r := evaluate(items, cat_of, w)), w, r))
        best1 = max(results, key=lambda t: t[0])[1]
        # Stage 2: ink, HOG and category-vote weights.
        for ink, hog, vote in itertools.product(
            [0.0, 0.2], [1.0, 2.0, 3.0], [0.0, 0.05, 0.1, 0.2]
        ):
            w = Weights(**{**best1.__dict__, "ink": ink, "hog": hog, "vote": vote})
            results.append((objective(r := evaluate(items, cat_of, w)), w, r))
    cur = results[0]
    results.sort(key=lambda r: -r[0])
    srcs = [s for s in SYNTH + REALISTIC[2:] if s in cur[2]]
    print(
        "| objective | spacing_w | free band | slopes | residual_w | ink_w | hog_w | prefilter | "
        + " | ".join(f"{s} fam1/cat1" for s in srcs)
        + " |"
    )
    print("|" + "---|" * (8 + len(srcs)))
    print(_fmt(cur[2], cur[1], cur[0]) + " (current)")
    for obj, w, res in results[: args.top]:
        print(_fmt(res, w, obj))
    best = results[0]
    print("\nbest:", best[1])
    for s, r in sorted(best[2].items()):
        print(
            f"  {s:<13} "
            + " ".join(f"{k}={v:.3f}" for k, v in sorted(r.items()) if not k.startswith("_"))
        )
    calibrate(best[2])
    # prefilter sweep at the best weights
    for pf, lam in itertools.product((600, 1200, 2000, 10**6), (0.0, 0.1, 0.3, 1.0)):
        w = Weights(**{**best[1].__dict__, "prefilter": pf, "prefilter_spacing": lam})
        res = evaluate(items, cat_of, w)
        rec = {s: round(r["prefilter_recall"], 3) for s, r in res.items()}
        print(f"prefilter {pf} spacing {lam}: objective {objective(res):.3f} recall {rec}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--families", type=int, default=100)
    c.add_argument("--seed", type=int, default=1234)
    c.add_argument("--browser", default="")
    c.add_argument("--out", required=True)
    c.add_argument("--workers", type=int, default=3)
    c.add_argument("--prefilter", type=int, default=0, help="faces rendered per query (0 = all)")
    c.add_argument("--catalog", default=str(ROOT / "glyph_atlas" / "catalog.json"))
    f = sub.add_parser("refresh-prefilter")
    f.add_argument("--signals", required=True)
    f.add_argument("--families", type=int, default=100)
    f.add_argument("--seed", type=int, default=1234)
    f.add_argument("--browser", default="")
    f.add_argument("--workers", type=int, default=3)
    f.add_argument("--catalog", default=str(ROOT / "glyph_atlas" / "catalog.json"))
    g = sub.add_parser("grid")
    g.add_argument("--signals", required=True)
    g.add_argument("--top", type=int, default=15)
    g.add_argument("--only-current", action="store_true")
    g.add_argument("--prefilter", type=int, default=R.PREFILTER_FACES)
    g.add_argument("--prefilter-spacing", type=float, default=R.PREFILTER_SPACING_WEIGHT)
    args = ap.parse_args()
    {"collect": collect, "refresh-prefilter": refresh, "grid": grid}[args.cmd](args)


if __name__ == "__main__":
    main()
