"""Where candidate font files live (shared by the atlas builder, eval and app)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

SEARCH_DIRS = [
    ROOT / "google-fonts-repo",
    ROOT / "tests" / "fixtures",
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/ubuntu"),
    Path("/usr/share/fonts/truetype/freefont"),
    Path("/usr/share/fonts/opentype/linux-libertine"),
]
