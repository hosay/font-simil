"""Render-and-compare ranking of candidate faces for an image of text.

For each face in the glyph atlas we typeset the query's own text, then
compare it with the text in the image:

- shape: Pearson correlation of the two lines after scaling to a common
  height and the query's width, with a light blur to absorb sub-pixel noise
- aspect: |log| ratio of line width/height (proportions, advance widths:
  the strongest signal for metric clones)
- ink: |log| ratio of ink coverage (stroke weight)

score = shape - ASPECT_WEIGHT * aspect_penalty - INK_WEIGHT * ink_penalty
Weights are tuned on the eval dev split (docs/image-matching.md).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter

from fontmatch.image.glyphs import GlyphAtlas
from fontmatch.image.prep import crop_box, deskew_soft, ink_map

LINE_HEIGHT = 32
BLUR_SIGMA = 1.0
ASPECT_WEIGHT = 0.25  # tuned on dev (scripts/tune_image_ranker.py)
INK_WEIGHT = 0.0  # tuned: no gain on dev; kept as a knob
MIN_COVERAGE = 0.85
MAX_CHARS = 40
PREFILTER_FACES = 1200  # faces kept after the metric-only aspect prefilter
# A line of MAX_CHARS characters is never wider than ~1.6 em per char; anything
# wider is not a text line (and would make the comparison stack huge).
MAX_QUERY_ASPECT = MAX_CHARS * 1.6
SCORE_CHUNK = 200  # candidates compared per batch (bounds peak memory)
# Re-rank the best candidates by HOG (gradient-orientation) similarity: +2-3
# points on dev for ~10 ms. CLIP was tried too and rejected (see docs).
HOG_WEIGHT = 2.0
RERANK_TOP = 60
HOG_MIN_WIDTH = 16
CASING_PREFILTER = 300  # faces used to decide the transcript's casing
CASING_MARGIN = 0.005  # the hint's own casing wins near-ties

DEFAULT_ATLAS_DIR = Path(__file__).resolve().parent.parent.parent / "glyph_atlas"


INK_THRESHOLD = 0.3  # soft-ink level that counts as "ink" for bounding boxes


def _resize(
    soft: np.ndarray, width: int, height: int, resample=Image.Resampling.BILINEAR
) -> np.ndarray:
    img = Image.fromarray(np.asarray(soft, dtype=np.float32), mode="F")
    return np.asarray(img.resize((max(1, width), max(1, height)), resample), dtype=np.float32)


@dataclass
class LineFeatures:
    soft: np.ndarray  # cropped soft ink map, float32 in [0, 1]
    aspect: float  # width / height of the ink box
    ink: float  # mean ink coverage inside the box

    def normalized(self, width: int | None = None) -> np.ndarray:
        if width is None:
            width = max(1, int(round(self.aspect * LINE_HEIGHT)))
        return gaussian_filter(_resize(self.soft, width, LINE_HEIGHT), BLUR_SIGMA)

    def at_height(self, height: int) -> "LineFeatures":
        """Resample to a lower pixel height, simulating the query's resolution."""
        if height >= self.soft.shape[0]:
            return self
        width = max(1, int(round(self.aspect * height)))
        soft = _resize(self.soft, width, height, Image.Resampling.BOX)
        return LineFeatures(soft=soft, aspect=self.aspect, ink=float(soft.mean()))

    def correlate(self, other: "LineFeatures") -> float:
        a = self.normalized()
        b = other.at_height(self.soft.shape[0]).normalized(a.shape[1])
        return _pearson(a, b)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    denom = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
    return float((a * b).sum() / denom) if denom > 0 else 0.0


def line_features(soft: np.ndarray) -> LineFeatures | None:
    """Features of one line of text from a soft ink map (bool masks accepted)."""
    soft = np.asarray(soft, dtype=np.float32)
    box = crop_box(soft > INK_THRESHOLD)
    if box is None:
        return None
    top, bottom, left, right = box
    cropped = soft[top:bottom, left:right]
    if cropped.shape[0] < 4:
        return None
    h, w = cropped.shape
    return LineFeatures(soft=cropped, aspect=w / h, ink=float(cropped.mean()))


@dataclass(frozen=True)
class Match:
    family: str  # base family
    key: str  # catalog font name
    style: str
    score: float
    shape: float
    aspect_penalty: float
    ink_penalty: float


