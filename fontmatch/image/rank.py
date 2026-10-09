"""Render-and-compare ranking of candidate faces for an image of text.

For each face in the glyph atlas we typeset the query's own text, then
compare it with the text in the image:

- tracking: each candidate is typeset with the uniform letter-spacing that
  makes its line exactly as wide (relative to its height) as the query's.
  Logos, headings and CSS letter-spacing rarely use a font's natural
  spacing, and comparing at natural spacing shifts every glyph out of phase
  with the image (the Google logo matched a serif that way).
- shape: Pearson correlation of the two lines after scaling to a common
  height and the query's width, with a light blur to absorb sub-pixel noise
- spacing: how unusual the fitted letter-spacing is (free inside a band of
  normal spacing, linear outside it) plus any width mismatch left when the
  fit is clamped. Proportions are the strongest signal for metric clones;
  their fitted spacing is ~0.
- ink: |log| ratio of ink coverage (stroke weight)

score = shape - SPACING_WEIGHT * spacing_penalty - INK_WEIGHT * ink_penalty
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

from fontmatch.image.glyphs import EM_PX, GlyphAtlas
from fontmatch.image.prep import crop_box, deskew_soft, ink_map

LINE_HEIGHT = 32
BLUR_SIGMA = 1.0
# Letter-spacing fit, in em (atlas glyphs are rendered at EM_PX per em).
TRACK_MIN_EM = -0.15
TRACK_MAX_EM = 0.6
# Spacing prior, tuned on dev (scripts/tune_image_ranker.py; the objective
# is flat near the optimum, so these are the simplest values in that plateau).
TRACK_FREE_EM = (-0.03, 0.0)  # natural spacing +- kerning: no penalty
TRACK_SLOPE = (2.0, 1.0)  # penalty per em below / above the free band (tight is rarer)
RESIDUAL_WEIGHT = 3.0  # per |log| unit of width the clamped fit can't explain
SPACING_WEIGHT = 0.5
INK_WEIGHT = 0.0  # tuned: no gain on dev; kept as a knob
MIN_COVERAGE = 0.85
MAX_CHARS = 40
# Faces kept for the rendered comparison (see prefilter_order). On dev the
# true face survives for 99-100% of screenshots/browser renders and 86% of
# photos; 2000 faces gain 1-3 points (noise level) for +0.6 s at p95.
PREFILTER_FACES = 1200
PROFILE_BINS = 16  # vertical resolution of the row-profile prefilter
PREFILTER_SPACING_WEIGHT = 0.1  # spacing prior's weight in the prefilter key
# A line of MAX_CHARS characters is never wider than ~1.6 em per char; anything
# wider is not a text line (and would make the comparison stack huge).
MAX_QUERY_ASPECT = MAX_CHARS * 1.6
SCORE_CHUNK = 200  # candidates compared per batch (bounds peak memory)
# Re-rank the best candidates by HOG (gradient-orientation) similarity: +2-3
# points on dev for ~10 ms. CLIP was tried too and rejected (see docs).
HOG_WEIGHT = 2.0
RERANK_TOP = 60
# Re-ranked faces whose Google Fonts category differs from the (key-weighted)
# majority category of the top 3 families lose this much. On dev: LOFO
# category@1 +4..11 points, a serif among a sans query's alternatives half as
# often, family@1 -0..4 points (scripts/tune_image_ranker.py, "vote").
CATEGORY_VOTE = 0.1
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


PROFILE_MASS = (0.02, 0.98)  # profiles span these ink-mass quantiles


def mass_profiles(rows: np.ndarray, bins: int = PROFILE_BINS) -> np.ndarray:
    """Resample row-ink profiles (F, B) into ``bins`` equal bands between the
    PROFILE_MASS quantiles of each row's ink, normalised to sum 1.

    Using ink-mass quantiles instead of the ink box makes the profile
    insensitive to faint anti-aliased edge rows, which differ by renderer
    (one such row moved box-based profiles as much as a different font)."""
    rows = np.asarray(rows, dtype=np.float32)
    n_rows, n_bins = rows.shape
    cum = np.concatenate([np.zeros((n_rows, 1), np.float32), np.cumsum(rows, axis=1)], axis=1)
    total = cum[:, -1:]
    frac = cum / np.where(total > 0, total, 1.0)  # (F, B + 1), non-decreasing in [0, 1]

    def position(q: float) -> np.ndarray:
        # fractional bin position where the cumulative mass reaches q
        hi = np.clip((frac < q).sum(axis=1), 1, n_bins)
        f0 = np.take_along_axis(frac, (hi - 1)[:, None], axis=1)[:, 0]
        f1 = np.take_along_axis(frac, hi[:, None], axis=1)[:, 0]
        return (hi - 1) + np.clip((q - f0) / np.where(f1 > f0, f1 - f0, 1.0), 0.0, 1.0)

    lo, hi = position(PROFILE_MASS[0]), position(PROFILE_MASS[1])
    edges = lo[:, None] + (hi - lo)[:, None] * np.linspace(0.0, 1.0, bins + 1)
    base = np.clip(np.floor(edges).astype(np.int64), 0, n_bins - 1)
    t = edges - base
    at = (
        np.take_along_axis(cum, base, axis=1) * (1 - t)
        + np.take_along_axis(cum, base + 1, axis=1) * t
    )
    out = np.maximum(np.diff(at, axis=1), 0.0).astype(np.float32)
    s = out.sum(axis=1, keepdims=True)
    return out / np.where(s > 0, s, 1.0)


def query_profile(soft: np.ndarray, bins: int = PROFILE_BINS) -> np.ndarray:
    """Vertical ink profile of a query line (see ImageMatcher.line_profiles)."""
    return mass_profiles(np.asarray(soft, dtype=np.float32).sum(axis=1)[None], bins)[0]


def family_margins(families: np.ndarray, key: np.ndarray, reranked: np.ndarray) -> np.ndarray:
    """Per face: its key minus the best key of any *other* family, among the
    re-ranked faces (keys outside lack the HOG term). 0 when there is no
    comparable rival or the face wasn't re-ranked. Shared with
    scripts/tune_image_ranker.py, which calibrates SAME_FONT_MARGIN on it."""
    out = np.zeros(len(key), dtype=np.float32)
    idx = np.flatnonzero(reranked)
    best: dict[str, float] = {}
    for i in idx:
        f = families[i]
        best[f] = max(best.get(f, -np.inf), float(key[i]))
    if len(best) < 2:
        return out
    ranked = sorted(best.items(), key=lambda t: -t[1])
    (f1, k1), (_, k2) = ranked[0], ranked[1]
    for i in idx:
        rival = k2 if families[i] == f1 else k1
        out[i] = float(key[i]) - rival
    return out


def majority_category(categories: list[str]) -> str:
    """Most common category among the top families (one vote each, best
    first); without a majority, the top family's."""
    counts: dict[str, int] = {}
    for c in categories:
        counts[c] = counts.get(c, 0) + 1
    return max(counts, key=lambda c: (counts[c], -categories.index(c)))


