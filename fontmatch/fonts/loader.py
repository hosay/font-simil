"""Load and validate font files across TTF/OTF/WOFF/WOFF2/TTC formats."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from fontTools.ttLib import TTFont


class UnsupportedFontError(Exception):
    """Raised when a file cannot be parsed as a valid font."""


@dataclass(frozen=True)
class LoadedFont:
    """A successfully loaded and validated font face."""

    tt: TTFont
    family: str
    subfamily: str
    postscript_name: str
    file_hash: str

    def __repr__(self) -> str:
        return f"LoadedFont({self.family} {self.subfamily}, hash={self.file_hash[:12]}...)"


def _name_entry(tt: TTFont, name_id: int) -> str:
    """Extract a name table entry, preferring platform 3 (Windows) encoding."""
    name_table = tt["name"]
    record = name_table.getName(name_id, 3, 1, 0x0409)
    if record is None:
        record = name_table.getName(name_id, 1, 0, 0)
    if record is None:
        return ""
    return str(record)


def load(source: Union[str, Path, bytes], *, index: int = 0) -> LoadedFont:
    """Load a font from a file path or raw bytes.

    Handles TTF, OTF, WOFF, WOFF2, and TTC (via the index parameter).
    """
    if isinstance(source, bytes):
        raw_bytes = source
        file_obj = io.BytesIO(raw_bytes)
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Font file not found: {path}")
        raw_bytes = path.read_bytes()
        file_obj = io.BytesIO(raw_bytes)

    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    try:
        tt = TTFont(file_obj, fontNumber=index)
    except Exception as exc:
        raise UnsupportedFontError(f"Cannot parse font: {exc}") from exc

    family = _name_entry(tt, 1)  # nameID 1 = Font Family
    subfamily = _name_entry(tt, 2)  # nameID 2 = Font Subfamily
    postscript_name = _name_entry(tt, 6)  # nameID 6 = PostScript Name

    if not family:
        raise UnsupportedFontError("Font has no family name in the name table")

    return LoadedFont(
        tt=tt,
        family=family,
        subfamily=subfamily,
        postscript_name=postscript_name,
        file_hash=file_hash,
    )
