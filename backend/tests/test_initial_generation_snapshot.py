from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.chapter_production_v2_contracts import ChapterProductionV2ValidationError
from app.services.initial_generation_snapshot import (
    InitialGenerationScope,
    InitialGenerationSnapshot,
)
from app.services.provider_attempt_contracts import ProviderAttempt


def _parts() -> tuple[InitialGenerationScope, ProviderAttempt]:
    attempt_id = uuid4()
    key = "a" * 64
    return (
        InitialGenerationScope(
            uuid4(), uuid4(), uuid4(), uuid4(), key, 0, attempt_id
        ),
        ProviderAttempt.initial(
            attempt_id=attempt_id, operation_key=key, checkpoint_index=0
        ),
    )


def test_snapshot_is_content_safe_pure_identity() -> None:
    snapshot = InitialGenerationSnapshot(*_parts())

    assert repr(snapshot) == "InitialGenerationSnapshot()"
    assert repr(snapshot.scope) == "InitialGenerationScope()"
    assert not hasattr(snapshot, "__dict__")


@pytest.mark.parametrize("mismatch", ("key", "checkpoint", "token"))
def test_snapshot_rejects_mixed_generation(mismatch: str) -> None:
    scope, attempt = _parts()
    changed = ProviderAttempt.initial(
        attempt_id=uuid4() if mismatch == "token" else attempt.attempt_id,
        operation_key="b" * 64 if mismatch == "key" else attempt.operation_key,
        checkpoint_index=1 if mismatch == "checkpoint" else attempt.checkpoint_index,
    )
    with pytest.raises(ChapterProductionV2ValidationError):
        InitialGenerationSnapshot(scope, changed)
