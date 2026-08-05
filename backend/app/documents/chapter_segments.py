"""Deterministic, bounded locators for immutable chapter snapshots.

Pure map validation proves internal integrity only. Source authority belongs to
``DocumentService``, which re-reads and re-derives an exact server-owned version.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import json
import re
from types import MappingProxyType
from typing import Final
import unicodedata
from uuid import UUID, uuid5

from app.workspace.hashing import sha256_content


MARKDOWN_V1_SEGMENTER_VERSION: Final = "markdown-v1"
CURRENT_CHAPTER_SEGMENTER_VERSION: Final = MARKDOWN_V1_SEGMENTER_VERSION
MAX_CHAPTER_CONTENT_BYTES = 2 * 1024 * 1024
MAX_CHAPTER_LINES = 32_768
MAX_CHAPTER_SEGMENT_BYTES = 16 * 1024
MAX_CHAPTER_SEGMENTS = 1024
MAX_REVIEW_SEGMENTS_PER_BATCH = 64

_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)")
_SETEXT_HEADING = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HASH = re.compile(r"[0-9a-f]{64}")


class ChapterSegmentError(ValueError):
    """A content-safe validation failure at the segment-map boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ChapterSegmentKind(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    FENCED_BLOCK = "fenced_block"


@dataclass(frozen=True, slots=True)
class ChapterSegment:
    segment_id: UUID
    ordinal: int
    structural_path: str
    kind: ChapterSegmentKind
    content_hash: str
    start_byte: int
    end_byte: int
    content: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChapterSegmentMap:
    project_id: UUID
    chapter_id: UUID
    document_id: UUID
    version_id: UUID
    segmenter_version: str
    content_hash: str
    byte_size: int
    map_hash: str
    segments: tuple[ChapterSegment, ...]

    def canonical_bytes(self) -> bytes:
        """Return the content-free canonical integrity envelope."""

        return _canonical_map_bytes(
            project_id=self.project_id,
            chapter_id=self.chapter_id,
            document_id=self.document_id,
            version_id=self.version_id,
            segmenter_version=self.segmenter_version,
            content_hash=self.content_hash,
            byte_size=self.byte_size,
            segments=self.segments,
        )


@dataclass(frozen=True, slots=True)
class ReviewSegmentSelection:
    segment_id: UUID
    global_ordinal: int
    batch_index: int
    title: str
    content: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChapterReviewSegmentBatch:
    batch_number: int
    segments: tuple[ReviewSegmentSelection, ...]


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    start_char: int
    end_char: int
    start_byte: int
    end_byte: int


@dataclass(frozen=True, slots=True)
class _Block:
    kind: ChapterSegmentKind
    structural_path: str
    start_char: int
    end_char: int
    start_byte: int
    end_byte: int


Segmenter = Callable[[str], Iterator[_Block]]


def normalize_chapter_content(content: str) -> str:
    if type(content) is not str:
        raise ChapterSegmentError("invalid_chapter_content")
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    try:
        encoded = normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise ChapterSegmentError("invalid_chapter_content") from None
    if len(encoded) > MAX_CHAPTER_CONTENT_BYTES:
        raise ChapterSegmentError("chapter_content_too_large")
    if normalized.count("\n") + 1 > MAX_CHAPTER_LINES:
        raise ChapterSegmentError("too_many_chapter_lines")
    if any(
        unicodedata.category(character) == "Cc" and character not in "\n\t"
        for character in normalized
    ):
        raise ChapterSegmentError("invalid_chapter_content")
    return normalized


def _stream_lines(content: str) -> Iterator[_Line]:
    start_char = 0
    start_byte = 0
    while True:
        newline = content.find("\n", start_char)
        end_char = len(content) if newline < 0 else newline
        text = content[start_char:end_char]
        end_byte = start_byte + len(text.encode("utf-8"))
        yield _Line(text, start_char, end_char, start_byte, end_byte)
        if newline < 0:
            return
        start_char = newline + 1
        start_byte = end_byte + 1


def _heading_path(counters: list[int], level: int) -> str:
    return "/".join(
        f"h{index}:{counters[index]}" for index in range(1, level + 1) if counters[index]
    )


def _is_fence_close(line: str, marker: str) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False
    match = re.match(rf"{re.escape(marker[0])}{{{len(marker)},}}[ \t]*$", stripped)
    return match is not None


def _markdown_v1(content: str) -> Iterator[_Block]:
    lines = iter(_stream_lines(content))
    pending: _Line | None = None
    heading_counters = [0] * 7
    heading_context = "root"
    block_index = 0

    while True:
        try:
            current = pending if pending is not None else next(lines)
        except StopIteration:
            return
        pending = None
        if not current.text.strip():
            continue

        fence = _FENCE_OPEN.match(current.text)
        if fence is not None:
            marker = fence.group(1)
            final = current
            for candidate in lines:
                final = candidate
                if _is_fence_close(candidate.text, marker):
                    break
            block_index += 1
            yield _Block(
                ChapterSegmentKind.FENCED_BLOCK,
                f"{heading_context}/block:{block_index}",
                current.start_char,
                final.end_char,
                current.start_byte,
                final.end_byte,
            )
            continue

        atx = _ATX_HEADING.match(current.text)
        if atx is not None:
            level = len(atx.group(1))
            heading_counters[level] += 1
            for deeper in range(level + 1, 7):
                heading_counters[deeper] = 0
            heading_context = _heading_path(heading_counters, level)
            block_index = 0
            yield _Block(
                ChapterSegmentKind.HEADING,
                f"{heading_context}/heading",
                current.start_char,
                current.end_char,
                current.start_byte,
                current.end_byte,
            )
            continue

        paragraph_start = current
        paragraph_end = current
        setext_level: int | None = None
        for candidate in lines:
            if not candidate.text.strip():
                break
            setext = _SETEXT_HEADING.match(candidate.text)
            if setext is not None:
                paragraph_end = candidate
                setext_level = 1 if setext.group(1).startswith("=") else 2
                break
            if _FENCE_OPEN.match(candidate.text) or _ATX_HEADING.match(candidate.text):
                pending = candidate
                break
            paragraph_end = candidate

        if setext_level is not None:
            heading_counters[setext_level] += 1
            for deeper in range(setext_level + 1, 7):
                heading_counters[deeper] = 0
            heading_context = _heading_path(heading_counters, setext_level)
            block_index = 0
            kind = ChapterSegmentKind.HEADING
            structural_path = f"{heading_context}/heading"
        else:
            block_index += 1
            kind = ChapterSegmentKind.PARAGRAPH
            structural_path = f"{heading_context}/block:{block_index}"
        yield _Block(
            kind,
            structural_path,
            paragraph_start.start_char,
            paragraph_end.end_char,
            paragraph_start.start_byte,
            paragraph_end.end_byte,
        )


_SEGMENTERS: Final = MappingProxyType({MARKDOWN_V1_SEGMENTER_VERSION: _markdown_v1})


def _bounded_parts(content: str, block: _Block) -> Iterator[tuple[int, int, int, int]]:
    start_char = block.start_char
    start_byte = block.start_byte
    while start_char < block.end_char:
        end_char = start_char
        end_byte = start_byte
        while end_char < block.end_char:
            width = len(content[end_char].encode("utf-8"))
            if end_byte - start_byte + width > MAX_CHAPTER_SEGMENT_BYTES:
                break
            end_byte += width
            end_char += 1
        if end_char == start_char:
            raise ChapterSegmentError("invalid_chapter_content")
        yield start_char, end_char, start_byte, end_byte
        start_char = end_char
        start_byte = end_byte


def _require_uuid(value: object) -> UUID:
    if type(value) is not UUID or value.int == 0:
        raise ChapterSegmentError("invalid_chapter_segment_binding")
    return value


def _segment_id(
    version_id: UUID, segmenter_version: str, ordinal: int, path: str, content_hash: str
) -> UUID:
    return uuid5(version_id, f"{segmenter_version}|{ordinal}|{path}|{content_hash}")


def _canonical_map_bytes(
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    segmenter_version: str,
    content_hash: str,
    byte_size: int,
    segments: tuple[ChapterSegment, ...],
) -> bytes:
    payload = {
        "byte_size": byte_size,
        "chapter_id": str(chapter_id),
        "content_hash": content_hash,
        "document_id": str(document_id),
        "project_id": str(project_id),
        "segmenter_version": segmenter_version,
        "segments": [
            {
                "content_hash": item.content_hash,
                "end_byte": item.end_byte,
                "kind": item.kind.value,
                "ordinal": item.ordinal,
                "segment_id": str(item.segment_id),
                "start_byte": item.start_byte,
                "structural_path": item.structural_path,
            }
            for item in segments
        ],
        "version_id": str(version_id),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _map_hash(**kwargs: object) -> str:
    return sha256_content(_canonical_map_bytes(**kwargs).decode())  # type: ignore[arg-type]


def derive_chapter_segment_map(
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    content: str,
    segmenter_version: str = CURRENT_CHAPTER_SEGMENTER_VERSION,
) -> ChapterSegmentMap:
    project_id = _require_uuid(project_id)
    chapter_id = _require_uuid(chapter_id)
    document_id = _require_uuid(document_id)
    version_id = _require_uuid(version_id)
    if type(segmenter_version) is not str or segmenter_version not in _SEGMENTERS:
        raise ChapterSegmentError("unknown_chapter_segmenter")
    normalized = normalize_chapter_content(content)
    segments: list[ChapterSegment] = []
    for block in _SEGMENTERS[segmenter_version](normalized):
        parts = _bounded_parts(normalized, block)
        for part_number, (start_char, end_char, start_byte, end_byte) in enumerate(parts, 1):
            if len(segments) >= MAX_CHAPTER_SEGMENTS:
                raise ChapterSegmentError("too_many_chapter_segments")
            segment_content = normalized[start_char:end_char]
            content_hash = sha256_content(segment_content)
            ordinal = len(segments) + 1
            path = block.structural_path
            if end_byte - start_byte < block.end_byte - block.start_byte or part_number > 1:
                path = f"{path}/part:{part_number}"
            elif block.kind is ChapterSegmentKind.FENCED_BLOCK:
                path = f"{path}/part:1"
            segments.append(
                ChapterSegment(
                    _segment_id(version_id, segmenter_version, ordinal, path, content_hash),
                    ordinal,
                    path,
                    block.kind,
                    content_hash,
                    start_byte,
                    end_byte,
                    segment_content,
                )
            )
    if not segments:
        raise ChapterSegmentError("empty_chapter_content")
    encoded = normalized.encode()
    content_hash = sha256_content(normalized)
    segment_tuple = tuple(segments)
    hash_kwargs = {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "document_id": document_id,
        "version_id": version_id,
        "segmenter_version": segmenter_version,
        "content_hash": content_hash,
        "byte_size": len(encoded),
        "segments": segment_tuple,
    }
    return ChapterSegmentMap(**hash_kwargs, map_hash=_map_hash(**hash_kwargs))


def _validate_map_integrity(segment_map: ChapterSegmentMap) -> None:
    try:
        bindings_are_valid = all(
            _require_uuid(value) is value
            for value in (
                segment_map.project_id,
                segment_map.chapter_id,
                segment_map.document_id,
                segment_map.version_id,
            )
        )
    except (AttributeError, ChapterSegmentError):
        bindings_are_valid = False
    if (
        type(segment_map) is not ChapterSegmentMap
        or not bindings_are_valid
        or segment_map.segmenter_version not in _SEGMENTERS
        or type(segment_map.segments) is not tuple
        or not 1 <= len(segment_map.segments) <= MAX_CHAPTER_SEGMENTS
        or type(segment_map.byte_size) is not int
        or not 0 <= segment_map.byte_size <= MAX_CHAPTER_CONTENT_BYTES
        or _HASH.fullmatch(segment_map.content_hash) is None
    ):
        raise ChapterSegmentError("invalid_evidence_binding")
    previous_end = 0
    for ordinal, item in enumerate(segment_map.segments, 1):
        try:
            encoded = item.content.encode("utf-8")
        except (AttributeError, UnicodeEncodeError):
            raise ChapterSegmentError("invalid_evidence_binding") from None
        if (
            type(item) is not ChapterSegment
            or item.ordinal != ordinal
            or type(item.kind) is not ChapterSegmentKind
            or type(item.structural_path) is not str
            or not item.structural_path
            or _HASH.fullmatch(item.content_hash) is None
            or item.content_hash != sha256_content(item.content)
            or type(item.start_byte) is not int
            or type(item.end_byte) is not int
            or not previous_end <= item.start_byte < item.end_byte <= segment_map.byte_size
            or item.end_byte - item.start_byte != len(encoded)
            or len(encoded) > MAX_CHAPTER_SEGMENT_BYTES
            or item.segment_id
            != _segment_id(
                segment_map.version_id,
                segment_map.segmenter_version,
                ordinal,
                item.structural_path,
                item.content_hash,
            )
        ):
            raise ChapterSegmentError("invalid_evidence_binding")
        previous_end = item.end_byte
    expected_hash = _map_hash(
        project_id=segment_map.project_id,
        chapter_id=segment_map.chapter_id,
        document_id=segment_map.document_id,
        version_id=segment_map.version_id,
        segmenter_version=segment_map.segmenter_version,
        content_hash=segment_map.content_hash,
        byte_size=segment_map.byte_size,
        segments=segment_map.segments,
    )
    if segment_map.map_hash != expected_hash:
        raise ChapterSegmentError("invalid_evidence_binding")


def validate_segment_map_evidence_integrity(
    segment_map: ChapterSegmentMap,
    *,
    project_id: UUID,
    chapter_id: UUID,
    document_id: UUID,
    version_id: UUID,
    segmenter_version: str,
    segment_ids: Sequence[UUID],
) -> tuple[UUID, ...]:
    """Validate internal integrity only; this does not establish source authority."""

    _validate_map_integrity(segment_map)
    binding = (
        _require_uuid(project_id),
        _require_uuid(chapter_id),
        _require_uuid(document_id),
        _require_uuid(version_id),
        segmenter_version,
    )
    if binding != (
        segment_map.project_id,
        segment_map.chapter_id,
        segment_map.document_id,
        segment_map.version_id,
        segment_map.segmenter_version,
    ):
        raise ChapterSegmentError("invalid_evidence_binding")
    if type(segment_ids) not in (tuple, list) or not 1 <= len(segment_ids) <= 64:
        raise ChapterSegmentError("invalid_evidence_segments")
    selected = tuple(_require_uuid(value) for value in segment_ids)
    known_order = {item.segment_id: item.ordinal for item in segment_map.segments}
    if (
        len(selected) != len(set(selected))
        or any(value not in known_order for value in selected)
        or list(selected) != sorted(selected, key=known_order.__getitem__)
    ):
        raise ChapterSegmentError("invalid_evidence_segments")
    return selected


def build_chapter_review_segment_batches(
    segment_map: ChapterSegmentMap,
) -> tuple[ChapterReviewSegmentBatch, ...]:
    """Select every locator in deterministic batches accepted by #112 contracts."""

    _validate_map_integrity(segment_map)
    batches: list[ChapterReviewSegmentBatch] = []
    for start in range(0, len(segment_map.segments), MAX_REVIEW_SEGMENTS_PER_BATCH):
        source = segment_map.segments[start : start + MAX_REVIEW_SEGMENTS_PER_BATCH]
        selections = tuple(
            ReviewSegmentSelection(
                segment_id=item.segment_id,
                global_ordinal=item.ordinal,
                batch_index=index,
                title=item.structural_path,
                content=item.content,
            )
            for index, item in enumerate(source, 1)
        )
        batches.append(ChapterReviewSegmentBatch(len(batches) + 1, selections))
    return tuple(batches)


__all__ = [
    "CURRENT_CHAPTER_SEGMENTER_VERSION",
    "MARKDOWN_V1_SEGMENTER_VERSION",
    "MAX_CHAPTER_CONTENT_BYTES",
    "MAX_CHAPTER_LINES",
    "MAX_CHAPTER_SEGMENT_BYTES",
    "MAX_CHAPTER_SEGMENTS",
    "ChapterReviewSegmentBatch",
    "ChapterSegment",
    "ChapterSegmentError",
    "ChapterSegmentKind",
    "ChapterSegmentMap",
    "ReviewSegmentSelection",
    "build_chapter_review_segment_batches",
    "derive_chapter_segment_map",
    "normalize_chapter_content",
]
