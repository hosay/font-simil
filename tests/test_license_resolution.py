"""Licence resolution for a font file, strongest evidence first: licence file
beside it, Google Fonts METADATA.pb, the font's own name table, a licence file
in a recognised licence folder one level up, and the owning Debian package's
copyright file. Exercised through ``resolve_license`` / ``resolve_license_across``
(ingest) and ``relicense_unknown`` (backfill)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fontmatch import licenses
from fontmatch.index import ingest
from fontmatch.index.ingest import LICENSE_FILENAMES, resolve_license, resolve_license_across
from fontmatch.index.relicense import relicense_unknown
from fontmatch.index.store import FontStore
from fontmatch.web.helpers import license_label
from tests.test_relicense import _insert_font, make_font

OFL_TEXT = "This Font Software is licensed under the SIL Open Font License, Version 1.1."
APACHE_TEXT = 'Licensed under the Apache License, Version 2.0 (the "License")'
UFL_TEXT = "------ UBUNTU FONT LICENCE Version 1.0 ------\nPREAMBLE\nThis licence allows"
LIBERATION_TEXT = (
    "Licensed under the Liberation Fonts license, see https://fedoraproject.org/wiki/Licensing"
)


def font_in(directory: Path, name: str = "Font-Regular.ttf", **names) -> Path:
    return make_font(directory / name, **names)


# --- licence files ------------------------------------------------------------------


def test_licence_filenames_are_checked_in_a_fixed_order():
    assert isinstance(LICENSE_FILENAMES, tuple)
    assert LICENSE_FILENAMES[0] == "OFL.txt"
    assert "UFL.txt" in LICENSE_FILENAMES and "LICENCE.txt" in LICENSE_FILENAMES


def test_a_licence_file_without_keywords_does_not_stop_the_search(tmp_path):
    # A README-ish LICENSE.txt must not shadow the OFL.txt beside it, whatever
    # order the filenames are tried in.
    (tmp_path / "LICENSE.txt").write_text("See OFL.txt for terms.\n")
    (tmp_path / "OFL.txt").write_text(OFL_TEXT)
    assert resolve_license(font_in(tmp_path)) == "OFL-1.1"


def test_ubuntu_font_licence_text_is_recognised(tmp_path):
    (tmp_path / "LICENCE.txt").write_text(UFL_TEXT)
    assert resolve_license(font_in(tmp_path)) == "UFL-1.0"


def test_a_long_copyright_header_does_not_hide_the_licence(tmp_path):
    # Multi-author OFL.txt files (Noto) carry several KB of Reserved Font Name
    # lines before the licence sentence.
    header = "".join(
        f"Copyright 2020 Author {i} with Reserved Font Name Name{i}\n" for i in range(80)
    )
    assert len(header) > 3000
    (tmp_path / "OFL.txt").write_text(header + OFL_TEXT)
    assert resolve_license(font_in(tmp_path)) == "OFL-1.1"


def test_a_licence_file_beside_the_font_outranks_its_name_table(tmp_path):
    # Deliberate policy (see LICENSE_TIERS): the distributor's directory licence
    # wins over a lagging name table, e.g. families relicensed Apache -> OFL.
    (tmp_path / "OFL.txt").write_text(OFL_TEXT)
    assert resolve_license(font_in(tmp_path, text=APACHE_TEXT)) == "OFL-1.1"


# --- METADATA.pb -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected",
    [("APACHE2", "Apache-2.0"), ("OFL", "OFL-1.1"), ("UFL", "UFL-1.0")],
)
def test_metadata_pb_licence_is_used_when_no_licence_file(tmp_path, value, expected):
    (tmp_path / "METADATA.pb").write_text(
        f'name: "Thing"\ndesigner: "X"\nlicense: "{value}"\ncategory: "SERIF"\n'
    )
    assert resolve_license(font_in(tmp_path)) == expected


def test_metadata_pb_with_crlf_line_endings(tmp_path):
    (tmp_path / "METADATA.pb").write_bytes(b'name: "Thing"\r\nlicense: "OFL"\r\n')
    assert resolve_license(font_in(tmp_path)) == "OFL-1.1"


def test_metadata_pb_with_an_unknown_value_is_not_trusted(tmp_path):
    (tmp_path / "METADATA.pb").write_text('license: "PROPRIETARY"\n')
    assert resolve_license(font_in(tmp_path)) == "unknown"


def test_metadata_pb_licence_line_must_be_a_whole_line(tmp_path):
    (tmp_path / "METADATA.pb").write_text('description: "no license: \\"OFL\\" here"\n')
    assert resolve_license(font_in(tmp_path)) == "unknown"


def test_a_licence_file_beside_the_font_beats_metadata_pb(tmp_path):
    (tmp_path / "OFL.txt").write_text(OFL_TEXT)
    (tmp_path / "METADATA.pb").write_text('license: "APACHE2"\n')
    assert resolve_license(font_in(tmp_path)) == "OFL-1.1"


# --- name table --------------------------------------------------------------------


def test_liberation_one_x_name_table_maps_to_the_liberation_licence(tmp_path):
    font = font_in(tmp_path, text=LIBERATION_TEXT)
    assert resolve_license(font) == "LicenseRef-Liberation-1.0"


def test_name_table_is_used_when_the_directory_says_nothing(tmp_path):
    assert resolve_license(font_in(tmp_path, text=APACHE_TEXT)) == "Apache-2.0"


# --- parent directory ------------------------------------------------------------


def test_parent_licence_file_counts_only_in_a_recognised_licence_folder(tmp_path):
    # repo/LICENSE covers the code, not the fonts in repo/fonts/
    (tmp_path / "LICENSE.txt").write_text(APACHE_TEXT)
    flat = tmp_path / "fonts"
    flat.mkdir()
    assert resolve_license(font_in(flat)) == "unknown"
    # google/fonts style: apache/LICENSE.txt one level up is the family's licence
    apache = tmp_path / "apache"
    (apache / "family").mkdir(parents=True)
    (apache / "LICENSE.txt").write_text(APACHE_TEXT)
    assert resolve_license(font_in(apache / "family")) == "Apache-2.0"


def test_parent_licence_file_never_overrides_the_fonts_own_evidence(tmp_path):
    ofl = tmp_path / "ofl"
    family = ofl / "family"
    family.mkdir(parents=True)
    (ofl / "LICENSE.txt").write_text(APACHE_TEXT)  # wrong, on purpose
    (family / "METADATA.pb").write_text('license: "OFL"\n')
    assert resolve_license(font_in(family)) == "OFL-1.1"
    (family / "METADATA.pb").unlink()
    assert resolve_license(font_in(family, "B-Regular.ttf", text=OFL_TEXT)) == "OFL-1.1"


# --- Debian: dpkg ownership + DEP-5 copyright --------------------------------------


UBUNTU_COPYRIGHT = """Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Ubuntu Font Family

