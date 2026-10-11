"""Licence identifiers used across ingest, backfill and the web layer.

One place for the SPDX ids we record, the strings that map onto them, and the
labels shown to users, so the resolvers and the UI cannot drift apart.
"""

from __future__ import annotations

OFL = "OFL-1.1"
APACHE = "Apache-2.0"
MIT = "MIT"
UFL = "UFL-1.0"
BITSTREAM_VERA = "Bitstream-Vera"
GPL_FONT_EXCEPTION = "GPL-3.0-or-later WITH Font-exception-2.0"
# Liberation 1.x (Liberation Sans Narrow): GPL 2 with the font-embedding exception
# plus Red Hat's extra clause for physical products, which no SPDX exception
# expresses exactly, hence a LicenseRef rather than a GPL expression.
LIBERATION = "LicenseRef-Liberation-1.0"
UNKNOWN = "unknown"

# google/fonts METADATA.pb ``license:`` values and the top-level directories.
GOOGLE_FONTS_LICENSES = {"OFL": OFL, "APACHE2": APACHE, "UFL": UFL}
GOOGLE_FONTS_DIRS = {"ofl": OFL, "apache": APACHE, "ufl": UFL}

# Debian DEP-5 ``License:`` short names seen in /usr/share/doc/fonts-*/copyright,
# lower-cased for matching. Expressions ("GPL-2+ with Font exception and OFL-1.1")
# are deliberately absent: a single id cannot represent them.
DEBIAN_COPYRIGHT_LICENSES = {
    "ubuntu-font-licence-1.0": UFL,
    "ofl-1.1": OFL,
    "ofl": OFL,
    "sil-ofl-1.1": OFL,
    "sil-1.1": OFL,
    "apache-2.0": APACHE,
    "apache-2": APACHE,
    "expat": MIT,
    "bitstream-vera": BITSTREAM_VERA,
}

LICENSE_LABELS = {
    OFL: "SIL Open Font License",
    APACHE: "Apache 2.0",
    MIT: "MIT License",
    UFL: "Ubuntu Font License",
    BITSTREAM_VERA: "Bitstream Vera License",
    GPL_FONT_EXCEPTION: "GPL with font exception",
    LIBERATION: "Liberation Fonts License (GPL 2.0 with exceptions)",
    UNKNOWN: "Unknown",
}

# Every id a resolver can return; tests check each has a label.
ALL_LICENSE_IDS = frozenset(
    {OFL, APACHE, MIT, UFL, BITSTREAM_VERA, GPL_FONT_EXCEPTION, LIBERATION}
    | set(GOOGLE_FONTS_LICENSES.values())
    | set(DEBIAN_COPYRIGHT_LICENSES.values())
)
