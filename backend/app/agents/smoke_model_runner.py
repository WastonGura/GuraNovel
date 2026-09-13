"""Bounded, explicit real-model smoke command for chapter writing and review.

Requires explicit opt-in via --enable-real-model or GURANOVEL_SMOKE_REAL_MODEL=1.
Executes exactly one cost-bounded invocation against standard quality benchmark cases.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from uuid import uuid4

from app.agents.chapter_writer_contracts import (
    AllowedChapterSegment,
    ApprovedOutlineReference,
    InitialDraftRequest,
    WriterContextKind,
    WriterContextSnapshot,
)
from app.agents.chapter_review_contracts import (
    ApprovedOutlineSnapshot,
    ChapterReviewTarget,
    EditorReviewRequest,
    ChiefEditorChapterFinalRequest,
    LoreChapterFinalRequest,
    ReviewContextKind,
    ReviewContextSnapshot,
    ReviewSegmentSnapshot,
)
from app.agents.chapter_writer_providers import OpenAICompatibleChapterWriterProvider
from app.agents.chapter_review_providers import OpenAICompatibleChapterReviewProvider
from app.agents.concept_providers import (
    OpenAICompatibleConceptChiefEditorProvider,
    OpenAICompatibleConceptProvider,
)
from app.agents.maintenance_providers import OpenAICompatibleMaintenanceProvider
from app.agents.contracts import (
    ConceptAgentRequest,
    ConceptGenerationOutput,
    ConceptOption,
)
from app.agents.maintenance_contracts import (
    AffectedItemReference,
    AppliedDocumentReference,
    ApplyChangeRequest,
    DocumentVersionReference,
    MaintenanceImpactRequest,
    PostChangeRequest,
    RevisionOperation,
    RevisionOperationKind,
    RevisionPlanRequest,
)
from app.workflows.project_maintenance_types import AffectedItemType, ImpactLevel
from app.agents.profiles import ProfileRegistry
from app.core.config import settings
from enum import StrEnum
from typing import Any
from tests.quality_benchmarks import get_benchmark_by_id


class BenchmarkSampleId(StrEnum):
    SAMPLE_1_MOTIVATION_BREAKDOWN = "sample_1_motivation_breakdown"
    SAMPLE_2_LORE_CONFLICT = "sample_2_lore_conflict"
    SAMPLE_3_STYLE_MISMATCH = "sample_3_style_mismatch"
    SAMPLE_4_QUALIFIED = "sample_4_qualified"


_SAMPLE_MAP: dict[BenchmarkSampleId, str] = {
    BenchmarkSampleId.SAMPLE_1_MOTIVATION_BREAKDOWN: "bench-motivation-breakdown-01",
    BenchmarkSampleId.SAMPLE_2_LORE_CONFLICT: "bench-timeline-lore-conflict-02",
    BenchmarkSampleId.SAMPLE_3_STYLE_MISMATCH: "bench-style-mismatch-03",
    BenchmarkSampleId.SAMPLE_4_QUALIFIED: "bench-qualified-chapter-04",
}


def _load_sample_outline_and_draft(sample_id: BenchmarkSampleId) -> tuple[str, list[dict[str, Any]]]:
    case_id = _SAMPLE_MAP[sample_id]
    case = get_benchmark_by_id(case_id)
    outline = case.outline_content
    segments = [
        {"title": seg["title"], "content": seg["content"]}
        for seg in case.segments
    ]
    return outline, segments


async def run_smoke(
    *,
    role: str,
    sample_id: BenchmarkSampleId,
    base_url: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
) -> int:
    registry = ProfileRegistry()
    outline_text, candidate_segments = _load_sample_outline_and_draft(sample_id)

    project_id = uuid4()
    chapter_id = uuid4()
    workflow_run_id = uuid4()
    outline_doc_id = uuid4()
    outline_ver_id = uuid4()

    start_time = time.monotonic()
    print("=== GuraNovel Real Model Smoke Test ===")
    print(f"Target Role: {role}")
    print(f"Benchmark Sample: {sample_id.value}")
    print(f"Model: {model}")
    print(f"Base URL: {base_url}")
    print("Initiating bounded structured model call...")

    try:
        if role == "writer":
            provider = OpenAICompatibleChapterWriterProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            try:
                allowed_segments = tuple(
                    AllowedChapterSegment(
                        segment_id=uuid4(),
                        index=idx,
                        title=seg["title"],
                        brief=seg["title"],
                    )
                    for idx, seg in enumerate(candidate_segments, 1)
                )
                request = InitialDraftRequest(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    workflow_run_id=workflow_run_id,
                    approved_outline=ApprovedOutlineReference(
                        document_id=outline_doc_id,
                        version_id=outline_ver_id,
                        project_id=project_id,
                        chapter_id=chapter_id,
                        content=outline_text,
                    ),
                    allowed_segments=allowed_segments,
                    contexts=(
                        WriterContextSnapshot(
                            document_id=uuid4(),
                            version_id=uuid4(),
                            project_id=project_id,
                            kind=WriterContextKind.STYLE_GUIDE,
                            content="Standard Victorian gothic third-person limited voice.",
                        ),
                    ),
                )
                profile = registry.load("writer_agent", mode="initial_draft")
                result = await provider.draft_initial(request, profile)
                elapsed = time.monotonic() - start_time
                print(f"[SUCCESS] Writer produced {len(result.segments)} segments in {elapsed:.2f}s")
                print(f"Summary: {result.summary[:120]}...")
                print(f"Self Check: outline_followed={result.self_check.outline_followed}")
                print(f"Input Tokens: {provider.last_input_tokens}, Output Tokens: {provider.last_output_tokens}")
                return 0
            finally:
                await provider.aclose()

        elif role in ("editor", "chief_editor", "lore"):
            provider = OpenAICompatibleChapterReviewProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            try:
                target_doc_id = uuid4()
                target_ver_id = uuid4()
                review_segments = tuple(
                    ReviewSegmentSnapshot(
                        segment_id=uuid4(),
                        index=idx,
                        title=seg["title"],
                        content=seg["content"],
                    )
                    for idx, seg in enumerate(candidate_segments, 1)
                )
                target = ChapterReviewTarget(
                    project_id=project_id,
                    chapter_id=chapter_id,
                    document_id=target_doc_id,
                    version_id=target_ver_id,
                    segments=review_segments,
                )
                approved_outline = ApprovedOutlineSnapshot(
                    document_id=outline_doc_id,
                    version_id=outline_ver_id,
                    project_id=project_id,
                    chapter_id=chapter_id,
                    content=outline_text,
                )

                if role == "editor":
                    profile = registry.load("editor_agent", mode=None)
                    review_request = EditorReviewRequest(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=workflow_run_id,
                        target=target,
                        approved_outline=approved_outline,
                        contexts=(
                            ReviewContextSnapshot(
                                document_id=uuid4(),
                                version_id=uuid4(),
                                project_id=project_id,
                                kind=ReviewContextKind.STYLE_GUIDE,
                                content="Maintain period atmospheric immersion.",
                            ),
                        ),
                    )
                    result = await provider.review_editor(review_request, profile)
                elif role == "chief_editor":
                    profile = registry.load("chief_editor", mode="chapter_final")
                    review_request = ChiefEditorChapterFinalRequest(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=workflow_run_id,
                        target=target,
                        approved_outline=approved_outline,
                        contexts=(
                            ReviewContextSnapshot(
                                document_id=uuid4(),
                                version_id=uuid4(),
                                project_id=project_id,
                                kind=ReviewContextKind.AUDIENCE_GOAL,
                                content="Deliver a gripping detective climax.",
                            ),
                        ),
                    )
                    result = await provider.review_chief_final(review_request, profile)
                else:  # lore
                    profile = registry.load("lore_agent", mode="chapter_final")
                    review_request = LoreChapterFinalRequest(
                        project_id=project_id,
                        chapter_id=chapter_id,
                        workflow_run_id=workflow_run_id,
                        target=target,
                        approved_outline=approved_outline,
                        contexts=(
                            ReviewContextSnapshot(
                                document_id=uuid4(),
                                version_id=uuid4(),
                                project_id=project_id,
                                kind=ReviewContextKind.LORE_BOUNDARY,
                                content="Steam mechanical core rules.",
                            ),
                        ),
                    )
                    result = await provider.review_lore_final(review_request, profile)

                elapsed = time.monotonic() - start_time
                print(f"[SUCCESS] Review completed in {elapsed:.2f}s")
                print(f"Passed: {result.passed}")
                print(f"Summary: {result.summary[:120]}...")
                print(f"Findings Count: {len(result.findings)}")
                for f in result.findings[:3]:
                    print(f"  - [{f.severity.value}] {f.code}: {f.suggested_action[:80]}")
                print(f"Input Tokens: {provider.last_input_tokens}, Output Tokens: {provider.last_output_tokens}")
                return 0
            finally:
                await provider.aclose()

        elif role == "concept":
            provider = OpenAICompatibleConceptProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            try:
                concept_req = ConceptAgentRequest(
                    user_seed="A clockwork detective in Victorian London investigates an impossible vault murder.",
                    target_platform="Webnovel",
                    preferred_genres=["mystery", "steampunk"],
                    disliked_elements=["harem", "litrpg"],
                    style_preference="Victorian gothic tone",
                )
                profile = registry.load("concept_agent", mode=None)
                concept_res = await provider.generate_concepts(concept_req, profile)
                elapsed = time.monotonic() - start_time
                print(
                    f"[SUCCESS] Concept generation produced {len(concept_res.options)} options in {elapsed:.2f}s"
                )
                for opt in concept_res.options:
                    print(f"  - [{opt.id}] {opt.title}: {opt.logline[:80]}...")
                print(
                    f"Input Tokens: {provider.last_input_tokens}, Output Tokens: {provider.last_output_tokens}"
                )
                return 0
            finally:
                await provider.aclose()

        elif role == "concept_review":
            provider = OpenAICompatibleConceptChiefEditorProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            try:
                concepts = ConceptGenerationOutput(
                    options=[
                        ConceptOption(
                            id="clockwork-detective",
                            title="The Brass Key of Whitechapel",
                            logline="A disgraced automaton investigator uncovers an aristocratic conspiracy behind a locked-room death.",
                            premise="In an alternate 1888 London, clockwork enforcers maintain the peace until a human noble is found murdered inside an airtight vault.",
                            genres=["mystery", "steampunk"],
                        )
                    ]
                )
                profile = registry.load("chief_editor", mode=None)
                review_res = await provider.review_concepts(concepts, profile)
                elapsed = time.monotonic() - start_time
                print(f"[SUCCESS] Concept review completed in {elapsed:.2f}s")
                print(f"Passed: {review_res.passed}")
                print(f"Summary: {review_res.summary[:120]}...")
                print(
                    f"Blocking Issues: {len(review_res.blocking_issues)}, Warnings: {len(review_res.warnings)}"
                )
                print(
                    f"Input Tokens: {provider.last_input_tokens}, Output Tokens: {provider.last_output_tokens}"
                )
                return 0
            finally:
                await provider.aclose()

        elif role in (
            "maintenance_impact",
            "revision_plan",
            "archivist",
            "consistency_review",
        ):
            provider = OpenAICompatibleMaintenanceProvider(
                base_url=base_url,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout_seconds,
            )
            try:
                doc_id = uuid4()
                ver_id = uuid4()
                doc_ref = DocumentVersionReference(
                    document_id=doc_id, current_version_id=ver_id
                )

                if role == "maintenance_impact":
                    profile = registry.load("lore_agent", mode="maintenance_impact")
                    maint_impact_req = MaintenanceImpactRequest(
                        project_id=project_id,
                        workflow_run_id=workflow_run_id,
                        change_request_id=uuid4(),
                        change_request="Introduce a secret underground resistance faction in the lower wards.",
                        document_refs=(doc_ref,),
                    )
                    impact_res = await provider.analyze_maintenance_impact(
                        maint_impact_req, profile
                    )
                    elapsed = time.monotonic() - start_time
                    print(
                        f"[SUCCESS] Maintenance impact analysis completed in {elapsed:.2f}s"
                    )
                    print(f"Safe to change: {impact_res.safe_to_change}")
                    print(f"Summary: {impact_res.impact_summary[:120]}...")
                    print(
                        f"Affected items: {len(impact_res.affected_items)}, Warnings: {len(impact_res.warnings)}"
                    )

                elif role == "revision_plan":
                    profile = registry.load("plot_architect_agent", mode="revision_plan")
                    item_id = uuid4()
                    affected_item = AffectedItemReference(
                        affected_item_id=item_id,
                        stable_reference="world/underground-faction",
                        item_type=AffectedItemType.WORLD,
                        impact_level=ImpactLevel.MEDIUM,
                        document=doc_ref,
                        reason="The faction alters existing district governance canon.",
                    )
                    revision_req = RevisionPlanRequest(
                        project_id=project_id,
                        workflow_run_id=workflow_run_id,
                        change_request_id=uuid4(),
                        change_request="Integrate the underground faction into district lore documents.",
                        affected_items=(affected_item,),
                        document_refs=(doc_ref,),
                    )
                    plan_res = await provider.plan_revision(revision_req, profile)
                    elapsed = time.monotonic() - start_time
                    print(f"[SUCCESS] Revision plan completed in {elapsed:.2f}s")
                    print(f"Operations count: {len(plan_res.operations)}")
                    print(f"Summary: {plan_res.summary[:120]}...")

                elif role == "archivist":
                    profile = registry.load("archivist_agent", mode="apply_change")
                    op_id = uuid4()
                    item_id = uuid4()
                    operation = RevisionOperation(
                        operation_id=op_id,
                        sequence=1,
                        operation=RevisionOperationKind.REVISE,
                        target=doc_ref,
                        affected_item_ids=(item_id,),
                        instruction="Append section detailing the underground faction contacts.",
                    )
                    archivist_req = ApplyChangeRequest(
                        project_id=project_id,
                        workflow_run_id=workflow_run_id,
                        change_request_id=uuid4(),
                        approval_id=uuid4(),
                        revision_plan_id=uuid4(),
                        revision_plan_document_id=uuid4(),
                        revision_plan_version_id=uuid4(),
                        operations=(operation,),
                    )
                    archivist_res = await provider.propose_changes(
                        archivist_req, profile
                    )
                    elapsed = time.monotonic() - start_time
                    print(f"[SUCCESS] Archivist proposal completed in {elapsed:.2f}s")
                    print(f"Proposed edits count: {len(archivist_res.proposed_edits)}")
                    for edit in archivist_res.proposed_edits:
                        print(
                            f"  - Edit for doc {edit.document_id}: {edit.rationale[:80]}..."
                        )

                else:  # consistency_review
                    profile = registry.load("lore_agent", mode="post_change")
                    applied_change = AppliedDocumentReference(
                        proposed_edit_id=uuid4(),
                        document_id=doc_id,
                        previous_version_id=ver_id,
                        current_version_id=uuid4(),
                    )
                    post_req = PostChangeRequest(
                        project_id=project_id,
                        workflow_run_id=workflow_run_id,
                        change_request_id=uuid4(),
                        approval_id=uuid4(),
                        revision_plan_id=uuid4(),
                        revision_plan_document_id=uuid4(),
                        revision_plan_version_id=uuid4(),
                        change_set_id=uuid4(),
                        applied_changes=(applied_change,),
                    )
                    review_res = await provider.review_consistency(post_req, profile)
                    elapsed = time.monotonic() - start_time
                    print(f"[SUCCESS] Consistency review completed in {elapsed:.2f}s")
                    print(f"Outcome: {review_res.outcome.value}")
                    print(f"Findings count: {len(review_res.findings)}")

                print(
                    f"Input Tokens: {provider.last_input_tokens}, Output Tokens: {provider.last_output_tokens}"
                )
                return 0
            finally:
                await provider.aclose()

        else:
            print(f"[ERROR] Unsupported role: {role}", file=sys.stderr)
            return 1

    except Exception as exc:
        print(f"[FAILURE] Model call failed with {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bounded, explicit smoke test for real OpenAI-compatible models."
    )
    parser.add_argument(
        "--enable-real-model",
        action="store_true",
        default=os.environ.get("GURANOVEL_SMOKE_REAL_MODEL") == "1",
        help="Explicitly enable real model API calls (guards against accidental budget consumption).",
    )
    parser.add_argument(
        "--role",
        choices=[
            "writer",
            "editor",
            "chief_editor",
            "lore",
            "concept",
            "concept_review",
            "maintenance_impact",
            "revision_plan",
            "archivist",
            "consistency_review",
        ],
        default="writer",
        help="Agent role to invoke (default: writer).",
    )
    parser.add_argument(
        "--sample",
        choices=[s.value for s in BenchmarkSampleId],
        default=BenchmarkSampleId.SAMPLE_4_QUALIFIED.value,
        help="Benchmark sample case to use (default: sample_4_qualified).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Timeout in seconds (default: 60.0).",
    )
    args = parser.parse_args()

    if not args.enable_real_model:
        print(
            "Smoke runner safety guard: real model calls are disabled.\n"
            "To execute a single cost-bounded invocation against an active model endpoint, provide:\n"
            "  --enable-real-model (or set GURANOVEL_SMOKE_REAL_MODEL=1)\n"
            "and configure OPENAI_COMPATIBLE_BASE_URL, OPENAI_COMPATIBLE_API_KEY, and OPENAI_COMPATIBLE_MODEL.",
            file=sys.stderr,
        )
        sys.exit(3)

    base_url = settings.openai_compatible_base_url or os.environ.get("OPENAI_COMPATIBLE_BASE_URL")
    api_key_secret = settings.openai_compatible_api_key
    api_key = api_key_secret.get_secret_value() if api_key_secret else os.environ.get("OPENAI_COMPATIBLE_API_KEY")
    model = settings.openai_compatible_model or os.environ.get("OPENAI_COMPATIBLE_MODEL")

    if not base_url or not api_key or not model:
        print(
            "Configuration error: OPENAI_COMPATIBLE_BASE_URL, OPENAI_COMPATIBLE_API_KEY, "
            "and OPENAI_COMPATIBLE_MODEL must all be configured.",
            file=sys.stderr,
        )
        sys.exit(4)

    exit_code = asyncio.run(
        run_smoke(
            role=args.role,
            sample_id=BenchmarkSampleId(args.sample),
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=args.timeout,
        )
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