Files: *
Copyright: 2010-2011 Canonical Ltd.
License: Ubuntu-Font-Licence-1.0

Files: debian/*
Copyright: 2010 Someone
License: GPL-3
"""

LIBERATION2_COPYRIGHT = """Files: *
Copyright: Red Hat
License: SIL-OFL-1.1

Files: debian/*
License: GPL-2+
"""

NARROW_COPYRIGHT = """Files: *
Copyright: Red Hat
License: GPL-2 with Font exception
"""


class Debian:
    """A fake dpkg database: ``add(package, font_path, copyright_text)``."""

    def __init__(self, root: Path, monkeypatch):
        self.info = root / "var/lib/dpkg/info"
        self.doc = root / "usr/share/doc"
        self.info.mkdir(parents=True)
        self.doc.mkdir(parents=True)
        monkeypatch.setattr(ingest, "DPKG_INFO_DIR", self.info)
        monkeypatch.setattr(ingest, "DEBIAN_DOC_ROOT", self.doc)
        ingest._dpkg_font_files.cache_clear()

    def add(self, package: str, font: Path, copyright_text: str | None) -> None:
        with (self.info / f"{package}.list").open("a") as f:
            f.write(f"{font.resolve()}\n")
        if copyright_text is not None:
            (self.doc / package).mkdir(exist_ok=True)
            (self.doc / package / "copyright").write_text(copyright_text)
        ingest._dpkg_font_files.cache_clear()


@pytest.fixture
def debian(tmp_path, monkeypatch):
    return Debian(tmp_path, monkeypatch)


def test_system_font_with_empty_name_table_uses_the_owning_packages_copyright(tmp_path, debian):
    font_dir = tmp_path / "usr/share/fonts/truetype/ubuntu"
    font_dir.mkdir(parents=True)
    font = font_in(font_dir, "Ubuntu-B.ttf")
    debian.add("fonts-ubuntu", font, UBUNTU_COPYRIGHT)
    assert resolve_license(font) == "UFL-1.0"


def test_a_directory_shared_by_two_packages_is_resolved_per_file(tmp_path, debian):
    # /usr/share/fonts/truetype/liberation holds fonts-liberation (OFL) and
    # fonts-liberation-sans-narrow (GPL-2 with exceptions). The directory name
    # must not decide; the owning package does.
    font_dir = tmp_path / "usr/share/fonts/truetype/liberation"
    font_dir.mkdir(parents=True)
    sans = font_in(font_dir, "LiberationSans-Regular.ttf")
    narrow = font_in(font_dir, "LiberationSansNarrow-Regular.ttf")
    debian.add("fonts-liberation", sans, LIBERATION2_COPYRIGHT)
    debian.add("fonts-liberation-sans-narrow", narrow, NARROW_COPYRIGHT)
    assert resolve_license(sans) == "OFL-1.1"
    # an expression maps to nothing: better unknown than half right
    assert resolve_license(narrow) == "unknown"


def test_a_font_no_package_owns_stays_unknown(tmp_path, debian):
    owned = tmp_path / "usr/share/fonts/truetype/ubuntu"
    owned.mkdir(parents=True)
    debian.add("fonts-ubuntu", font_in(owned, "Other.ttf"), UBUNTU_COPYRIGHT)
    local = tmp_path / "home/me/.local/share/fonts/ubuntu"
    local.mkdir(parents=True)
    assert resolve_license(font_in(local, "Ubuntu-B.ttf")) == "unknown"


def test_debian_copyright_reads_only_the_files_star_stanza(tmp_path, debian):
    font_dir = tmp_path / "usr/share/fonts/truetype/ubuntu"
    font_dir.mkdir(parents=True)
    font = font_in(font_dir, "Ubuntu-B.ttf")
    text = UBUNTU_COPYRIGHT.replace("License: Ubuntu-Font-Licence-1.0", "License: Weird-1.0")
    debian.add("fonts-ubuntu", font, text)  # debian/* says GPL-3; must not leak
    assert resolve_license(font) == "unknown"


def test_debian_copyright_values_match_case_insensitively_and_crlf(tmp_path, debian):
    font_dir = tmp_path / "usr/share/fonts/truetype/dejavu"
    font_dir.mkdir(parents=True)
    font = font_in(font_dir, "DejaVuSans.ttf")
    debian.add(
        "fonts-dejavu-core",
        font,
        "Files: *\r\nCopyright: Bitstream\r\nLicense: Bitstream-Vera\r\n",
    )
    assert resolve_license(font) == "Bitstream-Vera"


def test_missing_copyright_file_is_simply_unknown(tmp_path, debian):
    font_dir = tmp_path / "usr/share/fonts/truetype/mystery"
    font_dir.mkdir(parents=True)
    font = font_in(font_dir)
    debian.add("fonts-mystery", font, None)
    assert resolve_license(font) == "unknown"


def test_symlinked_font_dir_is_matched_by_its_resolved_path(tmp_path, debian):
    real = tmp_path / "opt/fonts/ubuntu"
    real.mkdir(parents=True)
    font = font_in(real, "Ubuntu-B.ttf")
    link_dir = tmp_path / "usr/share/fonts/truetype"
    link_dir.mkdir(parents=True)
    (link_dir / "ubuntu").symlink_to(real)
    debian.add("fonts-ubuntu", font, UBUNTU_COPYRIGHT)
    assert resolve_license(link_dir / "ubuntu" / "Ubuntu-B.ttf") == "UFL-1.0"


# --- tiers across identical copies ---------------------------------------------------


def test_a_strong_tier_on_any_copy_beats_a_weak_tier_on_the_first(tmp_path):
    bare = tmp_path / "fixtures"
    bare.mkdir()
    first = font_in(bare, "Fam-Regular.ttf", text=APACHE_TEXT)  # name table only
    family = tmp_path / "ofl" / "fam"
    family.mkdir(parents=True)
    second = family / "Fam-Regular.ttf"
    second.write_bytes(first.read_bytes())
    (family / "OFL.txt").write_text(OFL_TEXT)
    assert resolve_license_across([first, second]) == "OFL-1.1"
    assert resolve_license_across([second, first]) == "OFL-1.1"


# --- labels ----------------------------------------------------------------------


def test_every_resolvable_licence_id_has_a_label():
    for license_id in licenses.ALL_LICENSE_IDS | {licenses.UNKNOWN}:
        label = license_label(license_id)
        assert label and label != license_id, license_id


def test_new_labels_are_distinguishable():
    assert license_label("LicenseRef-Liberation-1.0") == (
        "Liberation Fonts License (GPL 2.0 with exceptions)"
    )
    assert license_label("LicenseRef-Liberation-1.0") != license_label(
        "GPL-3.0-or-later WITH Font-exception-2.0"
    )


# --- relicense uses the same resolver ----------------------------------------------


def _store_with(tmp_path: Path, font: Path) -> Path:
    db = tmp_path / "f.db"
    store = FontStore(db)
    _insert_font(store.conn, font.name, font.name, file=font)
    store.close()
    return db


def test_relicense_resolves_from_metadata_pb_not_only_the_name_table(tmp_path):
    corpus = tmp_path / "corpus" / "apache" / "jsmathcmr10"
    corpus.mkdir(parents=True)
    (corpus / "METADATA.pb").write_text('license: "APACHE2"\n')
    font = font_in(corpus, "jsMath-cmr10.ttf")  # empty name table
    db = _store_with(tmp_path, font)
    counts = relicense_unknown(db, [tmp_path / "corpus"])
    assert counts["updated"] == 1 and counts["still_unknown"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT license_id FROM fonts").fetchone()[0] == "Apache-2.0"


def test_relicense_still_refuses_a_same_named_file_with_other_bytes(tmp_path):
    good = tmp_path / "good" / "fam"
    good.mkdir(parents=True)
    (good / "METADATA.pb").write_text('license: "OFL"\n')
    font = font_in(good, "Fam-Regular.ttf")
    db = _store_with(tmp_path, font)
    other = tmp_path / "other" / "fam"
    other.mkdir(parents=True)
    (other / "METADATA.pb").write_text('license: "OFL"\n')
    font_in(other, "Fam-Regular.ttf", text="different bytes")
    counts = relicense_unknown(db, [tmp_path / "other"])  # only the impostor is searchable
    assert counts["updated"] == 0 and counts["hash_mismatch"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT license_id FROM fonts").fetchone()[0] == "unknown"


def test_relicense_leaves_undeclared_fonts_unknown(tmp_path):
    lonely = tmp_path / "lonely"
    lonely.mkdir()
    font = font_in(lonely, "Ubuntu-B.ttf")  # ubuntu-ish name, no evidence anywhere
    db = _store_with(tmp_path, font)
    counts = relicense_unknown(db, [lonely])
    assert counts["updated"] == 0 and counts["still_unknown"] == 1


def test_relicense_uses_any_identical_copy_that_carries_evidence(tmp_path):
    # The same bytes sit in a bare fixtures folder (no evidence) and in the
    # google/fonts family folder (LICENCE.txt). Whichever copy is found first,
    # the row must be relicensed.
    bare = tmp_path / "fixtures"
    bare.mkdir()
    font = font_in(bare, "Ubuntu-Regular.ttf")
    family = tmp_path / "repo" / "ufl" / "ubuntu"
    family.mkdir(parents=True)
    (family / "Ubuntu-Regular.ttf").write_bytes(font.read_bytes())
    (family / "LICENCE.txt").write_text(UFL_TEXT)
    db = _store_with(tmp_path, font)
    counts = relicense_unknown(db, [bare, tmp_path / "repo"])
    assert counts["updated"] == 1 and counts["still_unknown"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT license_id FROM fonts").fetchone()[0] == "UFL-1.0"