class ImageMatcher:
    def __init__(self, atlas: GlyphAtlas, family_of: dict[str, str]):
        self.atlas = atlas
        self.family_of = family_of
        self.faces = np.array(
            [i for i, f in enumerate(atlas.faces) if f["key"] in family_of], dtype=np.int64
        )
        self._spaces = np.array([f["space"] for f in atlas.faces], dtype=np.float32)

    def estimate_aspects(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        """(aspect, coverage) per candidate face from glyph metrics alone,
        without rendering. Glyph boxes include side bearings, so this is a
        cheap prefilter, not the final measurement."""
        idx = self.atlas.char_indices(text)
        faces = self.faces
        n_faces = len(faces)
        pen = np.zeros(n_faces, dtype=np.float32)
        x0 = np.full(n_faces, np.inf, dtype=np.float32)
        x1 = np.full(n_faces, -np.inf, dtype=np.float32)
        y0 = np.full(n_faces, np.inf, dtype=np.float32)
        y1 = np.full(n_faces, -np.inf, dtype=np.float32)
        drawn = np.zeros(n_faces, dtype=np.float32)
        wanted = 0
        for ci in idx:
            if ci is None or ci < 0:
                pen += self._spaces[faces]
                continue
            wanted += 1
            meta = self.atlas.meta[faces, ci]  # (F, 5)
            present = meta[:, 1] >= 0
            gx = pen + meta[:, 3]
            x0 = np.where(present, np.minimum(x0, gx), x0)
            x1 = np.where(present, np.maximum(x1, gx + meta[:, 1]), x1)
            y0 = np.where(present, np.minimum(y0, meta[:, 4]), y0)
            y1 = np.where(present, np.maximum(y1, meta[:, 4] + meta[:, 2]), y1)
            pen = pen + np.where(present, self.atlas.advance[faces, ci], self._spaces[faces])
            drawn += present
        coverage = drawn / wanted if wanted else np.zeros(n_faces, dtype=np.float32)
        height = np.maximum(y1 - y0, 1)
        aspect = np.where(np.isfinite(x0), (x1 - x0) / height, np.nan)
        return aspect, coverage

    def best_casing(self, mask: np.ndarray, variants: list[str]) -> str:
        """Pick the casing of the transcript that explains the image best.

        ChatGPT may transcribe an all-caps logo as "Welcome Home". Typeset each
        variant (as given, UPPER) against a reduced candidate set and keep the
        one whose top-5 scores are higher; the given casing wins ties. On dev
        this picks the right casing 96% of the time (aspect alone: ~80%).
        """
        options = list(dict.fromkeys(v for base in variants for v in (base, base.upper())))
        if len(options) == 1:
            return options[0]

        def fit(text: str) -> float:
            comp = self.components(mask, text, prefilter=CASING_PREFILTER)
            if comp is None:
                return float("-inf")
            _, shape, aspect_pen, _ = comp
            score = shape - ASPECT_WEIGHT * aspect_pen
            return float(np.sort(score)[-5:].mean())

        scores = {o: fit(o) for o in options}
        given = options[0]
        best = max(options, key=lambda o: scores[o])
        return best if scores[best] > scores[given] + CASING_MARGIN else given

    def components(self, mask: np.ndarray, text: str, prefilter: int | None = PREFILTER_FACES):
        """Raw per-face signals for a query. Returns (faces, shape, aspect_pen, ink_pen)
        arrays, or None if the query has no usable ink/text. Used by rank_mask
        and by scripts/tune_image_ranker.py."""
        text = " ".join(text.split())[:MAX_CHARS]
        query = line_features(deskew_soft(np.asarray(mask, dtype=np.float32)))
        if query is None or not text:
            return None
        if query.aspect > MAX_QUERY_ASPECT:
            return None
        query_height = query.soft.shape[0]

        est_aspect, coverage = self.estimate_aspects(text)
        ok = (coverage >= MIN_COVERAGE) & np.isfinite(est_aspect) & (est_aspect > 0)
        candidates = self.faces[ok]
        if prefilter is not None and len(candidates) > prefilter:
            gap = np.abs(np.log(query.aspect / est_aspect[ok]))
            candidates = candidates[np.argsort(gap)[:prefilter]]

        q = query.normalized()
        width = q.shape[1]
        qv = (q - q.mean()).ravel()
        q_norm = float(np.linalg.norm(qv)) or 1.0
        faces, aspects, inks, shapes = [], [], [], []
        for start in range(0, len(candidates), SCORE_CHUNK):
            stack = []
            for face in candidates[start : start + SCORE_CHUNK]:
                composed, _ = self.atlas.compose(int(face), text)
                cand = line_features(composed.astype(np.float32) / 255.0)
                if cand is None:
                    continue
                cand = cand.at_height(query_height)
                stack.append(_resize(cand.soft, width, LINE_HEIGHT))
                faces.append(int(face))
                aspects.append(cand.aspect)
                inks.append(cand.ink)
            if not stack:
                continue
            c = gaussian_filter(np.stack(stack), (0, BLUR_SIGMA, BLUR_SIGMA))
            c = c.reshape(len(stack), -1)
            c -= c.mean(axis=1, keepdims=True)
            denom = np.linalg.norm(c, axis=1) * q_norm
            shapes.append((c @ qv) / np.where(denom > 0, denom, 1.0))
        if not faces:
            return None
        shape = np.concatenate(shapes)
        aspect_pen = np.abs(np.log(query.aspect / np.asarray(aspects)))
        ink_pen = np.abs(np.log(max(query.ink, 1e-3) / np.maximum(np.asarray(inks), 1e-3)))
        return np.asarray(faces), shape, aspect_pen, ink_pen

    def rank_mask(
        self, mask: np.ndarray, text: str, k: int = 10, dedupe: bool = True
    ) -> list[Match]:
        comp = self.components(mask, text)
        if comp is None:
            return []
        faces, shape, aspect_pen, ink_pen = comp
        score = shape - ASPECT_WEIGHT * aspect_pen - INK_WEIGHT * ink_pen
        order_key = self._hog_rerank(mask, text, faces, score) if HOG_WEIGHT else score
        out, seen = [], set()
        for i in np.argsort(-order_key):
            info = self.atlas.faces[faces[i]]
            family = self.family_of[info["key"]]
            if dedupe and family in seen:
                continue
            seen.add(family)
            out.append(
                Match(
                    family=family,
                    key=info["key"],
                    style=info["style"],
                    score=float(score[i]),
                    shape=float(shape[i]),
                    aspect_penalty=float(aspect_pen[i]),
                    ink_penalty=float(ink_pen[i]),
                )
            )
            if len(out) >= k:
                break
        return out

    def _hog_rerank(self, mask, text, faces, score):
        """Ordering key: HOG similarity added to the top RERANK_TOP scores, which
        are lifted above the rest. Match.score keeps the un-boosted value."""
        from skimage.feature import hog

        query = line_features(deskew_soft(np.asarray(mask, dtype=np.float32)))
        text = " ".join(text.split())[:MAX_CHARS]
        q = query.normalized()
        width = q.shape[1]

        def vec(img):
            v = hog(img, orientations=9, pixels_per_cell=(8, 8), cells_per_block=(2, 2))
            return v / (np.linalg.norm(v) or 1.0)

        if width < HOG_MIN_WIDTH:
            return score  # skimage's hog needs at least one 2x2 block of 8 px cells
        qh = vec(q)
        top = np.argsort(-score)[:RERANK_TOP]
        boosted = score.copy()
        offset = float(score.max() - score.min()) + 1.0
        for i in top:
            composed, _ = self.atlas.compose(int(faces[i]), text)
            cand = line_features(composed.astype(np.float32) / 255.0)
            cand = cand.at_height(query.soft.shape[0])
            boosted[i] = score[i] + HOG_WEIGHT * float(qh @ vec(cand.normalized(width))) + offset
        return boosted

    def rank(self, img: Image.Image, text: str, k: int = 10, dedupe: bool = True) -> list[Match]:
        return self.rank_mask(ink_map(img), text, k=k, dedupe=dedupe)


class EvalAdapter:
    """Full pipeline (locate -> mask -> rank) behind the eval harness interface.

    use_hint=True simulates ChatGPT passing text_hint; False is the website
    path where only OCR is available.
    """

    def __init__(self, matcher: ImageMatcher, use_hint: bool = True):
        self.matcher = matcher
        self.use_hint = use_hint
        self.last_transcript = None

    def rank(self, img, text="", k=10):
        from fontmatch.image.locate import locate

        located = locate(img, hint=text if self.use_hint else "")
        if located is None:
            self.last_transcript = None
            return []
        transcript, matches = rank_located(self.matcher, located, k=k)
        self.last_transcript = transcript
        return [(m.family, m.score) for m in matches]


def rank_located(matcher: ImageMatcher, located, k: int = 10) -> tuple[str, list[Match]]:
    """Shared by the service and the eval: drop ink outside the OCR box, fix the
    hint's casing to fit the image, then rank. Returns (transcript, matches)."""
    from fontmatch.image.prep import keep_components_touching

    soft = keep_components_touching(ink_map(located.crop), located.ink_box)
    transcript = located.transcript
    if located.source == "hint":
        transcript = matcher.best_casing(soft, [transcript])
    return transcript, matcher.rank_mask(soft, transcript, k=k)


def load_atlas(atlas_dir: Path | None = None) -> GlyphAtlas:
    return GlyphAtlas.load(
        Path(atlas_dir or os.environ.get("FONTMATCH_GLYPH_ATLAS", DEFAULT_ATLAS_DIR))
    )


def load_default_ranker(catalog, atlas_dir: Path | None = None, use_hint: bool = True):
    family_of = {e.name: e.base_family for e in catalog}
    return EvalAdapter(ImageMatcher(load_atlas(atlas_dir), family_of), use_hint=use_hint)
