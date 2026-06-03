"""Combined fingerprint: metric + perceptual vectors."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fontmatch.features.metrics import MetricVector, metrics
from fontmatch.features.perceptual import (
    FINGERPRINT_SCHEMA_VERSION,
    RENDERER_VERSION,
    compose_sheet,
    perceptual,
    render_glyphs,
)
from fontmatch.fonts.loader import LoadedFont


@dataclass(frozen=True)
class Fingerprint:
    """A complete font fingerprint combining metric and perceptual features."""

    file_hash: str
    family: str
    subfamily: str
    metric_vec: MetricVector
    perceptual_vec: np.ndarray
    schema_version: int
    renderer_version: str

    def metric_array(self) -> np.ndarray:
        return self.metric_vec.to_array()


def fingerprint(font: LoadedFont) -> Fingerprint:
    """Compute a full fingerprint for a loaded font."""
    metric_vec = metrics(font)
    glyphs = render_glyphs(font)
    sheet = compose_sheet(glyphs)
    perceptual_vec = perceptual(sheet)

    return Fingerprint(
        file_hash=font.file_hash,
        family=font.family,
        subfamily=font.subfamily,
        metric_vec=metric_vec,
        perceptual_vec=perceptual_vec,
        schema_version=FINGERPRINT_SCHEMA_VERSION,
        renderer_version=RENDERER_VERSION,
    )
