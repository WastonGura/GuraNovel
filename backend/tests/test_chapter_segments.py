from dataclasses import FrozenInstanceError, replace
from uuid import UUID, uuid4

import pytest

from app.agents import (
    ApprovedOutlineSnapshot,
    ChapterReviewTarget,
    EditorReviewRequest,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewerRole,
    ReviewSegmentSnapshot,
    validate_chapter_review_report,
)
from app.documents.chapter_segments import (
    CURRENT_CHAPTER_SEGMENTER_VERSION,
    MARKDOWN_V1_SEGMENTER_VERSION,
    MAX_CHAPTER_LINES,
    MAX_CHAPTER_SEGMENT_BYTES,
    ChapterSegmentError,
    ChapterSegmentKind,
    build_chapter_review_segment_batches,
    derive_chapter_segment_map,
    validate_segment_map_evidence_integrity,
)
from app.workspace.hashing import sha256_content


PROJECT_ID = UUID("10000000-0000-4000-8000-000000000001")
CHAPTER_ID = UUID("20000000-0000-4000-8000-000000000002")
DOCUMENT_ID = UUID("30000000-0000-4000-8000-000000000003")
VERSION_ID = UUID("40000000-0000-4000-8000-000000000004")


def segment_map(content: str, *, version_id: UUID = VERSION_ID):
    return derive_chapter_segment_map(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        version_id=version_id,
        content=content,
    )


def test_markdown_v1_is_deterministic_version_scoped_and_crlf_canonical() -> None:
    content = "# 第一章\r\n\r\nHello, 世界 👋\u2028same paragraph\r\ncontinued\r\n"

    first = segment_map(content)
    repeated = segment_map(content.replace("\r\n", "\n"))

    assert first == repeated
    assert first.map_hash == repeated.map_hash
    assert b"Hello" not in first.canonical_bytes()
    assert "Hello" not in repr(first)
    assert (
        first.segmenter_version
        == CURRENT_CHAPTER_SEGMENTER_VERSION
        == MARKDOWN_V1_SEGMENTER_VERSION
        == "markdown-v1"
    )
    normalized = "# 第一章\n\nHello, 世界 👋\u2028same paragraph\ncontinued\n"
    assert first.content_hash == sha256_content(normalized)
    assert first.byte_size == len(normalized.encode())
    assert [item.kind for item in first.segments] == [
        ChapterSegmentKind.HEADING,
        ChapterSegmentKind.PARAGRAPH,
    ]
    assert [item.structural_path for item in first.segments] == [
        "h1:1/heading",
        "h1:1/block:1",
    ]
    assert first.segments[1].content == "Hello, 世界 👋\u2028same paragraph\ncontinued"
    changed_version = segment_map(content, version_id=uuid4())
    assert [item.segment_id for item in changed_version.segments] != [
        item.segment_id for item in first.segments
    ]


def test_markdown_v1_ignores_blank_blocks_and_keeps_fenced_blocks_together() -> None:
    result = segment_map("\n\n## Scene\n\nBefore.\n\n```python\n\nprint('ok')\n```\n\nAfter.\n\n")

    assert [(item.kind, item.content) for item in result.segments] == [
        (ChapterSegmentKind.HEADING, "## Scene"),
        (ChapterSegmentKind.PARAGRAPH, "Before."),
        (ChapterSegmentKind.FENCED_BLOCK, "```python\n\nprint('ok')\n```"),
        (ChapterSegmentKind.PARAGRAPH, "After."),
    ]
    assert [item.ordinal for item in result.segments] == [1, 2, 3, 4]
    assert [item.structural_path for item in result.segments] == [
        "h2:1/heading",
        "h2:1/block:1",
        "h2:1/block:2/part:1",
        "h2:1/block:3",
    ]

    setext = segment_map("Title\n=====\n\nText.")
    assert [(item.kind, item.content) for item in setext.segments] == [
        (ChapterSegmentKind.HEADING, "Title\n====="),
        (ChapterSegmentKind.PARAGRAPH, "Text."),
    ]
    multiline = segment_map("First\nSecond\n---")
    assert [(item.kind, item.content) for item in multiline.segments] == [
        (ChapterSegmentKind.HEADING, "First\nSecond\n---")
    ]
    fenced_first = segment_map("```text\n---\n```\n\nAfter.")
    assert fenced_first.segments[0].kind is ChapterSegmentKind.FENCED_BLOCK
    assert fenced_first.segments[0].content == "```text\n---\n```"


