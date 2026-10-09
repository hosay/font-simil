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


def _stacked(words, size_px=64):
    """Words on separate lines (a sign: "PHONE / FOR / TRUCKS")."""
    parts = [render_text_image(TINOS, w, size_px=size_px) for w in words]
    canvas = Image.new("RGB", (max(p.width for p in parts) + 40, sum(p.height for p in parts) + 40), "white")
    y = 20
    for p in parts:
        canvas.paste(p, (20, y))
        y += p.height
    return canvas


def test_stacked_lines_without_ocr_pick_one_line_and_its_words(monkeypatch):
    import fontmatch.image.locate as loc

    monkeypatch.setattr(loc, "find_lines", lambda im: [])
    img = _stacked(["PHONE", "FOR", "TRUCKS"])
    got = locate(img, hint="PHONE FOR TRUCKS")
    assert got.transcript in ("PHONE", "TRUCKS")  # a wide line, never all three words
    assert got.box[3] - got.box[1] < img.height / 2


def test_hint_words_follow_line_widths():
    from fontmatch.image.locate import split_words_by_widths

    assert split_words_by_widths(["PHONE", "FOR", "TRUCKS"], [5, 3, 6]) == [
        ["PHONE"], ["FOR"], ["TRUCKS"]]
    assert split_words_by_widths("Please Stay on Trails and Paved Areas".split(), [21, 15]) == [
        ["Please", "Stay", "on", "Trails"], ["and", "Paved", "Areas"]]
    assert split_words_by_widths(["ONE"], [3, 3]) is None  # fewer words than lines


def test_single_line_without_ocr_still_uses_whole_hint(monkeypatch):
    import fontmatch.image.locate as loc

    monkeypatch.setattr(loc, "find_lines", lambda im: [])
    img = render_text_image(TINOS, "Harbor View Hotel", size_px=48)
    assert locate(img, hint="Harbor View Hotel").transcript == "Harbor View Hotel"


def test_line_broken_hint_uses_its_segments(monkeypatch):
    import fontmatch.image.locate as loc

    monkeypatch.setattr(loc, "find_lines", lambda im: [])
    img = _stacked(["PHONE", "FOR", "TRUCKS"])
    assert locate(img, hint="PHONE / FOR / TRUCKS").transcript in ("PHONE", "TRUCKS")


def test_word_split_is_bounded_and_rejects_bad_fits():
    from fontmatch.image.locate import split_words_by_widths

    words = ("lorem ipsum dolor sit amet " * 8).split()
    assert split_words_by_widths(words, [10] * 15) is None  # too many bands: no search
    # one long word cannot fill a wide band while three short ones fill a tiny one
    assert split_words_by_widths(["A", "B", "C", "Extraordinarily"], [100, 2]) is None
