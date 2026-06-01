"""Phase 10 — Agent skill packaging tests."""

import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


class TestSkillCLI:
    def _build_test_db(self, tmp_path):
        """Build a small test database for skill testing."""
        from fontmatch.features.fingerprint import fingerprint
        from fontmatch.fonts import load
        from fontmatch.index.store import FontStore

        db_path = tmp_path / "test.db"
        store = FontStore(db_path)
        for name in ["Roboto-Regular.ttf", "Arimo-Regular.ttf", "Tinos-Regular.ttf"]:
            font = load(FIXTURES / name)
            fp = fingerprint(font)
            store.store_fingerprint(name, fp, license_id="Apache-2.0")
        store.close()
        return db_path

    def test_skill_identifies_font(self, tmp_path):
        db_path = self._build_test_db(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "skill/identify_font.py",
                str(FIXTURES / "Cousine-Regular.ttf"),
                "--db", str(db_path),
                "--top", "2",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "Cousine" in result.stdout
        assert "Distance" in result.stdout

    def test_skill_json_output(self, tmp_path):
        import json

        db_path = self._build_test_db(tmp_path)
        result = subprocess.run(
            [
                sys.executable,
                "skill/identify_font.py",
                str(FIXTURES / "Cousine-Regular.ttf"),
                "--db", str(db_path),
                "--json",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_skill_rejects_nonexistent_file(self):
        result = subprocess.run(
            [
                sys.executable,
                "skill/identify_font.py",
                "/nonexistent/font.ttf",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()