@dataclass(frozen=True)
class Match:
    family: str  # base family
    key: str  # catalog font name
    style: str
    score: float
    shape: float
    spacing_penalty: float
    ink_penalty: float
    tracking_em: float = 0.0
    # Ranking key minus the best key of any other family: how clearly this
    # face beats the alternatives (confidence; see service.SAME_FONT_MARGIN).
    margin: float = 0.0


@dataclass
class Signals:
    """Raw per-face signals for one query (see the module docstring).

    ``score()`` combines them; scripts/tune_image_ranker.py uses the raw
    arrays to tune the weights."""

    faces: np.ndarray  # atlas face indices
    shape: np.ndarray
    tracking_em: np.ndarray  # fitted letter-spacing
    residual: np.ndarray  # |log| width mismatch left after clamping the fit
    ink_penalty: np.ndarray

    def spacing_penalty(self) -> np.ndarray:
        return spacing_penalty(self.tracking_em, self.residual)

    def score(self) -> np.ndarray:
        return self.shape - SPACING_WEIGHT * self.spacing_penalty() - INK_WEIGHT * self.ink_penalty

    def tracking_px(self, i: int) -> float:
        return float(self.tracking_em[i]) * EM_PX


def spacing_penalty(tracking_em, residual) -> np.ndarray:
    lo, hi = TRACK_FREE_EM
    t = np.asarray(tracking_em, dtype=np.float32)
    below = np.maximum(0.0, lo - t) * TRACK_SLOPE[0]
    above = np.maximum(0.0, t - hi) * TRACK_SLOPE[1]
    return below + above + RESIDUAL_WEIGHT * np.asarray(residual, dtype=np.float32)


