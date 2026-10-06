"""Fonts whose DB source is a bare file name ("AbhayaLibre-Regular.ttf") live
in a subdirectory of a corpus dir. The sample route resolves with walk=False,
so these 404'd for any ?style= that wasn't already rendered (106 fonts,
including Arimo and Carlito). Bare names now resolve through a cached index
of the corpus dirs. Also: the ChatGPT widget loads samples eagerly and has a
new template URI, so ChatGPT fetches the current template."""

from pathlib import Path

from fontmatch.web import api


class _Store:
    def __init__(self, source):
        self.source = source

    def get_font_source(self, name):
        return self.source


def test_bare_source_resolves_without_walk(tmp_path):
    font = tmp_path / "ofl" / "abhayalibre" / "AbhayaLibre-Regular.ttf"
    font.parent.mkdir(parents=True)
    font.write_bytes(b"x")
    api.corpus_file_index.cache_clear()
    got = api.resolve_font_file(_Store("AbhayaLibre-Regular.ttf"), [str(tmp_path)],
                                "AbhayaLibre-Regular.ttf", walk=False)  # fmt: skip
    assert got == font


def test_index_skips_git_and_stays_in_corpus(tmp_path):
    (tmp_path / ".git" / "x").mkdir(parents=True)
    (tmp_path / ".git" / "x" / "Hidden.ttf").write_bytes(b"x")
    api.corpus_file_index.cache_clear()
    store = _Store("Hidden.ttf")
    assert api.resolve_font_file(store, [str(tmp_path)], "Hidden.ttf", walk=False) is None


def test_widget_loads_samples_eagerly_and_has_new_uri():
    from fontmatch.mcp_server import server

    html = (Path(server.__file__).parent / "widget.html").read_text()
    assert 'loading = "lazy"' not in html
    assert server.WIDGET_URI != "ui://widget/dupefont-results-v1.html"
