"""Exceptions shared across the image pipeline (messages are user-presentable)."""


class NoTextFound(Exception):
    """No readable text in the image."""


class EngineUnavailable(Exception):
    """The matcher can't run right now (OCR failure/timeout, atlas missing)."""