@dataclass
class LineMetrics:
    """Per-face line geometry from glyph metrics alone (no rendering), in
    atlas pixels. ``steps`` = advances between the first and last drawn
    glyph, i.e. how many times a letter-spacing value adds to the width."""

    width: np.ndarray
    height: np.ndarray
    steps: np.ndarray
    coverage: np.ndarray

    @property
    def aspect(self) -> np.ndarray:
        return np.where(np.isfinite(self.width), self.width / self.height, np.nan)

    def fit_tracking(self, query_aspect: float) -> tuple[np.ndarray, np.ndarray]:
        """(tracking in em, residual) per face: the letter-spacing that makes
        the line's aspect equal ``query_aspect``, clamped to
        [TRACK_MIN_EM, TRACK_MAX_EM], and the |log| aspect gap that remains."""
        width = np.where(np.isfinite(self.width), self.width, 0.0)
        steps = np.maximum(self.steps, 0)
        need = query_aspect * self.height - width
        with np.errstate(divide="ignore", invalid="ignore"):
            px = np.where(steps > 0, need / np.where(steps > 0, steps, 1), 0.0)
        em = np.clip(px / EM_PX, TRACK_MIN_EM, TRACK_MAX_EM).astype(np.float32)
        fitted = np.maximum(width + em * EM_PX * steps, 1.0) / self.height
        residual = np.abs(np.log(query_aspect / fitted)).astype(np.float32)
        return em, residual


