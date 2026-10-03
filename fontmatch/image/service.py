"""Image -> free font matching, as used by the API and the MCP server."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path

from fontmatch.image.catalog import CatalogEntry, build_catalog, load_catalog_json
from fontmatch.image.errors import EngineUnavailable, NoTextFound
from fontmatch.image.glyphs import GlyphAtlas
from fontmatch.image.locate import locate
from fontmatch.image.paths import SEARCH_DIRS
from fontmatch.image.prep import load_image
from fontmatch.image.rank import ImageMatcher, load_atlas, rank_located

logger = logging.getLogger(__name__)

# Bump when the ranker or scoring code changes. Atlas/catalog rebuilds are
# picked up automatically via ImageIdentifier.version (content hash).
IMAGE_SCHEMA_VERSION = 6
MAX_HINT_CHARS = 500
RETRY_FAILED_LOAD_AFTER = 60  # seconds
# Top match with shape correlation >= this is labelled "likely the same font":
# on the dev split that label was right 86% of the time (72 of 135 queries).
SAME_FONT_SHAPE = 0.94

__all__ = ["EngineUnavailable", "ImageIdentifier", "LazyImageIdentifier", "NoTextFound"]


def _percent(score: float) -> int:
    return int(round(max(0.0, min(1.0, score)) * 100))


class ImageIdentifier:
    def __init__(self, atlas: GlyphAtlas, catalog: list[CatalogEntry]):
        self.entries = {e.name: e for e in catalog}
        self.matcher = ImageMatcher(atlas, {e.name: e.base_family for e in catalog})
        digest = hashlib.sha256(str(IMAGE_SCHEMA_VERSION).encode())
        for face in atlas.faces:
            digest.update(f"{face['key']}|{face['style']}\n".encode())
        for name in sorted(self.entries):
            e = self.entries[name]
            digest.update(f"{name}|{e.family}|{e.license_id}|{e.category}\n".encode())
        # Changes whenever the code version, atlas or catalog changes; part of
        # the result-cache key so a rebuild never serves stale rankings.
        self.version = digest.hexdigest()[:16]

    @classmethod
    def from_paths(cls, db_path: Path, atlas_dir: Path | None = None) -> "ImageIdentifier":
        atlas = load_atlas(atlas_dir)
        manifest = Path(atlas.directory) / "catalog.json"
        if manifest.exists():
            catalog = load_catalog_json(manifest)
        else:
            logger.warning("%s missing; scanning the DB for the catalog", manifest)
            catalog = build_catalog(Path(db_path), SEARCH_DIRS)
        return cls(atlas, catalog)

    def identify(self, data: bytes, hint: str = "", k: int = 5) -> dict:
        img = load_image(data)
        hint = (hint or "")[:MAX_HINT_CHARS]
        located = locate(img, hint=hint)
        if located is None:
            raise NoTextFound("No readable text found in the image.")
        transcript, matches = rank_located(self.matcher, located, k=k)
        if not matches:
            raise NoTextFound("Couldn't match the text in the image to any font.")
        out = []
        shown = 100
        for rank, m in enumerate(matches):
            entry = self.entries[m.key]
            # Visual similarity (correlation of the two typeset lines), made
            # non-increasing so it reads consistently with the ranking.
            shown = min(shown, _percent(m.shape))
            same = rank == 0 and m.shape >= SAME_FONT_SHAPE
            out.append(
                {
                    "name": entry.name,
                    "family": entry.family,
                    "style": m.style if m.style != "default" else entry.subfamily,
                    "license_id": entry.license_id,
                    "category": entry.category,
                    "score": shown,
                    "match_label": "likely the same font" if same else "similar alternative",
                }
            )
        return {
            "transcript": transcript,
            "transcript_source": located.source,
            "text_box": list(located.box),
            "matches": out,
        }


class LazyImageIdentifier:
    """Builds the identifier on first use (catalog scan + atlas mmap), once."""

    def __init__(self, db_path: Path, atlas_dir: Path | None = None):
        self._db_path = db_path
        self._atlas_dir = atlas_dir
        self._lock = threading.Lock()
        self._instance: ImageIdentifier | None = None
        self._failed_at: float | None = None

    def get(self) -> ImageIdentifier:
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    if (
                        self._failed_at is not None
                        and time.monotonic() - self._failed_at < RETRY_FAILED_LOAD_AFTER
                    ):
                        raise EngineUnavailable("Image matching is temporarily unavailable.")
                    try:
                        self._instance = ImageIdentifier.from_paths(self._db_path, self._atlas_dir)
                    except Exception as exc:
                        self._failed_at = time.monotonic()
                        logger.exception("image matcher failed to load")
                        raise EngineUnavailable(
                            "Image matching is temporarily unavailable."
                        ) from exc
        return self._instance

    @property
    def version(self) -> str:
        return self.get().version

    def identify(self, data: bytes, hint: str = "", k: int = 5) -> dict:
        return self.get().identify(data, hint=hint, k=k)
