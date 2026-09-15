"""Unit tests verifying all studio_* reader personas are registered and loadable."""

import pytest
from app.agents.profiles import ProfileRegistry, _STUDIO_READER_PROFILE_MAP


STUDIO_READER_IDS = [
    "studio_plot",
    "studio_character",
    "studio_world",
    "studio_emotion",
    "studio_language",
    "studio_casual",
]


@pytest.mark.parametrize("persona_id", STUDIO_READER_IDS)
def test_studio_reader_profile_loads_successfully(persona_id: str):
    registry = ProfileRegistry()
    profile = registry.load(persona_id)
    assert profile is not None
    assert profile.agent_role == "reader_agent"
    assert profile.output_schema == "reader_panel_output"
    assert profile.name == _STUDIO_READER_PROFILE_MAP[persona_id]
    assert profile.description
    assert profile.model.provider in ("openai_compatible", "fake")
