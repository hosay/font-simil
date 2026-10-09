"""Find the text line to match and its transcript.

Tesseract finds lines (boxes + its own reading). ChatGPT's ``text_hint`` is
always the transcript when given: it reads stylised text far better than
Tesseract. We pick the OCR line that best matches a hint segment (or the most
prominent line if none matches) and typeset the hint's wording there.
Without a hint (website uploads) Tesseract's reading is used.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass

from PIL import Image

from fontmatch.image.errors import EngineUnavailable

# One OpenMP thread per Tesseract process: requests run in parallel workers.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")
TESSERACT_TIMEOUT = 20  # seconds
MAX_CHARS = 40  # longest text typeset for comparison (see rank.MAX_CHARS)
MIN_HINT_RATIO = 0.6
_PAD_FRACTION = 0.25


@dataclass(frozen=True)
class Line:
    box: tuple[int, int, int, int]  # left, top, right, bottom
    text: str
    conf: float
    words: tuple = ()  # ((box, text), ...) in reading order

    @property
    def height(self) -> int:
        return self.box[3] - self.box[1]


@dataclass(frozen=True)
class Located:
    crop: Image.Image
    transcript: str
    source: str  # "hint" or "ocr"
    box: tuple[int, int, int, int]  # text box in the original image
    ink_box: tuple[int, int, int, int] | None = None  # same box in crop coordinates


def find_lines(img: Image.Image) -> list[Line]:
    import pytesseract

    try:
        data = pytesseract.image_to_data(
            img, config="--psm 3", output_type=pytesseract.Output.DICT, timeout=TESSERACT_TIMEOUT
        )
    except (pytesseract.TesseractError, RuntimeError, OSError) as exc:
        # TesseractError, timeout (RuntimeError), TesseractNotFoundError (OSError)
        raise EngineUnavailable("Text recognition failed, please try again.") from exc
    groups: dict[tuple, list[int]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip() or float(data["conf"][i]) < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(i)
    lines = []
    for idxs in groups.values():
        left = min(data["left"][i] for i in idxs)
        top = min(data["top"][i] for i in idxs)
        right = max(data["left"][i] + data["width"][i] for i in idxs)
        bottom = max(data["top"][i] + data["height"][i] for i in idxs)
        text = " ".join(data["text"][i] for i in idxs)
        conf = sum(float(data["conf"][i]) for i in idxs) / len(idxs)
        words = tuple(
            (
                (
                    data["left"][i],
                    data["top"][i],
                    data["left"][i] + data["width"][i],
                    data["top"][i] + data["height"][i],
                ),
                data["text"][i],
            )
            for i in idxs
        )
        lines.append(Line(box=(left, top, right, bottom), text=text, conf=conf, words=words))
    return lines


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", s.lower()).strip()


def _match(hint: str, line_text: str) -> tuple[float, bool]:
    """(similarity, partial). partial=True when the hint only matches part of
    the line (hint "Grand Hotel" vs OCR "Welcome to the Grand Hotel Budapest")."""
    a, b = _norm(hint), _norm(line_text)
    if not a or not b:
        return 0.0, False
    full = difflib.SequenceMatcher(None, a, b).ratio()
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(long_) - len(short) <= 2:
        return full, False
    best = max(
        difflib.SequenceMatcher(None, short, long_[i : i + len(short)]).ratio()
        for i in range(0, len(long_) - len(short) + 1)
    )
    best *= 0.95  # a substring hit counts slightly less than a whole-line match
    return (best, True) if best > full else (full, False)


def _ratio(a: str, b: str) -> float:
    return _match(a, b)[0]


_QUOTES = "\"'“”‘’«»`"


def _segments(hint: str) -> list[str]:
    parts = (s.strip().strip(_QUOTES).strip() for s in re.split(r"[\n\r|/]+", hint))
    return [p for p in parts if p]


def _word_window(line: Line, transcript: str) -> tuple[str, tuple[int, int, int, int]]:
    """Trim a long line to its first words (<= MAX_CHARS) and their box, so the
    typeset text and the image crop cover exactly the same words."""
    if len(transcript) <= MAX_CHARS or not line.words:
        return transcript, line.box
    words = transcript.split()
    if len(words) != len(line.words):
        words = [w for _, w in line.words]  # OCR words align with OCR boxes
    kept, length = [], 0
    for w in words:
        if kept and length + 1 + len(w) > MAX_CHARS:
            break
        kept.append(w)
        length += len(w) + (1 if len(kept) > 1 else 0)
    boxes = [b for b, _ in line.words[: len(kept)]]
    box = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    return " ".join(kept), box


def _prominence(line: Line) -> float:
    return line.height * min(len(line.text), 20) * max(line.conf, 1.0)


def choose_line(lines: list[Line], hint: str) -> Line | None:
    if not lines:
        return None
    if hint.strip():
        best = max(
            ((max(_ratio(seg, ln.text) for seg in _segments(hint)), ln) for ln in lines),
            key=lambda t: (t[0], _prominence(t[1])),
        )
        if best[0] >= MIN_HINT_RATIO:
            return best[1]
    return max(lines, key=_prominence)


def _best_segment(hint: str, line_text: str) -> tuple[str, float, bool]:
    segs = _segments(hint)
    if not segs:
        return "", 0.0, False
    seg = max(segs, key=lambda s: _ratio(s, line_text))
    score, partial = _match(seg, line_text)
    return seg, score, partial


def _padded_crop(img: Image.Image, box: tuple[int, int, int, int]):
    """Crop with padding; returns (crop, (dx, dy)) where (dx, dy) is the crop's
    offset in the original image."""
    left, top, right, bottom = box
    pad = int((bottom - top) * _PAD_FRACTION) + 2
    x0, y0 = max(0, left - pad), max(0, top - pad)
    crop = img.crop((x0, y0, min(img.width, right + pad), min(img.height, bottom + pad)))
    return crop, (x0, y0)


STACK_MAX_HEIGHT = 600  # line detection runs on a downscaled copy
MIN_LINE_FRACTION = 0.3  # ink bands shorter than this x the tallest are accents/noise
MAX_STACKED_LINES = 6  # more bands than this is a paragraph or a picture, not a sign
MAX_SPLIT_COST = 0.05  # sum of squared share errors; worse means words don't fit the bands


def split_words_by_widths(words: list[str], widths: list[float]) -> list[list[str]] | None:
    """Split words (or hint segments), in reading order, into one non-empty
    group per line so each group's share of the characters is closest to its
    line's share of the ink width. None when there are fewer words than lines,
    too many lines to search, or no split fits."""
    k = len(widths)
    if len(words) < k or k == 0 or k > MAX_STACKED_LINES or len(words) > 40:
        return None
    from itertools import combinations

    total_w = float(sum(widths)) or 1.0
    best, best_cost = None, float("inf")
    for cuts in combinations(range(1, len(words)), k - 1):
        groups = [words[a:b] for a, b in zip((0, *cuts), (*cuts, len(words)))]
        chars = [len(" ".join(g)) for g in groups]
        total_c = float(sum(chars))
        cost = sum((c / total_c - w / total_w) ** 2 for c, w in zip(chars, widths))
        if cost < best_cost:
            best, best_cost = groups, cost
    return best if best_cost <= MAX_SPLIT_COST else None


def _stacked_line(
    img: Image.Image, units: list[str]
) -> tuple[str, tuple[int, int, int, int]] | None:
    """(transcript, box) of the tallest ink line when the image holds several
    stacked lines of text; None for a single line."""
    import numpy as np
    from scipy import ndimage

    from fontmatch.image.prep import ink_map

    scale = min(1.0, STACK_MAX_HEIGHT / img.height)
    small = img if scale == 1.0 else img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    )
    ink = ink_map(small) > 0.3
    rows = ink.mean(axis=1) > 0.01
    labels, n = ndimage.label(rows)
    if n < 2:
        return None
    bands = [s[0] for s in ndimage.find_objects(labels)]
    tallest = max(b.stop - b.start for b in bands)
    bands = [b for b in bands if b.stop - b.start >= MIN_LINE_FRACTION * tallest]
    if len(bands) < 2:
        return None
    extents = []
    for b in bands:
        cols = np.flatnonzero(ink[b].any(axis=0))
        extents.append((cols[0], cols[-1] + 1))
    groups = split_words_by_widths(units, [right - left for left, right in extents])
    if groups is None:
        return None
    def size(j):
        return bands[j].stop - bands[j].start, extents[j][1] - extents[j][0]

    i = max(range(len(bands)), key=size)
    left, right = extents[i]
    box = (
        int(left / scale),
        int(bands[i].start / scale),
        min(img.width, int(np.ceil(right / scale))),
        min(img.height, int(np.ceil(bands[i].stop / scale))),
    )
    return " ".join(groups[i]), box


def locate(img: Image.Image, hint: str = "") -> Located | None:
    lines = find_lines(img)
    line = choose_line(lines, hint)
    if line is None:
        segs = _segments(hint)
        if not segs:
            return None
        # OCR saw nothing (stylised text?) but ChatGPT read something. Signs
        # often stack the words ("PHONE / FOR / TRUCKS"): match the most
        # prominent ink line with its share of the hint's words. Otherwise
        # assume the image is essentially that one line.
        # Segments (line breaks, " / ") are the hint's own lines; else words.
        stacked = _stacked_line(img, segs if len(segs) > 1 else segs[0].split())
        if stacked is not None:
            transcript, box = stacked
            crop, (dx, dy) = _padded_crop(img, box)
            ink_box = (box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy)
            return Located(
                crop=crop, transcript=transcript, source="hint", box=box, ink_box=ink_box
            )
        full = (0, 0, img.width, img.height)
        return Located(crop=img, transcript=segs[0], source="hint", box=full, ink_box=None)

    transcript, source = line.text, "ocr"
    if hint.strip():
        seg, ratio, partial = _best_segment(hint, line.text)
        if ratio >= MIN_HINT_RATIO and partial:
            # The hint is part of this line and OCR read it well: OCR's text
            # covers exactly what the crop shows.
            transcript, source = line.text, "ocr"
        elif ratio >= MIN_HINT_RATIO:
            transcript, source = seg, "hint"
        else:
            # Trust ChatGPT's reading over Tesseract's: on script/display fonts
            # OCR returns garbage. choose_line gave the most prominent line.
            transcript, source = _segments(hint)[0], "hint"
    transcript, box = _word_window(line, transcript)
    crop, (dx, dy) = _padded_crop(img, box)
    ink_box = (box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy)
    return Located(crop=crop, transcript=transcript, source=source, box=box, ink_box=ink_box)
