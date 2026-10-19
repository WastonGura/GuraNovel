"""Bounded image normalization and descriptor-relative persistent cover storage."""
import hashlib
import io
import os
import re
import stat
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import AppError, NotFoundError, ValidationError
from app.workspace.project_workspace import ProjectWorkspace

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 16_000_000
COVER_WIDTHS = (160, 320, 640, 1200)


class CoverTooLargeError(AppError):
    status_code = 413
    code = "cover_too_large"
    default_message = "Cover images must be at most 5 MiB."


def normalize_cover(payload: bytes) -> bytes:
    if len(payload) > MAX_UPLOAD_BYTES:
        raise CoverTooLargeError()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > MAX_PIXELS:
                    raise ValidationError("Use a PNG, JPEG or WebP image with at most 16 million pixels.")
                image.load()
                oriented = ImageOps.exif_transpose(image).convert("RGBA")
                oriented.thumbnail((1200, 1600), Image.Resampling.LANCZOS)
                background = Image.new("RGBA", oriented.size, "white")
                background.alpha_composite(oriented)
                result = io.BytesIO()
                background.convert("RGB").save(result, format="WEBP", quality=88)
                return result.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValidationError("The uploaded image cannot be decoded.") from error


def resize_cover(payload: bytes, width: int) -> bytes:
    """Preserve aspect ratio, never upscale, and reuse the master when it already fits."""
    with Image.open(io.BytesIO(payload)) as image:
        if image.width <= width:
            return payload
        height = max(1, round(image.height * width / image.width))
        resized = image.resize((width, height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        resized.save(output, format="WEBP", quality=80 if width <= 320 else 85)
        return output.getvalue()


class CoverStore:
    def __init__(self, workspace: ProjectWorkspace) -> None:
        self.workspace = workspace

    @staticmethod
    def _write(directory: int, filename: str, data: bytes) -> None:
        temporary = f".{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    @staticmethod
    def _read(directory: int, filename: str) -> bytes:
        descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_UPLOAD_BYTES:
                raise NotFoundError()
            content = stream.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise NotFoundError()
            return content

    def save(self, payload: bytes) -> str:
        data = normalize_cover(payload)
        identity = hashlib.sha256(data).hexdigest()
        directory = self.workspace.open_assets_directory()
        try:
            self._write(directory, identity + ".webp", data)
            for width in COVER_WIDTHS:
                self._write(directory, f"{identity}-{width}.webp", resize_cover(data, width))
        finally:
            os.close(directory)
        return "cover-upload:" + identity

    def read(self, identity: str, width: int | None = None) -> bytes:
        if not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise NotFoundError()
        if width is not None and width not in COVER_WIDTHS:
            raise ValidationError("Unsupported cover width.")
        directory = self.workspace.open_assets_directory()
        try:
            try:
                filename = identity + (f"-{width}" if width else "") + ".webp"
                try:
                    return self._read(directory, filename)
                except FileNotFoundError:
                    if width is None:
                        raise
                    # Older uploads acquire a derivative once; no image migration is required.
                    data = resize_cover(self._read(directory, identity + ".webp"), width)
                    self._write(directory, filename, data)
                    return data
            except OSError as error:
                raise NotFoundError() from error
        finally:
            os.close(directory)
