"""Deterministic word counting.

A word is any non-empty sequence of non-whitespace characters. Markdown markup
is counted as written, so the result never depends on a Markdown renderer.
"""


def count_words(content: str) -> int:
    """Count whitespace-delimited tokens in ``content``."""
    return len(content.split())
