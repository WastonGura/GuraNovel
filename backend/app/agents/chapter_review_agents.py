"""Injected provider boundaries for advisory chapter review agents."""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import ValidationError

from app.agents.chapter_review_contracts import (
    ChapterReviewReport,
    ChapterReviewRequest,
    ChiefEditorChapterFinalRequest,
    EditorReviewRequest,
    LoreChapterFinalRequest,
    ReviewerRole,
    validate_chapter_review_report,
)
from app.agents.errors import ProfileRegistryError
from app.agents.profiles import AgentProfile, ProfileRegistry
from app.llm.errors import (
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


_SAFE_PROVIDER_ERRORS = (
    ProfileRegistryError,
    ProviderConfigurationError,
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def _safe_error_type(error: Exception) -> type[Exception]:
    for error_type in _SAFE_PROVIDER_ERRORS:
        if isinstance(error, error_type):
            return error_type
    return ProviderUnavailableError


_ReviewRequestT = TypeVar("_ReviewRequestT", bound=ChapterReviewRequest)


@runtime_checkable
class EditorReviewProvider(Protocol):
    async def review_editor(
        self, request: EditorReviewRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class ChiefEditorChapterFinalProvider(Protocol):
    async def review_chief_final(
        self, request: ChiefEditorChapterFinalRequest, profile: AgentProfile
    ) -> object: ...


@runtime_checkable
class LoreChapterFinalProvider(Protocol):
    async def review_lore_final(
        self, request: LoreChapterFinalRequest, profile: AgentProfile
    ) -> object: ...


class _ChapterReviewAgent:
    def __init__(self, provider: object, registry: ProfileRegistry | None = None) -> None:
        self._provider = provider
        self._registry = registry or ProfileRegistry()

    @staticmethod
    def _validated_request(request: object, request_type: type[_ReviewRequestT]) -> _ReviewRequestT:
        if type(request) is not request_type:
            raise ProviderConfigurationError() from None
        try:
            return request_type.model_validate(request.model_dump(mode="json", warnings="none"))
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise ProviderConfigurationError() from None

    @staticmethod
    def _validate_output(
        raw_output: object,
        *,
        request: ChapterReviewRequest,
        reviewer_role: ReviewerRole,
        review_mode: str,
    ) -> ChapterReviewReport:
        return validate_chapter_review_report(
            raw_output,
            request=request,
            reviewer_role=reviewer_role,
            mode=review_mode,
        )

    async def _invoke(
        self,
        *,
        request: ChapterReviewRequest,
        request_type: type[ChapterReviewRequest],
        provider_type: type[object],
        provider_method: str,
        profile_name: str,
        reviewer_role: ReviewerRole,
        profile_mode: str | None,
        review_mode: str,
    ) -> ChapterReviewReport:
        result: ChapterReviewReport | None = None
        failure: type[Exception] | None = None
        try:
            validated = self._validated_request(request, request_type)
            if not isinstance(self._provider, provider_type):
                raise ProviderConfigurationError()
            profile = self._registry.load(profile_name, profile_mode)
            raw_output = await getattr(self._provider, provider_method)(validated, profile)
            result = self._validate_output(
                raw_output,
                request=validated,
                reviewer_role=reviewer_role,
                review_mode=review_mode,
            )
        except Exception as error:
            failure = _safe_error_type(error)
        if failure is not None:
            raise failure() from None
        if result is None:
            raise ProviderUnavailableError() from None
        return result


class EditorAgent(_ChapterReviewAgent):
    @staticmethod
    def validate_output(raw_output: object, *, request: EditorReviewRequest) -> ChapterReviewReport:
        return _ChapterReviewAgent._validate_output(
            raw_output,
            request=request,
            reviewer_role=ReviewerRole.EDITOR,
            review_mode="chapter_editor",
        )

    async def review(self, request: EditorReviewRequest) -> ChapterReviewReport:
        return await self._invoke(
            request=request,
            request_type=EditorReviewRequest,
            provider_type=EditorReviewProvider,
            provider_method="review_editor",
            profile_name="editor_agent",
            reviewer_role=ReviewerRole.EDITOR,
            profile_mode=None,
            review_mode="chapter_editor",
        )


class ChiefEditorChapterFinalAgent(_ChapterReviewAgent):
    @staticmethod
    def validate_output(
        raw_output: object, *, request: ChiefEditorChapterFinalRequest
    ) -> ChapterReviewReport:
        return _ChapterReviewAgent._validate_output(
            raw_output,
            request=request,
            reviewer_role=ReviewerRole.CHIEF_EDITOR,
            review_mode="chapter_chief_final",
        )

    async def review(self, request: ChiefEditorChapterFinalRequest) -> ChapterReviewReport:
        return await self._invoke(
            request=request,
            request_type=ChiefEditorChapterFinalRequest,
            provider_type=ChiefEditorChapterFinalProvider,
            provider_method="review_chief_final",
            profile_name="chief_editor",
            reviewer_role=ReviewerRole.CHIEF_EDITOR,
            profile_mode="chapter_final",
            review_mode="chapter_chief_final",
        )


class LoreChapterFinalAgent(_ChapterReviewAgent):
    @staticmethod
    def validate_output(
        raw_output: object, *, request: LoreChapterFinalRequest
    ) -> ChapterReviewReport:
        return _ChapterReviewAgent._validate_output(
            raw_output,
            request=request,
            reviewer_role=ReviewerRole.LORE,
            review_mode="chapter_final_lore",
        )

    async def review(self, request: LoreChapterFinalRequest) -> ChapterReviewReport:
        return await self._invoke(
            request=request,
            request_type=LoreChapterFinalRequest,
            provider_type=LoreChapterFinalProvider,
            provider_method="review_lore_final",
            profile_name="lore_agent",
            reviewer_role=ReviewerRole.LORE,
            profile_mode="chapter_final",
            review_mode="chapter_final_lore",
        )


__all__ = [
    "ChiefEditorChapterFinalAgent",
    "ChiefEditorChapterFinalProvider",
    "EditorAgent",
    "EditorReviewProvider",
    "LoreChapterFinalAgent",
    "LoreChapterFinalProvider",
]