class ImageMatcher:
    def __init__(
        self,
        atlas: GlyphAtlas,
        family_of: dict[str, str],
        category_of: dict[str, str] | None = None,
    ):
        """``family_of``/``category_of``: catalog font name -> base family /
        Google Fonts category. Without categories there is no category vote."""
        self.atlas = atlas
        self.family_of = family_of
        self.category_of = category_of
        from fontmatch.image.catalog import is_text_family

        self.faces = np.array(
            [
                i
                for i, f in enumerate(atlas.faces)
                if f["key"] in family_of and is_text_family(family_of[f["key"]])
            ],
            dtype=np.int64,
        )
        self._spaces = np.array([f["space"] for f in atlas.faces], dtype=np.float32)
        self.atlas_row_bins = int(atlas.row_profiles(0).shape[1]) if atlas.meta.shape[1] else 0

    def line_metrics(self, text: str) -> LineMetrics:
        """Line geometry per candidate face from glyph metrics alone. The
        boxes are the glyph bitmaps' (ink), so this matches what compose()
        draws up to pixel rounding."""
        idx = self.atlas.char_indices(text)
        faces = self.faces
        n_faces = len(faces)
        pen = np.zeros(n_faces, dtype=np.float32)
        x0 = np.full(n_faces, np.inf, dtype=np.float32)
        x1 = np.full(n_faces, -np.inf, dtype=np.float32)
        y0 = np.full(n_faces, np.inf, dtype=np.float32)
        y1 = np.full(n_faces, -np.inf, dtype=np.float32)
        first = np.full(n_faces, -1, dtype=np.int32)
        last = np.full(n_faces, -1, dtype=np.int32)
        drawn = np.zeros(n_faces, dtype=np.float32)
        wanted = 0
        for pos, ci in enumerate(idx):
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
            first = np.where(present & (first < 0), pos, first)
            last = np.where(present, pos, last)
            pen = pen + np.where(present, self.atlas.advance[faces, ci], self._spaces[faces])
            drawn += present
        coverage = drawn / wanted if wanted else np.zeros(n_faces, dtype=np.float32)
        width = np.where(np.isfinite(x0), x1 - x0, np.nan).astype(np.float32)
        return LineMetrics(
            width=width,
            height=np.maximum(y1 - y0, 1).astype(np.float32),
            steps=np.maximum(last - first, 0).astype(np.float32),
            coverage=np.asarray(coverage, dtype=np.float32),
        )

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
            sig = self.components(mask, text, prefilter=CASING_PREFILTER)
            if sig is None:
                return float("-inf")
            return float(np.sort(sig.score())[-5:].mean())

        scores = {o: fit(o) for o in options}
        given = options[0]
        best = max(options, key=lambda o: scores[o])
        return best if scores[best] > scores[given] + CASING_MARGIN else given

    def _query(self, mask: np.ndarray) -> LineFeatures | None:
        query = line_features(deskew_soft(np.asarray(mask, dtype=np.float32)))
        if query is None or query.aspect > MAX_QUERY_ASPECT:
            return None
        return query

    def _typeset(self, face: int, text: str, tracking_px: float) -> LineFeatures | None:
        composed, _ = self.atlas.compose(face, text, tracking=tracking_px)
        return line_features(composed.astype(np.float32) / 255.0)

    def line_profiles(self, text: str) -> np.ndarray:
        """(faces, PROFILE_BINS) vertical ink profile of the typeset line per
        face (see mass_profiles). Built from per-glyph row profiles, so no
        rendering; letter-spacing doesn't change it."""
        total = np.zeros((len(self.faces), self.atlas_row_bins), dtype=np.float32)
        for ci in self.atlas.char_indices(text):
            if ci is not None and ci >= 0:
                total += self.atlas.row_profiles(ci)[self.faces]
        return mass_profiles(total, PROFILE_BINS)

    def prefilter_order(
        self, query: LineFeatures, metrics: LineMetrics, ok: np.ndarray, text: str
    ):
        """Indices into self.faces (where ``ok``), most plausible first, without
        rendering: distance between the query's vertical ink profile and each
        face's (x-height, ascenders, descenders, where the weight sits), plus
        the spacing prior of the letter-spacing the face would need.
        Returns (order, fitted tracking in em for every face)."""
        cand = np.flatnonzero(ok)
        em, residual = metrics.fit_tracking(query.aspect)
        if not len(cand):
            return cand, em
        q = query_profile(query.soft)
        prof = self.line_profiles(text)[cand]
        key = np.abs(prof - q).sum(axis=1)
        key += PREFILTER_SPACING_WEIGHT * spacing_penalty(em[cand], residual[cand])
        return cand[np.argsort(key, kind="stable")], em

    def components(
        self, mask: np.ndarray, text: str, prefilter: int | None | str = "default"
    ) -> Signals | None:
        """Raw per-face signals for a query, or None if the query has no usable
        ink/text. ``prefilter``: faces to render ("default" = PREFILTER_FACES,
        None = all). Used by rank_mask and by scripts/tune_image_ranker.py."""
        if prefilter == "default":
            prefilter = PREFILTER_FACES
        text = " ".join(text.split())[:MAX_CHARS]
        query = self._query(mask)
        if query is None or not text:
            return None
        query_height = query.soft.shape[0]

        metrics = self.line_metrics(text)
        aspect = metrics.aspect
        ok = (metrics.coverage >= MIN_COVERAGE) & np.isfinite(aspect) & (aspect > 0)
        order, track_em = self.prefilter_order(query, metrics, ok, text)
        if prefilter is not None:
            order = order[:prefilter]

        q = query.normalized()
        width = q.shape[1]
        qv = (q - q.mean()).ravel()
        q_norm = float(np.linalg.norm(qv)) or 1.0
        faces, tracks, aspects, inks, shapes = [], [], [], [], []
        for start in range(0, len(order), SCORE_CHUNK):
            stack = []
            for j in order[start : start + SCORE_CHUNK]:
                face = int(self.faces[j])
                cand = self._typeset(face, text, float(track_em[j]) * EM_PX)
                if cand is None:
                    continue
                cand = cand.at_height(query_height)
                stack.append(_resize(cand.soft, width, LINE_HEIGHT))
                faces.append(face)
                tracks.append(track_em[j])
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
        ink_pen = np.abs(np.log(max(query.ink, 1e-3) / np.maximum(np.asarray(inks), 1e-3)))
        return Signals(
            faces=np.asarray(faces),
            shape=np.concatenate(shapes),
            tracking_em=np.asarray(tracks, dtype=np.float32),
            # what the rendered line still misses after the (clamped) fit
            residual=np.abs(np.log(query.aspect / np.asarray(aspects))).astype(np.float32),
            ink_penalty=ink_pen,
        )

    def rank_mask(
        self, mask: np.ndarray, text: str, k: int = 10, dedupe: bool = True
    ) -> list[Match]:
        sig = self.components(mask, text)
        if sig is None:
            return []
        score = sig.score()
        spacing = sig.spacing_penalty()
        order_key, key, reranked = (
            self._hog_rerank(mask, text, sig, score)
            if HOG_WEIGHT
            else (score, score, np.ones(len(score), dtype=bool))
        )
        families = np.array([self.family_of[self.atlas.faces[f]["key"]] for f in sig.faces])
        if self.category_of and CATEGORY_VOTE:
            order_key = order_key - CATEGORY_VOTE * self._off_category(sig, key, reranked)
        order = np.argsort(-order_key)
        margins = family_margins(families, key, reranked)
        out, seen = [], set()
        for i in order:
            info = self.atlas.faces[sig.faces[i]]
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
                    shape=float(sig.shape[i]),
                    spacing_penalty=float(spacing[i]),
                    ink_penalty=float(sig.ink_penalty[i]),
                    tracking_em=float(sig.tracking_em[i]),
                    margin=float(margins[i]),
                )
            )
            if len(out) >= k:
                break
        return out

    def _off_category(self, sig: Signals, key, reranked) -> np.ndarray:
        """1.0 for re-ranked faces outside the majority category of the top 3
        families (by key), else 0."""
        idx = np.flatnonzero(reranked)
        idx = idx[np.argsort(-key[idx])]
        names = [self.atlas.faces[sig.faces[i]]["key"] for i in idx]
        cats = [self.category_of.get(n, "") for n in names]
        top: dict[str, str] = {}
        for n, cat in zip(names, cats):
            top.setdefault(self.family_of[n], cat)
            if len(top) == 3:
                break
        out = np.zeros(len(key), dtype=np.float32)
        if top:
            majority = majority_category(list(top.values()))
            out[idx] = [c != majority for c in cats]
        return out

    def _hog_rerank(self, mask, text, sig: Signals, score):
        """(ordering key, key, reranked): HOG similarity added to the top
        RERANK_TOP scores; in the ordering key those are also lifted above the
        rest. ``key`` has no lift, so differences between re-ranked faces
        (``reranked`` mask) are meaningful. Match.score keeps the un-boosted
        value."""
        from skimage.feature import hog

        query = self._query(mask)
        text = " ".join(text.split())[:MAX_CHARS]
        q = query.normalized()
        width = q.shape[1]

        def vec(img):
            v = hog(img, orientations=9, pixels_per_cell=(8, 8), cells_per_block=(2, 2))
            return v / (np.linalg.norm(v) or 1.0)

        if width < HOG_MIN_WIDTH:
            # skimage's hog needs at least one 2x2 block of 8 px cells
            return score, score, np.ones(len(score), dtype=bool)
        qh = vec(q)
        top = np.argsort(-score)[:RERANK_TOP]
        reranked = np.zeros(len(score), dtype=bool)
        reranked[top] = True
        key = score.copy()
        for i in top:
            cand = self._typeset(int(sig.faces[i]), text, sig.tracking_px(i))
            cand = cand.at_height(query.soft.shape[0])
            key[i] = score[i] + HOG_WEIGHT * float(qh @ vec(cand.normalized(width)))
        lifted = key.copy()
        lifted[top] += float(key.max() - score.min()) + 1.0
        return lifted, key, reranked

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


