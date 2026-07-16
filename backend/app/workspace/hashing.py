"""Deterministic content hashes."""

import hashlib


def sha256_content(content: str) -> str:
    """Return the lowercase SHA-256 hash of UTF-8 encoded text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
