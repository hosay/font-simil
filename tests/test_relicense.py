"""Licence from the OpenType name table (IDs 13/14), and the backfill CLI.

134 corpus rows were stored with license_id='unknown' because no OFL.txt /
LICENSE file sat next to the font, although the font itself declares its
licence in the name table (Liberation, Lato, Noto, Ubuntu, DejaVu, FreeFont).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from fontmatch.index.ingest import license_from_name_table
from fontmatch.index.relicense import main, relicense_unknown
from fontmatch.index.store import FontStore

FIXTURES = Path(__file__).parent / "fixtures"
OFL_URL = "http://scripts.sil.org/OFL"
GPL_FE = "GPL-3.0-or-later WITH Font-exception-2.0"


def make_font(path: Path, *, text: str | None = None, url: str | None = None) -> Path:
    """Write a minimal TTF whose name table carries the given licence strings."""
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder([".notdef"])
    fb.setupCharacterMap({})
    fb.setupGlyf({".notdef": TTGlyphPen(None).glyph()})
    fb.setupHorizontalMetrics({".notdef": (500, 0)})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    names = {"familyName": path.stem, "styleName": "Regular"}
    if text is not None:
        names["licenseDescription"] = text
    if url is not None:
        names["licenseInfoURL"] = url
    fb.setupNameTable(names)
    fb.setupOS2()
    fb.setupPost()
    path.parent.mkdir(parents=True, exist_ok=True)
    fb.save(str(path))
    return path


# --- license_from_name_table -----------------------------------------------------


@pytest.mark.parametrize(
    "text, url, expected",
    [
        # Liberation / Noto
        ("Licensed under the SIL Open Font License, Version 1.1", OFL_URL, "OFL-1.1"),
        # Lato: text is only a copyright notice, the URL carries the licence
        ("Copyright (c) 2011-2015 by tyPoland Lukasz Dziedzic", OFL_URL, "OFL-1.1"),
        # Linux Libertine: dual GPL AND OFL, OFL wins
        (
            "GPL- General Public License AND OFL-Open Font License",
            "http://www.fsf.org/licenses/gpl.html AND http://scripts.sil.org/OFL",
            "OFL-1.1",
        ),
        (
            "Licensed under the Apache License, Version 2.0",
            "apache.org/licenses/LICENSE-2.0",
            "Apache-2.0",
        ),
        (
            "Licensed under the Ubuntu Font Licence 1.0.",
            "ubuntu.com/legal/font-licence",
            "UFL-1.0",
        ),
        ("Licensed under the ubuntu font license 1.0", None, "UFL-1.0"),
        ("This font is released under the MIT License.", None, "MIT"),
        # DejaVu: Bitstream in the text, dejavu in the URL
        (
            "Fonts are (c) Bitstream (see below). DejaVu changes are in public domain.",
            "http://dejavu.sourceforge.net/wiki/index.php/License",
            "Bitstream-Vera",
        ),
        ("Public domain changes", "https://dejavu-fonts.github.io/License.html", "Bitstream-Vera"),
        ("Bitstream Vera Fonts Copyright (c) 2003 by Bitstream, Inc.", None, "Bitstream-Vera"),
        # Bitstream Charter carries a different licence: "Bitstream" alone is not enough
        ("(c) Copyright 1989-1992, Bitstream Inc., Cambridge, MA.", None, None),
        # GNU FreeFont
        (
            "This computer font is part of GNU FreeFont. It is free software",
            "http://www.gnu.org/copyleft/gpl.html",
            GPL_FE,
        ),
        ("Free software", "https://www.gnu.org/licenses/gpl-3.0.html", GPL_FE),
        # Nothing recognisable, or close-but-wrong
        ("All rights reserved.", "https://example.com", None),
        ("We permit licensees to embed this font.", None, None),
        ("Free software", "https://www.gnu.org/licenses/lgpl-2.1.html", None),
        (None, None, None),
    ],
)
def test_maps_name_table_strings_to_spdx_ids(tmp_path, text, url, expected):
    font = make_font(tmp_path / "Test-Regular.ttf", text=text, url=url)
    assert license_from_name_table(font) == expected


def test_returns_none_for_corrupt_and_missing_files(tmp_path):
    assert license_from_name_table(FIXTURES / "corrupt.ttf") is None
    assert license_from_name_table(tmp_path / "nope.ttf") is None
    not_a_font = tmp_path / "text.ttf"
    not_a_font.write_text("hello")
    assert license_from_name_table(not_a_font) is None


def test_reads_real_fixture_fonts():
    assert license_from_name_table(FIXTURES / "LiberationSans-Regular.ttf") == "OFL-1.1"
    assert license_from_name_table(FIXTURES / "Lato-Regular.ttf") == "OFL-1.1"
    assert license_from_name_table(FIXTURES / "DejaVuSans.ttf") == "Bitstream-Vera"


# --- ingest_corpus fallback -------------------------------------------------------


def test_ingest_falls_back_to_the_name_table_when_no_licence_file(tmp_path):
    from fontmatch.index.ingest import ingest_corpus

    corpus = tmp_path / "corpus"
    (corpus / "liberation").mkdir(parents=True)
    shutil.copy(FIXTURES / "LiberationSans-Regular.ttf", corpus / "liberation")
    store = FontStore(tmp_path / "t.db")

    assert ingest_corpus(corpus, store) == 1
    row = store.conn.execute("SELECT license_id FROM fonts").fetchone()
    assert row["license_id"] == "OFL-1.1"


def test_ingest_keeps_the_licence_file_when_present(tmp_path):
    """A LICENSE file next to the font still wins over the name table."""
    from fontmatch.index.ingest import ingest_corpus

    corpus = tmp_path / "corpus"
    (corpus / "fam").mkdir(parents=True)
    shutil.copy(FIXTURES / "LiberationSans-Regular.ttf", corpus / "fam")
    (corpus / "fam" / "LICENSE.txt").write_text("Apache License 2.0")
    store = FontStore(tmp_path / "t.db")

    ingest_corpus(corpus, store)
    row = store.conn.execute("SELECT license_id FROM fonts").fetchone()
    assert row["license_id"] == "Apache-2.0"


# --- relicense_unknown -------------------------------------------------------------


def _insert_font(
    conn: sqlite3.Connection,
    name: str,
    source: str,
    license_id: str = "unknown",
    file: Path | None = None,
):
    file_hash = hashlib.sha256(file.read_bytes()).hexdigest() if file else f"hash-{name}"
    conn.execute(
        """INSERT INTO fonts (file_hash, name, family, subfamily, license_id, source)
           VALUES (?, ?, ?, 'Regular', ?, ?)""",
        (file_hash, name, name.split("-")[0], license_id, source),
    )
    conn.commit()


@pytest.fixture
def db_and_fonts(tmp_path):
    db_path = tmp_path / "t.db"
    store = FontStore(db_path)
    fonts = tmp_path / "fonts"
    lib = make_font(
        fonts / "liberation" / "Lib-Regular.ttf",
        text="Licensed under the SIL Open Font License, Version 1.1",
        url="http://scripts.sil.org/OFL",
    )
    deja = make_font(
        fonts / "dejavu" / "Deja-Sans.ttf",
        text="Fonts are (c) Bitstream (see below). DejaVu changes are in public domain.",
    )
    mystery = make_font(fonts / "mystery" / "Mystery-Regular.ttf", text="All rights reserved")
    conn = store.conn
    _insert_font(conn, "Lib-Regular.ttf", "liberation/Lib-Regular.ttf", file=lib)
    # Source is a bare basename (rows ingested with the file at the corpus root)
    _insert_font(conn, "Deja-Sans.ttf", "Deja-Sans.ttf", file=deja)
    _insert_font(conn, "Mystery-Regular.ttf", "mystery/Mystery-Regular.ttf", file=mystery)
    _insert_font(conn, "Gone-Regular.ttf", "gone/Gone-Regular.ttf")
    _insert_font(conn, "Known-Regular.ttf", "ofl/known/Known-Regular.ttf", license_id="OFL-1.1")
    return db_path, store, fonts


def _licenses(store: FontStore) -> dict[str, str]:
    rows = store.conn.execute("SELECT name, license_id FROM fonts").fetchall()
    return {r["name"]: r["license_id"] for r in rows}


def test_relicense_updates_rows_whose_file_declares_a_licence(db_and_fonts):
    db_path, store, fonts = db_and_fonts

    counts = relicense_unknown(store, [fonts])

    assert counts == {
        "updated": 2,
        "still_unknown": 1,
        "not_found": 1,
        "hash_mismatch": 0,
        "cache_purged": 0,
    }
    assert _licenses(store) == {
        "Lib-Regular.ttf": "OFL-1.1",
        "Deja-Sans.ttf": "Bitstream-Vera",
        "Mystery-Regular.ttf": "unknown",
        "Gone-Regular.ttf": "unknown",
        "Known-Regular.ttf": "OFL-1.1",
    }


def test_relicense_accepts_a_db_path_and_several_search_dirs(db_and_fonts):
    db_path, store, fonts = db_and_fonts

    counts = relicense_unknown(db_path, [fonts / "liberation", fonts / "dejavu"])

    assert counts["updated"] == 2
    assert (
        FontStore(db_path)
        .conn.execute("SELECT license_id FROM fonts WHERE name = 'Lib-Regular.ttf'")
        .fetchone()[0]
        == "OFL-1.1"
    )


def test_relicense_purges_cached_results_that_embed_the_old_licence(db_and_fonts):
    from fontmatch.features.perceptual import FINGERPRINT_SCHEMA_VERSION

    db_path, store, fonts = db_and_fonts
    stale = [{"name": "Lib-Regular.ttf", "family": "Lib", "license_id": "unknown"}]
    fresh = [{"name": "Known-Regular.ttf", "family": "Known", "license_id": "OFL-1.1"}]
    store.cache_result("query-a", FINGERPRINT_SCHEMA_VERSION, stale)
    store.cache_result("query-b", FINGERPRINT_SCHEMA_VERSION, fresh)

    store.cache_result("query-c", FINGERPRINT_SCHEMA_VERSION + 1, stale)  # other schema
    store.cache_result("img:x", 9, [{"matches": stale}])  # image result shape
    store.conn.execute(
        "INSERT INTO match_cache (query_hash, schema_version, result_json) VALUES (?, ?, ?)",
        ("query-d", FINGERPRINT_SCHEMA_VERSION, "5"),
    )
    store.conn.commit()

    counts = relicense_unknown(store, [fonts])

    assert counts["cache_purged"] == 1
    assert store.get_cached_result("query-a", FINGERPRINT_SCHEMA_VERSION) is None
    assert store.get_cached_result("query-b", FINGERPRINT_SCHEMA_VERSION) == fresh
    assert store.get_cached_result("query-c", FINGERPRINT_SCHEMA_VERSION + 1) == stale
    assert store.get_cached_result("img:x", 9) is not None
    assert store.get_cached_result("query-d", FINGERPRINT_SCHEMA_VERSION) == 5


def test_relicense_never_trusts_a_same_named_file_with_different_bytes(db_and_fonts):
    """Another version of LiberationSans-Regular.ttf in a second directory must
    not relicense the row that was fingerprinted from different bytes."""
    db_path, store, fonts = db_and_fonts
    other = make_font(
        fonts.parent / "other" / "Lib-Regular.ttf",
        text="Licensed under the Apache License, Version 2.0",
    )

    counts = relicense_unknown(store, [other.parent])

    assert counts["hash_mismatch"] == 1
    assert counts["updated"] == 0
    assert _licenses(store)["Lib-Regular.ttf"] == "unknown"


def test_relicense_tries_every_same_named_file_until_the_hash_matches(tmp_path):
    """Walk order puts a stale copy first; the row's own bytes sit in a later dir."""
    db_path = tmp_path / "t.db"
    store = FontStore(db_path)
    make_font(tmp_path / "fonts" / "a-old" / "Dup-Regular.ttf", text="Apache License")
    right = make_font(tmp_path / "fonts" / "b-new" / "Dup-Regular.ttf", url="scripts.sil.org/OFL")
    _insert_font(store.conn, "Dup-Regular.ttf", "Dup-Regular.ttf", file=right)

    counts = relicense_unknown(store, [tmp_path / "fonts"])

    assert counts["updated"] == 1 and counts["hash_mismatch"] == 0
    assert _licenses(store)["Dup-Regular.ttf"] == "OFL-1.1"


