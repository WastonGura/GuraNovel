"""Pure document-domain helpers."""

from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    MARKDOWN_V1_SEGMENTER_VERSION,
    MAX_CHAPTER_CONTENT_BYTES,
    MAX_CHAPTER_LINES,
    MAX_CHAPTER_SEGMENT_BYTES,
    MAX_CHAPTER_SEGMENTS,
    ChapterReviewSegmentBatch,
    ChapterSegment,
    ChapterSegmentError,
    ChapterSegmentKind,
    ChapterSegmentMap,
    ReviewSegmentSelection,
    build_chapter_review_segment_batches,
    derive_chapter_segment_map,
)

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
]
