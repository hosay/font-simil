#!/usr/bin/env python3
"""L4 performance check for POST /api/identify-image (in-process Flask client).

Measures cold start, sequential p50/p95, p95 under N concurrent requests, and
process RSS. Cache is bypassed by giving every request a unique image.

Usage:
    OMP_NUM_THREADS=1 python scripts/perf_image_identify.py --requests 30 --concurrency 4
"""

from __future__ import annotations

import argparse
import io
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psutil  # noqa: E402

from fontmatch.image.synth import degrade, render_text_image  # noqa: E402

FONTS = sorted((ROOT / "google-fonts-repo" / "ofl").glob("*/*-Regular.ttf"))
PHRASES = ["Sunrise Bakery & Co", "Harbor View Hotel", "Quarterly Report 2026", "Wildflower Honey"]


def make_png(i: int) -> tuple[bytes, str]:
    rng = random.Random(i)
    text = rng.choice(PHRASES) + f" {i}"
    while True:  # some repo fonts are bitmap/colour-only and can't be scaled
        try:
            img = render_text_image(rng.choice(FONTS), text, size_px=rng.randint(28, 56))
            break
        except OSError:
            continue
    img = degrade(img, "screenshot", rng)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), text


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * (len(values) - 1) + 0.5))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--db", default=str(ROOT / "fontmatch.db"))
    ap.add_argument("--seed", type=int, default=10_000, help="change to avoid result-cache hits")
    args = ap.parse_args()

    from fontmatch.service.app import create_app

    proc = psutil.Process()
    rss0 = proc.memory_info().rss / 1e6
    # Unique images per request, so the result cache never hits. Results are
    # cached into --db: point it at a dev copy, never the production DB.
    app = create_app(db_path=Path(args.db))
    client = app.test_client()

    def call(i):
        data, text = make_png(args.seed + i)
        t0 = time.perf_counter()
        resp = client.post(
            "/api/identify-image",
            data={"image": (io.BytesIO(data), "q.png"), "text_hint": text},
            content_type="multipart/form-data",
            headers={"X-Forwarded-For": f"10.0.{i // 250}.{i % 250}"},
        )
        return time.perf_counter() - t0, resp.status_code

    t0 = time.perf_counter()
    cold, status = call(0)
    total = time.perf_counter() - t0
    print(f"cold first request: {cold:.2f}s (status {status}), total {total:.2f}s")
    rss_loaded = proc.memory_info().rss / 1e6

    seq = [call(i) for i in range(1, args.requests + 1)]
    seq_t = [t for t, _ in seq]
    print(
        f"sequential n={len(seq_t)}: p50={statistics.median(seq_t):.2f}s "
        f"p95={pct(seq_t, 0.95):.2f}s statuses={sorted({s for _, s in seq})}"
    )

    with ThreadPoolExecutor(args.concurrency) as pool:
        start = time.perf_counter()
        conc = list(pool.map(call, range(1000, 1000 + args.requests)))
        wall = time.perf_counter() - start
    conc_t = [t for t, _ in conc]
    print(
        f"concurrent x{args.concurrency} n={len(conc_t)}: p50={statistics.median(conc_t):.2f}s "
        f"p95={pct(conc_t, 0.95):.2f}s throughput={len(conc_t) / wall:.2f} req/s "
        f"statuses={sorted({s for _, s in conc})}"
    )
    status = Path("/proc/self/status").read_text()
    fields = {k: v.strip() for k, v in (ln.split(":", 1) for ln in status.splitlines() if ":" in ln)}
    print(
        f"RSS: start {rss0:.0f} MB, after first request {rss_loaded:.0f} MB, "
        f"end {proc.memory_info().rss / 1e6:.0f} MB "
        f"(anon {fields.get('RssAnon')}, file-backed/mmap {fields.get('RssFile')})"
    )


if __name__ == "__main__":
    main()
