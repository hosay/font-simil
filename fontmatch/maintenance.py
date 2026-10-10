"""Data-retention sweeps, runnable on a schedule.

``create_app`` runs these once at startup, which only enforces the retention
windows in ``templates/privacy.html`` as often as the service is deployed.
A systemd timer runs this module so the promised dates hold without a deploy.

    python -m fontmatch.maintenance [--db PATH]
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from fontmatch.index.store import FontStore

# Days each kind of record is kept. Must match the windows stated in
# templates/privacy.html and the defaults in service/app.py.
RETENTION = {"usage": 90, "image_cache": 30, "rating_ips": 365}

log = logging.getLogger(__name__)


def run_sweeps(store: FontStore) -> dict[str, int]:
    """Apply every retention window. Returns rows removed per sweep."""
    return {
        "usage": store.cleanup_old_usage(days=RETENTION["usage"]),
        "image_cache": store.cleanup_image_cache(days=RETENTION["image_cache"]),
        "rating_ips": store.forget_old_rating_ips(days=RETENTION["rating_ips"]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("FONTMATCH_DB", "fontmatch.db")),
        help="SQLite database (default: $FONTMATCH_DB or ./fontmatch.db)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.db.exists():
        log.error("no database at %s", args.db)
        return 1

    removed = run_sweeps(FontStore(args.db))
    for name, rows in removed.items():
        log.info("%s: removed %d row(s) older than %d days", name, rows, RETENTION[name])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
