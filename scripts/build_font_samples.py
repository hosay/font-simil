#!/usr/bin/env python3
"""Pregenerate "The quick brown fox ..." sample PNGs for every matchable face.

Covers every glyph-atlas face (what the image matcher can return, including
variable-font Regular/Bold instances) and every indexed DB font (what name
searches return). Existing samples are skipped, so re-runs are cheap. Run as
the service user so the files are readable by the app:

    runuser -u fontmatch -- venv/bin/python scripts/build_font_samples.py \
        --out /var/lib/fontmatch/font_samples
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fontmatch.image.paths import SEARCH_DIRS  # noqa: E402
from fontmatch.samples import SampleStore  # noqa: E402


def _jobs(db: Path, atlas: Path) -> tuple[list[tuple[str, str]], dict[str, str]]:
    paths: dict[str, str] = {}
    jobs: list[tuple[str, str]] = []
    catalog = atlas / "catalog.json"
    if catalog.exists():
        paths = {e["name"]: e["path"] for e in json.loads(catalog.read_text())}
        for face in json.loads((atlas / "faces.json").read_text()):
            style = "" if face["style"] == "default" else face["style"]
            jobs.append((face["key"], style))
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for (name,) in conn.execute(
        "SELECT DISTINCT f.name FROM fonts f JOIN fingerprints p ON p.font_id = f.id "
        "WHERE COALESCE(f.license_id, '') != ''"  # only open-source fonts are ever returned
    ):
        jobs.append((name, ""))
    conn.close()
    return list(dict.fromkeys(jobs)), paths


_STORE: SampleStore | None = None


def _init(out: str, db: str, paths: dict[str, str]) -> None:
    global _STORE
    from fontmatch.index.store import FontStore
    from fontmatch.web.api import resolve_font_file

    store = FontStore(db)
    dirs = [str(d) for d in SEARCH_DIRS if d.is_dir()]

    def resolve(name: str) -> Path | None:
        if name in paths:
            return Path(paths[name])
        return resolve_font_file(store, dirs, name, walk=False)

    _STORE = SampleStore(Path(out), resolve)


def _one(job: tuple[str, str]) -> bool:
    return _STORE.get(*job) is not None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--db", default=str(ROOT / "fontmatch.db"))
    ap.add_argument("--atlas", default=str(ROOT / "glyph_atlas"))
    ap.add_argument("--out", default=str(ROOT / "font_samples"))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    start = time.time()
    jobs, paths = _jobs(Path(args.db), Path(args.atlas))
    print(f"{len(jobs)} samples to check", flush=True)
    with ProcessPoolExecutor(
        args.workers, initializer=_init, initargs=(args.out, args.db, paths)
    ) as pool:
        results = list(pool.map(_one, jobs, chunksize=32))
    ok = sum(results)
    files = list(Path(args.out).glob("*.png"))
    size = sum(f.stat().st_size for f in files) / 1e6
    print(
        f"{ok}/{len(jobs)} available, {len(jobs) - ok} unrenderable; "
        f"{len(files)} files, {size:.0f} MB in {time.time() - start:.0f}s -> {args.out}"
    )


if __name__ == "__main__":
    main()
