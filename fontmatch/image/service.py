"""Image -> free font matching, as used by the API and the MCP server."""

from __future__ import annotations

import hashlib
import logging
import re
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
# 7: letter-spacing fit, colour ink map, row-profile prefilter
IMAGE_SCHEMA_VERSION = 9  # 9: line-box components, stacked signs, no non-text families
MAX_HINT_CHARS = 500
RETRY_FAILED_LOAD_AFTER = 60  # seconds
# The top match is labelled "likely the same font" when its ranking key beats
# every other family's by at least this much (rank.Match.margin). Measured
# with scripts/tune_image_ranker.py (calibrate) on 545 realistic dev queries
# (screenshots, photos, browser renders): 93% precise, applied to 35% of
# them; with the query's own family removed (a font not in the catalog, like
# most uploads) it fires 5.5% of the time (0.15: 11%, 0.06: 28%). The raw
# correlation is no use for this: with letter-spacing fitted, wrong fonts
# correlate highly too (~75% precision at any threshold).
SAME_FONT_MARGIN = 0.20

__all__ = ["EngineUnavailable", "ImageIdentifier", "LazyImageIdentifier", "NoTextFound"]


_WEIGHT_WORDS = (
    "Hairline|Thin|ExtraLight|Extra Light|UltraLight|Light|Book|Regular|Medium|"
    "SemiBold|Semi Bold|DemiBold|Bold|ExtraBold|Extra Bold|UltraBold|Black|Heavy"
)
_TRAILING_WEIGHT = re.compile(rf"\s+(?:{_WEIGHT_WORDS})(?:\s+Italic)?$")


def display_family(family: str, style: str) -> str:
    """Family name to show for a matched face.

    Variable fonts store their *default* instance in name ID 1 ("Outfit
    Thin"), but the atlas renders named instances (Regular/Bold); showing
    "Outfit Thin" for the Regular instance is wrong. Static files ("Poppins
    Medium", style "default") are shown as they are."""
    if style == "default":
        return family
    trimmed = _TRAILING_WEIGHT.sub("", family)
    return trimmed or family


def _percent(score: float) -> int:
    return int(round(max(0.0, min(1.0, score)) * 100))


class ImageIdentifier:
    def __init__(self, atlas: GlyphAtlas, catalog: list[CatalogEntry]):
        self.entries = {e.name: e for e in catalog}
        self.matcher = ImageMatcher(
            atlas,
            {e.name: e.base_family for e in catalog},
            {e.name: e.category for e in catalog},
        )
        digest = hashlib.sha256(str(IMAGE_SCHEMA_VERSION).encode())
        for face in atlas.faces:
            digest.update(f"{face['key']}|{face['style']}\n".encode())
        # Row profiles (prefilter input) are derived from the atlas pixels;
        # their layout constants decide what the prefilter sees. Sidecar and
        # lazily computed profiles are identical, so the file isn't hashed.
        from fontmatch.image import glyphs

        digest.update(f"rows|{glyphs.ROW_TOP}|{glyphs.ROW_BOTTOM}|{glyphs.ROW_BIN}\n".encode())
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
            same = rank == 0 and m.margin >= SAME_FONT_MARGIN
            out.append(
                {
                    "name": entry.name,
                    "family": display_family(entry.family, m.style),
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
