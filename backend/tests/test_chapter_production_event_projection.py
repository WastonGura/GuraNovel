from uuid import uuid4

import pytest

from app.services.chapter_production_service import ChapterProductionService


@pytest.mark.parametrize("event_type", ["generation_output_stored", "fake_output_stored"])
def test_output_stored_events_project_only_a_canonical_outline_document_id(event_type: str) -> None:
    outline_document_id = str(uuid4())

    assert ChapterProductionService._public_event_payload(  # noqa: SLF001
        event_type,
        {"outline_document_id": outline_document_id, "provider_identity": "must-not-leak"},
    ) == {"outline_document_id": outline_document_id}


@pytest.mark.parametrize("event_type", ["generation_output_stored", "fake_output_stored"])
@pytest.mark.parametrize("outline_document_id", [None, "not-a-uuid", str(uuid4()).upper()])
def test_output_stored_events_fail_closed_for_noncanonical_outline_document_ids(
    event_type: str, outline_document_id: str | None
) -> None:
    # Legacy fake_output_stored rows remain readable only with the same canonical UUID contract.
    assert ChapterProductionService._public_event_payload(  # noqa: SLF001
        event_type, {"outline_document_id": outline_document_id, "raw_output": "must-not-leak"}
    ) == {}
