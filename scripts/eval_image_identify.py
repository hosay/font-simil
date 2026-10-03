#!/usr/bin/env python3
"""Accuracy benchmark for image -> free font matching.

See docs/image-matching.md ("Validation methodology") for what each metric
means and which ones gate a release.

Usage:
    python scripts/eval_image_identify.py --ranker b0 --split dev --families 120
    python scripts/eval_image_identify.py --ranker engine --split test   # once per milestone
    python scripts/eval_image_identify.py --ranker engine --set external

Query sets
    corpus    Fonts from the candidate catalog, one per base family, split by
              a hash of the base family (dev 80% / test 20%) so weights and
              italics of one family never straddle the split.
    external  Fonts NOT in the catalog (system fonts, test fixtures): the real
              task. Only category metrics apply.

Every corpus query is scored two ways from one ranking:
    self      own family allowed  -> family_hit@1/@5  (diagnostic; flattering)
    lofo      own family excluded -> category@1, category_frac@5, clone_hit@5,
              file_overlap@5 (diagnostic agreement with the font-file matcher)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
import string
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fontmatch.image.catalog import (  # noqa: E402
    CatalogEntry,
    base_family,
    build_catalog,
    family_group,
)
from fontmatch.image.paths import SEARCH_DIRS  # noqa: E402
from fontmatch.image.synth import TIERS, degrade, render_text_image  # noqa: E402

PHRASES = [
    "Sunrise Bakery & Co",
    "Quarterly Report 2026",
    "The quick brown fox jumps",
    "Fresh Coffee Daily",
    "Welcome to Brooklyn",
    "Mountain Adventures",
    "Grand Opening Sale",
    "Kitchen Garden",
    "Modern Living",
    "Jazz Festival",
    "Organic Market",
    "Wildflower Honey",
    "Harbor View Hotel",
    "Midnight Express",
    "Golden Years",
    "Lucky Penny Diner",
    "Bright Ideas Studio",
    "Ocean Breeze",
    "Vintage Records",
    "Hamburgefonstiv",
    "Join us Saturday",
    "Product Roadmap",
    "Big Sky Brewing",
    "Hello World",
]

# Metric-compatible clone pairs: when one family is excluded, the other
# should be among the top 5.
CLONE_PAIRS = [
    ("arimo", "liberation sans"),
    ("tinos", "liberation serif"),
    ("cousine", "liberation mono"),
]

EXTERNAL_DIRS = [ROOT / "tests" / "fixtures", Path("/usr/share/fonts")]


@dataclass
class Query:
    query_id: str
    path: str
    base_family: str
    group: str  # family_group: siblings ("X SC", "X Display") count as the same family
    category: str
    tier: str
    text: str
    size_px: int
    seed: int


def split_of(base: str) -> str:
    h = int(hashlib.sha256(base.encode()).hexdigest(), 16) % 5
    return "test" if h == 0 else "dev"


def _representative(entries: list[CatalogEntry]) -> CatalogEntry:
    """Prefer an upright Regular style for a family."""

    def rank(e: CatalogEntry):
        sub = e.subfamily.lower()
        return (e.is_italic, sub not in ("regular", ""), "[" in e.name, e.name)

    return sorted(entries, key=rank)[0]


def covers_latin(path) -> bool:
    from fontTools.ttLib import TTFont

    try:
        with TTFont(str(path), lazy=True) as tt:
            cmap = tt.getBestCmap() or {}
    except Exception:
        return False
    return all(ord(c) in cmap for c in string.ascii_letters + "&")


def corpus_queries(catalog, split, n_families, seed) -> list[Query]:
    by_family: dict[str, list[CatalogEntry]] = defaultdict(list)
    for e in catalog:
        by_family[e.base_family].append(e)
    families = sorted(
        f
        for f in by_family
        if split_of(f) == split and covers_latin(_representative(by_family[f]).path)
    )

    # Stratify by category so display/handwriting aren't drowned out.
    by_cat: dict[str, list[str]] = defaultdict(list)
    for f in families:
        by_cat[_representative(by_family[f]).category].append(f)
    rng = random.Random(seed)
    chosen: list[str] = []
    per_cat = max(1, n_families // max(1, len(by_cat)))
    for cat in sorted(by_cat):
        pool = by_cat[cat][:]
        rng.shuffle(pool)
        chosen.extend(pool[:per_cat])
    # Always include clone-pair families present in this split.
    for a, b in CLONE_PAIRS:
        for fam in (a, b):
            if fam in by_family and split_of(family_group(fam)) == split and fam not in chosen:
                chosen.append(fam)

    queries = []
    for fam in sorted(chosen):
        entry = _representative(by_family[fam])
        for tier in TIERS:
            qseed = rng.randint(0, 2**31)
            qrng = random.Random(qseed)
            size = 48 if tier == "clean" else qrng.randint(24, 64)
            queries.append(
                Query(
                    query_id=f"{fam}|{tier}",
                    path=str(entry.path),
                    base_family=fam,
                    group=family_group(fam),
                    category=entry.category,
                    tier=tier,
                    text=qrng.choice(PHRASES),
                    size_px=size,
                    seed=qseed,
                )
            )
    return queries


EXTERNAL_LABELS = Path(__file__).with_name("eval_external_labels.json")


def external_labels() -> dict:
    data = json.loads(EXTERNAL_LABELS.read_text()) if EXTERNAL_LABELS.exists() else {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def external_queries(catalog, seed, limit) -> list[Query]:
    from fontmatch.features.metrics import metrics as extract_metrics
    from fontmatch.fonts import load

    known = {e.base_family for e in catalog}
    rng = random.Random(seed)
    seen = set()
    paths = []
    for root in EXTERNAL_DIRS:
        if root.is_dir():
            paths.extend(
                sorted(p for p in root.rglob("*") if p.suffix.lower() in (".ttf", ".otf"))
            )
    queries = []
    for path in paths:
        try:
            font = load(path)
        except Exception:
            continue
        fam = base_family(font.family)
        if fam in known or fam in seen or "italic" in font.subfamily.lower():
            continue
        cmap = font.tt.getBestCmap() or {}
        if not all(ord(c) in cmap for c in "aeorsAB"):
            continue  # symbol / non-Latin fonts
        label = external_labels().get(fam, {})
        if label.get("exclude"):
            continue
        seen.add(fam)
        # Hand label when available; otherwise our own classifier (circular:
        # diagnostic only, see docs/image-matching.md).
        serif_class = label.get("category") or extract_metrics(font).serif_class
        for tier in TIERS:
            qseed = rng.randint(0, 2**31)
            qrng = random.Random(qseed)
            queries.append(
                Query(
                    query_id=f"{fam}|{tier}",
                    path=str(path),
                    base_family=fam,
                    group=family_group(fam),
                    category=serif_class,
                    tier=tier,
                    text=qrng.choice(PHRASES),
                    size_px=48 if tier == "clean" else qrng.randint(24, 64),
                    seed=qseed,
                )
            )
        if len(seen) >= limit:
            break
    return queries


def make_image(q: Query):
    img = render_text_image(Path(q.path), q.text, size_px=q.size_px)
    return degrade(img, q.tier, random.Random(q.seed))


def file_matcher_top5(store, path: str, cache: dict) -> list[str]:
    """The existing font-file matcher's top 5 base families (diagnostic)."""
    if path not in cache:
        from fontmatch.features.fingerprint import fingerprint
        from fontmatch.fonts import load

        results = store.identify(fingerprint(load(path)), k=5)
        cache[path] = [base_family(r["family"]) for r in results]
    return cache[path]