def test_long_unicode_block_is_split_on_utf8_boundaries_without_data_loss() -> None:
    content = "界" * (MAX_CHAPTER_SEGMENT_BYTES // 3 + 200)

    result = segment_map(content)

    assert len(result.segments) == 2
    assert "".join(item.content for item in result.segments) == content
    assert all(len(item.content.encode()) <= MAX_CHAPTER_SEGMENT_BYTES for item in result.segments)
    assert [item.structural_path for item in result.segments] == [
        "root/block:1/part:1",
        "root/block:1/part:2",
    ]

    fenced = segment_map("```\n" + content + "\n```")
    assert "".join(item.content for item in fenced.segments) == "```\n" + content + "\n```"
    assert all(item.kind is ChapterSegmentKind.FENCED_BLOCK for item in fenced.segments)


def test_offsets_are_utf8_byte_ranges_and_round_trip_emoji() -> None:
    content = "# 🦈\n\nA界👋B"
    result = segment_map(content)
    encoded = content.encode("utf-8")

    for item in result.segments:
        assert encoded[item.start_byte : item.end_byte].decode("utf-8") == item.content
        assert item.end_byte - item.start_byte == len(item.content.encode("utf-8"))


@pytest.mark.parametrize(
    "content",
    ["", " \n\t\n", "safe\x00secret", "bad\ud800text"],
)
def test_invalid_or_empty_content_fails_without_leaking_content(content: str) -> None:
    with pytest.raises(ChapterSegmentError) as error:
        segment_map(content)

    assert str(error.value) == error.value.code
    assert "secret" not in str(error.value)
    assert "bad" not in str(error.value)


def test_unknown_algorithm_and_oversized_content_fail_closed() -> None:
    with pytest.raises(ChapterSegmentError, match="unknown_chapter_segmenter"):
        derive_chapter_segment_map(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DOCUMENT_ID,
            version_id=VERSION_ID,
            content="text",
            segmenter_version="markdown-v999",
        )

    with pytest.raises(ChapterSegmentError, match="chapter_content_too_large"):
        segment_map("a" * (2 * 1024 * 1024 + 1))

    with pytest.raises(ChapterSegmentError, match="too_many_chapter_segments"):
        segment_map("x\n\n" * 1025 + " " * (2 * 1024 * 1024 - 3 * 1025))


@pytest.mark.parametrize(
    "content",
    [
        "\n" * MAX_CHAPTER_LINES + " " * (2 * 1024 * 1024 - MAX_CHAPTER_LINES),
        "```\n" + "\n" * MAX_CHAPTER_LINES + "```",
    ],
    ids=("blank-newline-bomb", "fenced-newline-bomb"),
)
def test_newline_bombs_fail_before_streaming_scan(
    content: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_stream(_content: str):
        raise AssertionError("streaming scan must not start")

    monkeypatch.setattr("app.documents.chapter_segments._stream_lines", fail_stream)

    with pytest.raises(ChapterSegmentError, match="too_many_chapter_lines"):
        segment_map(content)


def test_locator_is_frozen_and_id_changes_with_content_or_algorithm() -> None:
    original = segment_map("First paragraph.")
    changed = segment_map("Changed paragraph.")

    assert original.segments[0].segment_id != changed.segments[0].segment_id
    with pytest.raises(FrozenInstanceError):
        original.segments[0].content = "mutated"  # type: ignore[misc]


def test_locators_are_uuid_compatible_with_chapter_review_snapshots() -> None:
    result = segment_map("# One\n\nReview evidence.")

    snapshots = tuple(
        ReviewSegmentSnapshot(
            segment_id=segment.segment_id,
            index=segment.ordinal,
            title=segment.structural_path,
            content=segment.content,
        )
        for segment in result.segments
    )

    assert tuple(item.segment_id for item in snapshots) == tuple(
        item.segment_id for item in result.segments
    )

    request = EditorReviewRequest(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        workflow_run_id=uuid4(),
        target=ChapterReviewTarget(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DOCUMENT_ID,
            version_id=VERSION_ID,
            segments=snapshots,
        ),
        approved_outline=ApprovedOutlineSnapshot(
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=uuid4(),
            version_id=uuid4(),
            content="Approved outline.",
        ),
        contexts=(
            ReviewContextSnapshot(
                project_id=PROJECT_ID,
                document_id=uuid4(),
                version_id=uuid4(),
                kind=ReviewContextKind.STYLE_GUIDE,
                content="Style context.",
            ),
        ),
    )
    report = validate_chapter_review_report(
        {
            "project_id": str(PROJECT_ID),
            "chapter_id": str(CHAPTER_ID),
            "workflow_run_id": str(request.workflow_run_id),
            "reviewer_role": "editor_agent",
            "review_mode": "chapter_editor",
            "target_document_id": str(DOCUMENT_ID),
            "target_version_id": str(VERSION_ID),
            "passed": True,
            "summary": "Advisory review.",
            "findings": [
                {
                    "sequence": 1,
                    "code": "pacing_note",
                    "severity": "warning",
                    "required": False,
                    "evidence_segment_ids": [str(result.segments[1].segment_id)],
                    "rationale": "The opening is compressed.",
                    "suggested_action": "Consider a longer beat.",
                }
            ],
            "suggested_actions": [],
        },
        request=request,
        reviewer_role=ReviewerRole.EDITOR,
        mode="chapter_editor",
    )
    assert report.findings[0].evidence_segment_ids == (result.segments[1].segment_id,)


def test_integrity_validation_requires_exact_binding_known_unique_canonical_ids() -> None:
    result = segment_map("# One\n\nFirst.\n\nSecond.")
    selected = (result.segments[0].segment_id, result.segments[2].segment_id)

    assert (
        validate_segment_map_evidence_integrity(
            result,
            project_id=PROJECT_ID,
            chapter_id=CHAPTER_ID,
            document_id=DOCUMENT_ID,
            version_id=VERSION_ID,
            segmenter_version="markdown-v1",
            segment_ids=selected,
        )
        == selected
    )

    invalid_values = (
        {"project_id": uuid4()},
        {"chapter_id": uuid4()},
        {"document_id": uuid4()},
        {"version_id": uuid4()},
        {"segmenter_version": "markdown-v999"},
        {"segment_ids": (selected[0], selected[0])},
        {"segment_ids": tuple(reversed(selected))},
        {"segment_ids": (uuid4(),)},
        {"segment_ids": ()},
        {"segment_ids": tuple(result.segments[0].segment_id for _ in range(65))},
    )
    defaults = {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "segmenter_version": "markdown-v1",
        "segment_ids": selected,
    }
    for override in invalid_values:
        with pytest.raises(ChapterSegmentError):
            validate_segment_map_evidence_integrity(result, **{**defaults, **override})

    with pytest.raises(ChapterSegmentError, match="invalid_evidence_binding"):
        validate_segment_map_evidence_integrity(
            replace(result, map_hash="0" * 64),
            **defaults,
        )


def test_forged_map_segments_fail_integrity_validation() -> None:
    result = segment_map("# One\n\nEvidence.")
    segment = result.segments[0]
    defaults = {
        "project_id": PROJECT_ID,
        "chapter_id": CHAPTER_ID,
        "document_id": DOCUMENT_ID,
        "version_id": VERSION_ID,
        "segmenter_version": "markdown-v1",
        "segment_ids": (segment.segment_id,),
    }
    forged_segments = (
        replace(segment, content="forged"),
        replace(segment, content_hash="0" * 64),
        replace(segment, segment_id=uuid4()),
        replace(segment, start_byte=1),
        replace(segment, end_byte=999999),
        replace(segment, ordinal=2),
    )
    for forged in forged_segments:
        forged_map = replace(result, segments=(forged, *result.segments[1:]))
        forged_map = replace(
            forged_map,
            map_hash=sha256_content(forged_map.canonical_bytes().decode()),
        )
        with pytest.raises(ChapterSegmentError, match="invalid_evidence_binding"):
            validate_segment_map_evidence_integrity(forged_map, **defaults)


def test_review_batching_keeps_global_identity_and_limits_each_request_to_64() -> None:
    result = segment_map("\n\n".join(f"Paragraph {index}." for index in range(65)))

    batches = build_chapter_review_segment_batches(result)

    assert [len(batch.segments) for batch in batches] == [64, 1]
    assert [item.global_ordinal for batch in batches for item in batch.segments] == list(
        range(1, 66)
    )
    assert [item.segment_id for batch in batches for item in batch.segments] == [
        item.segment_id for item in result.segments
    ]
    assert [item.batch_index for item in batches[0].segments] == list(range(1, 65))
    assert batches[1].segments[0].batch_index == 1


def test_markdown_v1_golden_identity_is_frozen() -> None:
    result = derive_chapter_segment_map(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        content="# One\n\nHello 👋",
        segmenter_version=MARKDOWN_V1_SEGMENTER_VERSION,
    )

    assert str(result.segments[0].segment_id) == "19207b85-20b2-577f-9e19-e7ba42283270"
    assert str(result.segments[1].segment_id) == "2ffc87e3-7da4-5e98-9dfe-6ed642289dcd"
    assert result.map_hash == "86762f6b7253f0f451894efa5b522a5b12bc74d1c4a7c77d599563f84babe484"
    assert result.canonical_bytes() == (
        b'{"byte_size":17,"chapter_id":"20000000-0000-4000-8000-000000000002",'
        b'"content_hash":"b77be422f985ae6b97314f6473f8bd52f3dac81b04d0407edb25d26636c3ba58",'
        b'"document_id":"30000000-0000-4000-8000-000000000003",'
        b'"project_id":"10000000-0000-4000-8000-000000000001",'
        b'"segmenter_version":"markdown-v1","segments":'
        b'[{"content_hash":"9ec1d2e3a102edbfa7ce4512308cf15d8fa812b46ea8541a4905a275b6d271f8",'
        b'"end_byte":5,"kind":"heading","ordinal":1,'
        b'"segment_id":"19207b85-20b2-577f-9e19-e7ba42283270","start_byte":0,'
        b'"structural_path":"h1:1/heading"},'
        b'{"content_hash":"fbb1d2ddebbd25b27597955e509871212ca30f34114cab9832e8c32046f37cd4",'
        b'"end_byte":17,"kind":"paragraph","ordinal":2,'
        b'"segment_id":"2ffc87e3-7da4-5e98-9dfe-6ed642289dcd","start_byte":7,'
        b'"structural_path":"h1:1/block:1"}],'
        b'"version_id":"40000000-0000-4000-8000-000000000004"}'
    )


def test_current_segmenter_alias_is_the_default_selection() -> None:
    assert segment_map("Text.") == derive_chapter_segment_map(
        project_id=PROJECT_ID,
        chapter_id=CHAPTER_ID,
        document_id=DOCUMENT_ID,
        version_id=VERSION_ID,
        content="Text.",
        segmenter_version=CURRENT_CHAPTER_SEGMENTER_VERSION,
    )
