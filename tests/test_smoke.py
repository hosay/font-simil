"""Smoke tests — Phase 1 scaffold acceptance."""


def test_package_imports():
    """fontmatch package and all subpackages are importable."""
    import fontmatch
    import fontmatch.features
    import fontmatch.fonts
    import fontmatch.index
    import fontmatch.match
    import fontmatch.scrape
    import fontmatch.service

    assert hasattr(fontmatch, "__version__")


def test_core_deps_importable():
    """All core dependencies resolve and import."""
    import brotli  # noqa: F401
    import flask  # noqa: F401
    import fontTools.ttLib  # noqa: F401
    import httpx  # noqa: F401
    import numpy  # noqa: F401
    import PIL  # noqa: F401