MAX_CROP_HEIGHT = 400  # px; taller crops are downscaled before the ink map


def _bounded_crop(located):
    """(crop, ink_box) with the crop at most MAX_CROP_HEIGHT tall. Comparison
    happens at LINE_HEIGHT anyway; full-size crops (e.g. the whole image
    when OCR found nothing) made the ink map cost seconds."""
    crop, box = located.crop, located.ink_box
    if crop.height <= MAX_CROP_HEIGHT:
        return crop, box
    scale = MAX_CROP_HEIGHT / crop.height
    crop = crop.resize(
        (max(1, round(crop.width * scale)), MAX_CROP_HEIGHT), Image.Resampling.LANCZOS
    )
    if box is not None:
        left, top, right, bottom = box
        box = (
            math.floor(left * scale),
            math.floor(top * scale),
            math.ceil(right * scale),
            math.ceil(bottom * scale),
        )
    return crop, box


def rank_located(matcher: ImageMatcher, located, k: int = 10) -> tuple[str, list[Match]]:
    """Shared by the service and the eval: drop ink outside the OCR box, fix the
    hint's casing to fit the image, then rank. Returns (transcript, matches)."""
    from fontmatch.image.prep import keep_components_touching

    crop, ink_box = _bounded_crop(located)
    soft = keep_components_touching(ink_map(crop), ink_box)
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
    category_of = {e.name: e.category for e in catalog}
    return EvalAdapter(
        ImageMatcher(load_atlas(atlas_dir), family_of, category_of), use_hint=use_hint
    )
