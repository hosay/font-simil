#!/usr/bin/env python3
"""Image matcher accuracy on browser-rendered screenshots.

The synthetic eval (eval_image_identify.py) renders queries with FreeType at
the font's natural spacing, which is what the glyph atlas uses too. Real
uploads are rendered by browsers (Skia, kerning, hinting) and often styled:
letter-spacing, per-letter colours (logos), dark mode, tight crops. This
script renders catalog fonts in headless Chrome in such styles and scores the
engine on them. The Google logo bug (2026-10-03: "Google" -> Lusitana) was a
spacing failure this set is meant to catch.

Usage:
    python scripts/eval_browser_screenshots.py render --families 80 --out eval_reports/browser
    python scripts/eval_browser_screenshots.py score --dir eval_reports/browser [--split dev]

Rendering needs playwright and a Chrome/Chromium binary (--chrome).
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from eval_image_identify import corpus_queries  # noqa: E402

from fontmatch.image.catalog import family_group, load_catalog_json  # noqa: E402

GOOGLE_PALETTE = ["#4285F4", "#EA4335", "#FBBC05", "#4285F4", "#34A853", "#EA4335"]
LOGO_WORDS = ["Google", "Spotify", "Netflix", "Bakery", "Studio", "Coffee", "Harbor", "Pixel"]
PHRASES = [
    "Sunrise Bakery & Co",
    "Fresh Coffee Daily",
    "Welcome to Brooklyn",
    "Mountain Adventures",
    "Kitchen Garden",
    "Harbor View Hotel",
    "Bright Ideas Studio",
    "Vintage Records",
    "Product Roadmap",
    "Join us Saturday",
]

KERN_PHRASES = ["AVATAR Travel", "Toyota WAVE", "Yarrow Valley", "LT Avenue", "Fly Away Tours"]

# name -> how the sample is styled. Every style uses the font's natural
# weight (400 on variable fonts, the Regular instance in the atlas).
STYLES = {
    "web": "dark text on white, 40-56 px, browser kerning, mixed case",
    "tracked_caps": "UPPERCASE heading, letter-spacing +0.12..+0.25 em, coloured background",
    "logo": "one word, per-letter Google colours, letter-spacing -0.05..-0.01 em, tight crop",
    "dark": "light text on a dark background, 28-40 px",
    "logo_palette": "one word, random per-letter colours on a random light/dark page, tight crop",
    "kerned": "kerning-heavy words (AV, To, Ya), browser kerning, dark on light",
}


def _sample(style: str, rng: random.Random) -> dict:
    if style == "web":
        return dict(text=rng.choice(PHRASES), size=rng.randint(40, 56), spacing=0.0,
                    fg=["#202124"], bg="#ffffff", pad=24)  # fmt: skip
    if style == "tracked_caps":
        return dict(text=rng.choice(PHRASES).upper()[:22], size=rng.randint(36, 52),
                    spacing=round(rng.uniform(0.12, 0.25), 3),
                    fg=["#ffffff"], pad=20,
                    bg=rng.choice(["#1a73e8", "#0b3d2e", "#7b1fa2"]))  # fmt: skip
    if style == "logo":
        return dict(text=rng.choice(LOGO_WORDS), size=rng.randint(64, 120),
                    spacing=round(rng.uniform(-0.05, -0.01), 3),
                    fg=GOOGLE_PALETTE, bg="#ffffff", pad=2)  # fmt: skip
    if style == "logo_palette":
        dark_bg = rng.random() < 0.3
        lo, hi = (120, 255) if dark_bg else (0, 200)
        fg = [
            "#%02x%02x%02x" % tuple(rng.randint(lo, hi) for _ in range(3))
            for _ in range(rng.randint(2, 5))
        ]
        bg = "#%02x%02x%02x" % tuple(rng.randint(0, 40) if dark_bg else rng.randint(225, 255)
                                     for _ in range(3))  # fmt: skip
        return dict(text=rng.choice(LOGO_WORDS), size=rng.randint(56, 110),
                    spacing=round(rng.uniform(-0.04, 0.06), 3), fg=fg, bg=bg, pad=3)  # fmt: skip
    if style == "kerned":
        return dict(text=rng.choice(KERN_PHRASES), size=rng.randint(40, 64), spacing=0.0,
                    fg=["#111111"], bg="#fafafa", pad=20)  # fmt: skip
    if style == "dark":
        return dict(text=rng.choice(PHRASES), size=rng.randint(28, 40), spacing=0.0,
                    fg=["#e8eaed"], bg="#202124", pad=16)  # fmt: skip
    raise ValueError(style)


def _html(font_b64: str, s: dict) -> str:
    letters = "".join(
        f'<span style="color:{s["fg"][i % len(s["fg"])]}">{c}</span>' if c != " " else " "
        for i, c in enumerate(s["text"])
    )
    return (
        "<html><head><style>"
        f"@font-face{{font-family:Q;src:url(data:font/ttf;base64,{font_b64})}}"
        "html,body{margin:0;background:" + s["bg"] + "}"
        "#t{display:inline-block;white-space:pre;font-family:Q;font-weight:normal;"
        f"font-size:{s['size']}px;letter-spacing:{s['spacing']}em;padding:{s['pad'] * 3}px;"
        "background:" + s["bg"] + "}"
        f'</style></head><body><div id="t">{letters}</div></body></html>'
    )


def _tight_crop(path: Path, bg: str, pad: int) -> None:
    """Crop a screenshot to its ink box plus ``pad`` px (logos are often
    uploaded cropped right to the letters)."""
    import numpy as np
    from PIL import Image

    img = Image.open(path).convert("RGB")
    arr = np.asarray(img, dtype=np.int16)
    ref = np.array([int(bg[i : i + 2], 16) for i in (1, 3, 5)], dtype=np.int16)
    ink = np.abs(arr - ref).sum(axis=2) > 30
    rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if rows.size == 0:
        return
    box = (
        max(0, cols[0] - pad),
        max(0, rows[0] - pad),
        min(img.width, cols[-1] + 1 + pad),
        min(img.height, rows[-1] + 1 + pad),
    )
    img.crop(box).save(path)


def render(args) -> None:
    from playwright.sync_api import sync_playwright

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog_json(Path(args.catalog))
    queries = [
        q
        for q in corpus_queries(catalog, args.split, args.families, args.seed)
        if q.tier == "clean"
    ]
    manifest = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=args.chrome, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 2400, "height": 600})
        for q in queries:
            font_b64 = base64.b64encode(Path(q.path).read_bytes()).decode()
            for style in args.styles.split(","):
                rng = random.Random(f"{q.base_family}|{style}|{args.seed}")
                s = _sample(style, rng)
                name = f"{q.base_family.replace(' ', '_')}__{style}.png"
                page.set_content(_html(font_b64, s))
                page.evaluate("document.fonts.ready")
                page.locator("#t").screenshot(path=str(out / name))
                _tight_crop(out / name, s["bg"], s["pad"])
                manifest.append(
                    dict(
                        file=name,
                        base_family=q.base_family,
                        group=q.group,
                        category=q.category,
                        font=q.path,
                        style=style,
                        **s,
                    )  # fmt: skip
                )
        browser.close()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"rendered {len(manifest)} samples into {out}")


def score(args) -> None:
    from PIL import Image

    from fontmatch.image.rank import load_default_ranker

    d = Path(args.dir)
    manifest = json.loads((d / args.manifest).read_text())
    if args.styles:
        manifest = [m for m in manifest if m["style"] in args.styles.split(",")]
    if args.limit:
        manifest = manifest[: args.limit]
    catalog = load_catalog_json(Path(args.catalog))
    cat_of = {e.base_family: e.category for e in catalog}
    ranker = load_default_ranker(catalog, use_hint=args.hint == "exact")
    rows = []
    for m in manifest:
        img = Image.open(d / m["file"]).convert("RGB")
        t0 = time.perf_counter()
        ranked = ranker.rank(img, m["text"] if args.hint == "exact" else "", k=10)
        secs = time.perf_counter() - t0
        fams = [f for f, _ in ranked]
        lofo = [f for f in fams if family_group(f) != m["group"]][:5]
        rows.append(
            dict(
                file=m["file"],
                style=m["style"],
                confidence=m.get("confidence"),
                category=m["category"],
                family_hit1=fams[:1] == [m["base_family"]],
                family_hit5=m["base_family"] in fams[:5],
                category1=bool(lofo) and cat_of.get(lofo[0]) == m["category"],
                category_frac5=sum(cat_of.get(f) == m["category"] for f in lofo) / 5,
                no_result=not fams,
                # r/identifythisfont truth outside the catalog: a free substitute in the top 5
                **(
                    {"acceptable_hit5": bool(set(m["acceptable"]) & set(fams[:5]))}
                    if m.get("acceptable")
                    else {}
                ),  # fmt: skip
                # real task (font not in the corpus): a serif among a sans's alternatives
                **(
                    {"serif_in5": any(cat_of.get(f) == "serif" for f in lofo)}
                    if m["category"] == "sans"
                    else {}
                ),  # fmt: skip
                seconds=secs,
                top3=fams[:3],
                lofo5=lofo,
            )
        )
    groups = defaultdict(list)
    for r in rows:
        groups["all"].append(r)
        groups[f"style={r['style']}"].append(r)
        if r.get("confidence"):
            groups[f"confidence={r['confidence']}"].append(r)
    summary = {}
    for g, rs in sorted(groups.items()):
        summary[g] = {"n": len(rs)}
        for k in ("family_hit1", "family_hit5", "category1", "category_frac5", "no_result",
                  "serif_in5", "acceptable_hit5"):  # fmt: skip
            vals = [float(r[k]) for r in rs if k in r]
            if vals:
                summary[g][k] = round(statistics.mean(vals), 3)
        secs = sorted(r["seconds"] for r in rs)
        summary[g]["p95_s"] = round(secs[int(0.95 * (len(secs) - 1))], 2)
        print(g, summary[g])
    if args.report:
        Path(args.report).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", required=True)
    r.add_argument("--families", type=int, default=80)
    r.add_argument("--split", choices=["dev", "test"], default="dev")
    r.add_argument("--seed", type=int, default=7)
    r.add_argument("--styles", default=",".join(STYLES))
    r.add_argument("--chrome", default="/opt/google/chrome/chrome")
    r.add_argument("--catalog", default=str(ROOT / "glyph_atlas" / "catalog.json"))
    s = sub.add_parser("score")
    s.add_argument("--dir", required=True)
    s.add_argument("--manifest", default="manifest.json")
    s.add_argument("--styles", default="")
    s.add_argument("--limit", type=int, default=0)
    s.add_argument("--hint", choices=["exact", "none"], default="exact")
    s.add_argument("--report", default="")
    s.add_argument("--catalog", default=str(ROOT / "glyph_atlas" / "catalog.json"))
    args = ap.parse_args()
    render(args) if args.cmd == "render" else score(args)


if __name__ == "__main__":
    main()