def test_relicense_counts_unreadable_files_as_not_found_and_carries_on(db_and_fonts):
    db_path, store, fonts = db_and_fonts
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    (fonts / "liberation" / "Lib-Regular.ttf").chmod(0)
    try:
        counts = relicense_unknown(store, [fonts])
    finally:
        (fonts / "liberation" / "Lib-Regular.ttf").chmod(0o644)

    assert counts["updated"] == 1  # Deja-Sans still done
    assert counts["not_found"] == 2
    assert _licenses(store)["Lib-Regular.ttf"] == "unknown"


def test_relicense_never_resolves_a_source_outside_the_search_dirs(tmp_path):
    db_path = tmp_path / "t.db"
    store = FontStore(db_path)
    esc = make_font(tmp_path / "outside" / "Esc-Regular.ttf", url="scripts.sil.org/OFL")
    dot = make_font(tmp_path / "outside" / "Dot-Regular.ttf", url="scripts.sil.org/OFL")
    _insert_font(store.conn, "Esc-Regular.ttf", str(esc), file=esc)
    _insert_font(store.conn, "Dot-Regular.ttf", "../outside/Dot-Regular.ttf", file=dot)
    (tmp_path / "fonts").mkdir()

    counts = relicense_unknown(store, [tmp_path / "fonts"])

    assert counts["not_found"] == 2 and counts["updated"] == 0


