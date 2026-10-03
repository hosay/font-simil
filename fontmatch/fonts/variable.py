"""Variable-font named instances, read with fontTools.

Pillow's ``get_variation_names()`` segfaults on some fonts (e.g. Jaro[opsz]),
so instance names and coordinates come from the ``fvar`` table and are
applied with ``set_variation_by_axes``.
"""

from __future__ import annotations

from fontTools.ttLib import TTFont


def named_instances(tt: TTFont) -> list[tuple[str, dict[str, float]]]:
    if "fvar" not in tt:
        return []
    name_table = tt["name"]
    out = []
    for inst in tt["fvar"].instances:
        name = name_table.getDebugName(inst.subfamilyNameID) or ""
        out.append((name.strip(), dict(inst.coordinates)))
    return out


def _distance_from_default(tt: TTFont, coords: dict[str, float]) -> float:
    total = 0.0
    for axis in tt["fvar"].axes:
        span = (axis.maxValue - axis.minValue) or 1.0
        total += abs(coords.get(axis.axisTag, axis.defaultValue) - axis.defaultValue) / span
    return total


def choose_instance(tt: TTFont, wanted: list[str]) -> tuple[str, dict[str, float]] | None:
    """First wanted name that exists; among duplicates (e.g. one 'Regular' per
    optical size) the instance closest to the default coordinates."""
    instances = named_instances(tt)
    for name in wanted:
        matches = [inst for inst in instances if inst[0] == name]
        if matches:
            return min(matches, key=lambda inst: _distance_from_default(tt, inst[1]))
    return None


def axis_values(tt: TTFont, coords: dict[str, float]) -> list[float]:
    """Axis values in fvar order (the order Pillow/FreeType expects)."""
    return [coords.get(axis.axisTag, axis.defaultValue) for axis in tt["fvar"].axes]


def apply_instance(pil_face, tt: TTFont, coords: dict[str, float]) -> None:
    pil_face.set_variation_by_axes(axis_values(tt, coords))