def coarse(cat: str) -> str:
    """External queries only know sans/serif/mono; map GF categories down."""
    return cat if cat in ("sans", "serif", "mono") else "other"


def score_query(
    q: Query, ranked: list[tuple[str, float]], cat_of: dict[str, str], external: bool, file_top5
):
    fams = [f for f, _ in ranked]
    lofo = [f for f in fams if family_group(f) != q.group][:5]
    row = {"query": asdict(q)}
    if not external:
        row["family_hit@1"] = fams[:1] == [q.base_family]
        row["family_hit@5"] = q.base_family in fams[:5]
        same = [cat_of.get(f) == q.category for f in lofo]
    else:
        # Hand labels use the full category set; classifier labels only know
        # sans/serif/mono, so compare coarsely.
        same = [cat_of.get(f) == q.category for f in lofo]
        if q.category not in ("display", "handwriting"):
            same = [coarse(cat_of.get(f, "")) == coarse(q.category) for f in lofo]
    row["category@1"] = bool(same[:1] and same[0])
    row["category_frac@5"] = sum(same) / max(1, len(same))
    for a, b in CLONE_PAIRS:
        for x, y in ((a, b), (b, a)):
            if q.base_family == x:
                row["clone_hit@5"] = y in lofo
    if file_top5 is not None:
        row["file_overlap@5"] = len(set(lofo) & set(file_top5)) / 5
    if external:
        label = external_labels().get(q.base_family, {})
        if label.get("acceptable"):
            row["acceptable_hit@5"] = bool(set(lofo) & set(label["acceptable"]))
        row["hand_labelled"] = bool(label)
    row["top5"] = lofo
    return row


