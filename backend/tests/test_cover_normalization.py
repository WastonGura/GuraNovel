import io

import pytest
from PIL import Image

from app.core.errors import ValidationError
from app.workspace.cover_store import normalize_cover


def test_cover_is_bounded_white_backed_webp_without_source_metadata():
    stream = io.BytesIO()
    Image.new("RGBA", (1800, 1800), (0, 0, 0, 0)).save(stream, format="PNG")
    with Image.open(io.BytesIO(normalize_cover(stream.getvalue()))) as image:
        assert image.format == "WEBP"
        assert image.size == (1200, 1200)
        assert min(image.getpixel((0, 0))) >= 250
        assert not image.getexif()


def test_unsupported_formats_are_rejected():
    stream = io.BytesIO()
    Image.new("RGB", (2, 2)).save(stream, format="GIF")
    with pytest.raises(ValidationError):
        normalize_cover(stream.getvalue())
