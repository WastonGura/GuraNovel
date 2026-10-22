import pytest
from pydantic import ValidationError

from app.api.schemas_setting_restore_points import UpdateSettingDocumentRestorePointMessageRequest


def test_archive_message_accepts_the_160_character_boundary_and_normalizes_both_fields():
    request = UpdateSettingDocumentRestorePointMessageRequest(
        message="界" * 160, expected_message="  Earlier explanation  "
    )
    assert request.message == "界" * 160
    assert request.expected_message == "Earlier explanation"
    empty = UpdateSettingDocumentRestorePointMessageRequest(message="  ", expected_message=None)
    assert empty.model_dump() == {"message": None, "expected_message": None}


@pytest.mark.parametrize("bad_base", ["x" * 161, "first\nsecond", "control\x00text"])
def test_archive_message_rejects_invalid_expected_base_too(bad_base):
    with pytest.raises(ValidationError):
        UpdateSettingDocumentRestorePointMessageRequest(
            message="Valid target", expected_message=bad_base
        )