def aggregate(rows: list[dict]) -> dict:
    metrics = [
        "family_hit@1",
        "family_hit@5",
        "category@1",
        "category_frac@5",
        "clone_hit@5",
        "acceptable_hit@5",
        "file_overlap@5",
        "seconds",
        "no_result",
        "transcript_exact",
    ]
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        groups[f"tier={r['query']['tier']}"].append(r)
        groups[f"cat={r['query']['category']}"].append(r)
    out = {}
    for name, rs in sorted(groups.items()):
        agg = {"n": len(rs)}
        for m in metrics:
            vals = [float(r[m]) for r in rs if m in r]
            if vals:
                agg[m] = round(statistics.mean(vals), 3)
        secs = sorted(r["seconds"] for r in rs)
        agg["p95_seconds"] = round(secs[int(0.95 * (len(secs) - 1))], 2)
        out[name] = agg
    return out


class FileMatcherRanker:
    """Reference, not an image ranker: the existing matcher run on the query's
    font FILE. Upper-bound-ish comparison and a way to measure changes to the
    file matcher itself (e.g. fingerprint schema bumps)."""

    needs_path = True

    def __init__(self, db: Path):
        from fontmatch.index.store import FontStore

        self.store = FontStore(db)
        self.store.build_index()
        self._cache = {}  # the image tier doesn't matter: same file, same answer

    def rank(self, img, text="", k=10, path=None):
        from fontmatch.features.fingerprint import fingerprint
        from fontmatch.fonts import load

        if path not in self._cache:
            results = self.store.identify(fingerprint(load(path)), k=k)
            self._cache[path] = [
                (base_family(r["family"]), -float(r["distance"])) for r in results
            ]
        return self._cache[path]


def bootstrap_ci(rows: list[dict], metric: str, n: int = 1000, seed: int = 0):
    """95% CI of a metric's mean, resampling whole families (the tiers of one
    family are not independent)."""
    by_family: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if metric in r:
            by_family[r["query"]["base_family"]].append(float(r[metric]))
    fams = list(by_family)
    if len(fams) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        vals = [v for f in (rng.choice(fams) for _ in fams) for v in by_family[f]]
        means.append(statistics.mean(vals))
    means.sort()
    return round(means[int(0.025 * n)], 3), round(means[int(0.975 * n) - 1], 3)


