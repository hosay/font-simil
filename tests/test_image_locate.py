"""Finding the text line to match (Tesseract + ChatGPT's text_hint)."""

from pathlib import Path

from PIL import Image

from fontmatch.image.locate import Line, choose_line, find_lines, locate
from fontmatch.image.synth import render_text_image

FIXTURES = Path(__file__).parent / "fixtures"
ROBOTO = FIXTURES / "Roboto-Regular.ttf"
TINOS = FIXTURES / "Tinos-Regular.ttf"


def _poster():
    """Big title in Roboto over a small subtitle in Tinos."""
    title = render_text_image(ROBOTO, "Sunrise Bakery", size_px=72)
    sub = render_text_image(TINOS, "Fresh bread every morning", size_px=28)
    canvas = Image.new(
        "RGB", (max(title.width, sub.width) + 40, title.height + sub.height + 40), "white"
    )
    canvas.paste(title, (20, 10))
    canvas.paste(sub, (20, title.height + 20))
    return canvas, title.height


def test_find_lines_reads_text():
    lines = find_lines(render_text_image(ROBOTO, "Harbor View Hotel", size_px=48))
    assert any("Harbor" in ln.text for ln in lines)


def test_find_lines_blank_image():
    assert find_lines(Image.new("RGB", (200, 60), "white")) == []


def test_without_hint_the_biggest_line_wins():
    img, title_h = _poster()
    line = choose_line(find_lines(img), hint="")
    assert "Sunrise" in line.text
    assert line.box[3] <= title_h + 25


def test_hint_selects_matching_line():
    img, title_h = _poster()
    line = choose_line(find_lines(img), hint="Fresh bread every morning")
    assert "bread" in line.text
    assert line.box[1] >= title_h


def test_hint_with_several_lines_picks_best_segment():
    lines = [
        Line(box=(0, 0, 100, 30), text="SALE", conf=90.0),
        Line(box=(0, 40, 100, 60), text="today only", conf=90.0),
    ]
    line = choose_line(lines, hint="Big SALE\ntoday only!")
    assert line.text == "today only"


def test_locate_returns_crop_and_hint_transcript():
    img, _ = _poster()
    result = locate(img, hint="Sunrise Bakery")
    assert result.transcript == "Sunrise Bakery"
    assert result.crop.height < img.height


def test_hint_is_trusted_when_ocr_misreads(monkeypatch):
    """Stylised fonts: Tesseract reads garbage, ChatGPT's hint is right. Typeset
    the hint; use OCR only to find where the text is."""
    import fontmatch.image.locate as loc

    img, title_h = _poster()
    garbage = [
        Line(box=(20, 10, 400, title_h), text="jun] E3ic", conf=40.0),
        Line(box=(20, title_h + 20, 300, title_h + 50), text="Frsh brd evry mrng", conf=50.0),
    ]
    monkeypatch.setattr(loc, "find_lines", lambda im: garbage)
    result = loc.locate(img, hint="Sunrise Bakery")
    assert result.transcript == "Sunrise Bakery" and result.source == "hint"
    assert result.box == (20, 10, 400, title_h)  # most prominent line


def test_locate_uses_whole_image_when_ocr_finds_nothing_but_hint_given(monkeypatch):
    import fontmatch.image.locate as loc

    monkeypatch.setattr(loc, "find_lines", lambda img: [])
    img = render_text_image(ROBOTO, "Zyx", size_px=48)
    result = loc.locate(img, hint="Zyx")
    assert result.transcript == "Zyx" and result.crop.size == img.size


def test_locate_nothing_found():
    assert locate(Image.new("RGB", (200, 60), "white"), hint="") is None