def test_relicense_prefers_the_exact_source_path_over_a_basename_match(tmp_path):
    db_path = tmp_path / "t.db"
    store = FontStore(db_path)
    right = make_font(tmp_path / "fonts" / "ofl" / "Dup-Regular.ttf", url="scripts.sil.org/OFL")
    make_font(tmp_path / "fonts" / "apache" / "Dup-Regular.ttf", text="Apache License")
    _insert_font(store.conn, "Dup-Regular.ttf", "ofl/Dup-Regular.ttf", file=right)

    counts = relicense_unknown(store, [tmp_path / "fonts"])

    assert counts["updated"] == 1
    assert _licenses(store)["Dup-Regular.ttf"] == "OFL-1.1"


def test_relicense_is_idempotent(db_and_fonts):
    db_path, store, fonts = db_and_fonts
    relicense_unknown(store, [fonts])
    counts = relicense_unknown(store, [fonts])
    assert counts["updated"] == 0
    assert counts["still_unknown"] == 1


# --- CLI ---------------------------------------------------------------------------


def test_cli_relicenses_against_the_given_database(db_and_fonts, caplog):
    db_path, store, fonts = db_and_fonts
    caplog.set_level("INFO")

    rc = main(
        [
            "--db",
            str(db_path),
            "--font-dir",
            str(fonts / "liberation"),
            "--font-dir",
            str(fonts / "dejavu"),
        ]
    )

    assert rc == 0
    assert _licenses(FontStore(db_path))["Lib-Regular.ttf"] == "OFL-1.1"
    assert "updated" in caplog.text and "not found" in caplog.text


def test_cli_fails_cleanly_when_the_database_is_missing(tmp_path):
    missing = tmp_path / "missing.db"
    assert main(["--db", str(missing), "--font-dir", str(tmp_path)]) == 1
    assert not missing.exists()


def test_cli_requires_a_font_dir(db_and_fonts):
    db_path, _, _ = db_and_fonts
    with pytest.raises(SystemExit):
        main(["--db", str(db_path)])


# --- labels --------------------------------------------------------------------------


def test_new_licence_ids_have_human_labels():
    from fontmatch.web.helpers import license_label

    assert license_label("Bitstream-Vera") == "Bitstream Vera License"
    assert license_label(GPL_FE) == "GPL with font exception"
