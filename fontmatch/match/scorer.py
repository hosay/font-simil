"""Similarity scoring, ranking, and evaluation."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fontmatch.features.fingerprint import Fingerprint

# Weight blend: metric features vs perceptual features
METRIC_WEIGHT = 0.4
PERCEPTUAL_WEIGHT = 0.6

# Normalization scales so both distance components contribute equally
# before weighting.  Derived from p95 of pairwise distances across the
# licensed corpus: metric Euclidean ≈ 1.3, CLIP cosine ≈ 0.06.
METRIC_SCALE = 1.5
PERCEPTUAL_SCALE = 0.06

# Per-field weights for the metric vector.  Fields like weight_class,
# width_class, cap_height, x_height, avg_width, and serif_score
# directly reflect visual appearance.  Ascender/descender are about
# line spacing and vary a lot between metric-compatible fonts (e.g.
# Arimo vs Liberation Sans), so they are downweighted.
# Field order: weight, width, italic, italic_angle, cap_h, x_h,
#              ascender, descender, avg_width, glyph_count, serif
_METRIC_FIELD_WEIGHTS = np.array([
    1.0,   # weight_class
    1.0,   # width_class
    1.0,   # is_italic
    1.0,   # italic_angle
    1.0,   # cap_height
    1.0,   # x_height
    0.3,   # ascender  — line spacing, not glyph shape
    0.3,   # descender — line spacing, not glyph shape
    1.0,   # avg_width
    0.5,   # glyph_count_log — coverage breadth, not visual character
    1.0,   # serif_score
], dtype=np.float64)


@dataclass
class Match:
    """A ranked match result."""

    name: str
    distance: float
    metric_distance: float
    perceptual_distance: float
    fingerprint: Fingerprint


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance between two vectors (1 - cosine_similarity)."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 1.0
    return 1.0 - dot / (norm_a * norm_b)


def _euclidean_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Weighted Euclidean distance using per-field importance weights."""
    diff = a - b
    if len(diff) == len(_METRIC_FIELD_WEIGHTS):
        diff = diff * _METRIC_FIELD_WEIGHTS
    return float(np.linalg.norm(diff))


def distance(fp_a: Fingerprint, fp_b: Fingerprint) -> float:
    """Compute weighted blend of metric and perceptual distance.

    Both distances are normalized to [0, ~1] before weighting so that
    the metric/perceptual weight ratio reflects actual importance.
    """
    m_a = fp_a.metric_array()
    m_b = fp_b.metric_array()
    metric_d = _euclidean_distance(m_a, m_b) / METRIC_SCALE

    p_a = fp_a.perceptual_vec
    p_b = fp_b.perceptual_vec
    perceptual_d = _cosine_distance(p_a, p_b) / PERCEPTUAL_SCALE

    return METRIC_WEIGHT * metric_d + PERCEPTUAL_WEIGHT * perceptual_d


def rank(
    query: Fingerprint,
    candidates: dict[str, Fingerprint],
    k: int = 10,
) -> list[Match]:
    """Rank candidates by similarity to query, return top-k."""
    results = []
    q_metric = query.metric_array()
    q_perceptual = query.perceptual_vec

    for name, fp in candidates.items():
        m_d = _euclidean_distance(q_metric, fp.metric_array()) / METRIC_SCALE
        p_d = _cosine_distance(q_perceptual, fp.perceptual_vec) / PERCEPTUAL_SCALE
        total = METRIC_WEIGHT * m_d + PERCEPTUAL_WEIGHT * p_d
        results.append(
            Match(
                name=name,
                distance=total,
                metric_distance=m_d,
                perceptual_distance=p_d,
                fingerprint=fp,
            )
        )

    results.sort(key=lambda m: m.distance)
    return results[:k]


def evaluate(
    ground_truth: dict[str, set[str]],
    corpus: dict[str, Fingerprint],
) -> dict[str, float]:
    """Compute Recall@1, Recall@5, and MRR over ground-truth pairs.

    ground_truth maps a query font name to a set of acceptable match names.
    """
    reciprocal_ranks = []
    recall_at_1 = 0
    recall_at_5 = 0
    n = len(ground_truth)

    for query_name, expected_matches in ground_truth.items():
        if query_name not in corpus:
            continue
        q = corpus[query_name]
        candidates = {name: fp for name, fp in corpus.items() if name != query_name}
        results = rank(q, candidates, k=len(candidates))
        ranked_names = [r.name for r in results]

        # Find first relevant position
        first_relevant = None
        for i, name in enumerate(ranked_names):
            if name in expected_matches:
                if first_relevant is None:
                    first_relevant = i + 1  # 1-indexed
                break

        if first_relevant is not None:
            reciprocal_ranks.append(1.0 / first_relevant)
            if first_relevant == 1:
                recall_at_1 += 1
            if first_relevant <= 5:
                recall_at_5 += 1
        else:
            reciprocal_ranks.append(0.0)

    return {
        "mrr": sum(reciprocal_ranks) / n if n else 0.0,
        "recall@1": recall_at_1 / n if n else 0.0,
        "recall@5": recall_at_5 / n if n else 0.0,
    }
