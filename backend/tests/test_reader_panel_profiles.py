"""Unit tests for built-in Reader Panel agent profiles and permission bounds."""

from __future__ import annotations

import pytest

from app.agents.profiles import ProfileRegistry


class TestReaderPanelProfiles:
    @pytest.fixture
    def registry(self) -> ProfileRegistry:
        return ProfileRegistry()

    @pytest.mark.parametrize(
        "profile_id, expected_role",
        [
            ("general_immersive", "reader_agent"),
            ("low_patience", "reader_agent"),
            ("genre_experienced", "reader_agent"),
            ("character_emotion", "reader_agent"),
            ("style_sensitive", "reader_agent"),
            ("newcomer", "reader_agent"),
        ],
    )
    def test_reader_profiles_load_and_have_safe_permissions(
        self, registry: ProfileRegistry, profile_id: str, expected_role: str
    ) -> None:
        profile = registry.load(profile_id)
        assert profile.name == profile_id
        assert profile.agent_role == expected_role
        assert profile.permissions.can_write == []
        assert "network" in profile.permissions.cannot
        assert "database" in profile.permissions.cannot
        assert "filesystem" in profile.permissions.cannot
        assert "workflow_transitions" in profile.permissions.cannot

    @pytest.mark.parametrize(
        "moderator_mode",
        [
            "issue_extraction",
            "discussion_summary",
            "report_synthesis",
        ],
    )
    def test_moderator_profiles_load_and_cannot_vote_or_transition(
        self, registry: ProfileRegistry, moderator_mode: str
    ) -> None:
        profile = registry.load("moderator_agent", moderator_mode)
        assert profile.name == "moderator_agent"
        assert profile.mode == moderator_mode
        assert profile.agent_role == "moderator_agent"
        assert profile.permissions.can_write == []
        assert "network" in profile.permissions.cannot
        assert "database" in profile.permissions.cannot
        assert "workflow_transitions" in profile.permissions.cannot
        assert "vote" in profile.permissions.cannot