def get_ranker(name: str, db: Path, catalog, hint: str = "exact"):
    if name == "file":
        return FileMatcherRanker(db)
    if name == "b0":
        from fontmatch.image.baseline import ClipSheetRanker

        return ClipSheetRanker(db, catalog)
    if name == "engine":
        from fontmatch.image.rank import load_default_ranker

        return load_default_ranker(catalog=catalog, use_hint=(hint == "exact"))
    raise SystemExit(f"unknown ranker {name}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ranker", default="b0")
    ap.add_argument("--set", choices=["corpus", "external"], default="corpus")
    ap.add_argument("--split", choices=["dev", "test"], default="dev")
    ap.add_argument("--families", type=int, default=100)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument(
        "--hint",
        choices=["exact", "none"],
        default="exact",
        help="engine: pass the true text as text_hint, or OCR only",
    )
    ap.add_argument("--db", default=str(ROOT / "fontmatch.db"))
    ap.add_argument(
        "--catalog-schema",
        type=int,
        default=None,
        help="fingerprint schema defining the catalog (default: current)",
    )
    ap.add_argument(
        "--no-file-matcher", action="store_true", help="skip the file-matcher overlap diagnostic"
    )
    ap.add_argument("--out", default=str(ROOT / "eval_reports"))
    args = ap.parse_args()

    db = Path(args.db)
    catalog = build_catalog(db, SEARCH_DIRS, args.catalog_schema)
    cat_of = {}
    for e in catalog:
        cat_of.setdefault(e.base_family, e.category)
    print(f"catalog: {len(catalog)} fonts / {len(cat_of)} families", file=sys.stderr)

    external = args.set == "external"
    queries = (
        external_queries(catalog, args.seed, args.families)
        if external
        else corpus_queries(catalog, args.split, args.families, args.seed)
    )
    print(f"queries: {len(queries)}", file=sys.stderr)

    ranker = get_ranker(args.ranker, db, catalog, args.hint)
    store = None
    if not args.no_file_matcher:
        from fontmatch.index.store import FontStore

        store = FontStore(db)
        store.build_index()
    file_cache: dict = {}

    rows = []
    for i, q in enumerate(queries, 1):
        img = make_image(q)
        t0 = time.perf_counter()
        if getattr(ranker, "needs_path", False):
            ranked = ranker.rank(img, text=q.text, k=10, path=q.path)
        else:
            ranked = ranker.rank(img, text=q.text, k=10)
        elapsed = time.perf_counter() - t0
        file_top5 = file_matcher_top5(store, q.path, file_cache) if store else None
        row = score_query(q, ranked, cat_of, external, file_top5)
        row["seconds"] = elapsed
        row["no_result"] = not ranked
        transcript = getattr(ranker, "last_transcript", None)
        if transcript is not None or args.ranker == "engine":
            row["transcript"] = transcript
            row["transcript_exact"] = transcript == q.text
        rows.append(row)
        if i % 25 == 0:
            print(f"  {i}/{len(queries)}", file=sys.stderr)

    summary = aggregate(rows)
    ci_metrics = ("family_hit@1", "family_hit@5", "category@1", "acceptable_hit@5")
    summary["all"]["ci95"] = {
        m: bootstrap_ci(rows, m) for m in ci_metrics if bootstrap_ci(rows, m)
    }
    report = {
        "ranker": args.ranker,
        "set": args.set,
        "split": None if external else args.split,
        "seed": args.seed,
        "catalog_fonts": len(catalog),
        "summary": summary,
        "rows": rows,
    }
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = args.set if external else args.split
    if args.ranker == "engine":
        label += f"-hint_{args.hint}"
    out_path = out_dir / f"{stamp}-{args.ranker}-{label}.json"
    out_path.write_text(json.dumps(report, indent=1, default=str))

    cols = [
        "n",
        "family_hit@1",
        "family_hit@5",
        "category@1",
        "category_frac@5",
        "clone_hit@5",
        "acceptable_hit@5",
        "file_overlap@5",
        "transcript_exact",
        "no_result",
        "p95_seconds",
    ]
    print("| group | " + " | ".join(cols) + " |")
    print("|" + "---|" * (len(cols) + 1))
    for name, agg in summary.items():
        print(f"| {name} | " + " | ".join(str(agg.get(c, "")) for c in cols) + " |")
    print("95% CI (family-clustered bootstrap): " + json.dumps(summary["all"].get("ci95", {})))
    print(f"\nreport: {out_path}")


if __name__ == "__main__":
    main()
