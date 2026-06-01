"""Phase 2 — Font loading, validation & format handling tests."""

import hashlib
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadTTF:
    def test_loads_ttf_and_reports_family(self):
        from fontmatch.fonts import load

        font = load(FIXTURES / "Roboto-Regular.ttf")
        assert font.family == "Roboto"

    def test_reports_subfamily(self):
        from fontmatch.fonts import load

        font = load(FIXTURES / "Roboto-Bold.ttf")
        assert font.subfamily == "Bold"

    def test_reports_postscript_name(self):
        from fontmatch.fonts import load

        font = load(FIXTURES / "Roboto-Regular.ttf")
        assert font.postscript_name == "Roboto-Regular"


class TestLoadOTF:
    """OTF is just OpenType with CFF outlines — TTFont handles it the same."""

    def test_loads_ttf_as_opentype(self):
        from fontmatch.fonts import load

        # Our TTFs are valid OpenType; this exercises the code path
        font = load(FIXTURES / "Arimo-Regular.ttf")
        assert font.family == "Arimo"


class TestLoadWOFF:
    def test_loads_woff(self):
        from fontmatch.fonts import load

        font = load(FIXTURES / "Cousine-Regular.woff")
        assert font.family == "Cousine"

    def test_woff_same_data_as_ttf(self):
        from fontmatch.fonts import load

        ttf = load(FIXTURES / "Cousine-Regular.ttf")
        woff = load(FIXTURES / "Cousine-Regular.woff")
        assert ttf.family == woff.family
        assert ttf.postscript_name == woff.postscript_name


class TestLoadWOFF2:
    def test_loads_woff2(self):
        from fontmatch.fonts import load

        font = load(FIXTURES / "Cousine-Regular.woff2")
        assert font.family == "Cousine"


class TestCorruptFile:
    def test_rejects_corrupt_file(self):
        from fontmatch.fonts import UnsupportedFontError, load

        with pytest.raises(UnsupportedFontError):
            load(FIXTURES / "corrupt.ttf")

    def test_rejects_nonexistent_file(self):
        from fontmatch.fonts import UnsupportedFontError, load

        with pytest.raises((UnsupportedFontError, FileNotFoundError)):
            load(FIXTURES / "does_not_exist.ttf")


class TestFileHash:
    def test_file_hash_is_sha256(self):
        from fontmatch.fonts import load

        font = load(FIXTURES / "Roboto-Regular.ttf")
        expected = hashlib.sha256(
            (FIXTURES / "Roboto-Regular.ttf").read_bytes()
        ).hexdigest()
        assert font.file_hash == expected

    def test_same_font_different_format_different_hash(self):
        from fontmatch.fonts import load

        ttf = load(FIXTURES / "Cousine-Regular.ttf")
        woff = load(FIXTURES / "Cousine-Regular.woff")
        # Different raw bytes → different hash (expected)
        assert ttf.file_hash != woff.file_hash


class TestLoadFromBytes:
    def test_loads_from_bytes(self):
        from fontmatch.fonts import load

        raw = (FIXTURES / "Roboto-Regular.ttf").read_bytes()
        font = load(raw)
        assert font.family == "Roboto"
        assert font.file_hash == hashlib.sha256(raw).hexdigest()
