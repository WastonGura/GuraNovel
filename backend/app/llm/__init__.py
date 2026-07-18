"""Provider-neutral language-model application boundaries."""

from app.llm.contracts import (
    ChapterGenerationProvider,
    ChapterGenerationRequest,
    ChapterGenerationResult,
)
from app.llm.fake_provider import FakeChapterGenerationProvider
from app.llm.errors import (
    ProviderInvalidOutputError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

__all__ = [
    "ChapterGenerationProvider",
    "ChapterGenerationRequest",
    "ChapterGenerationResult",
    "FakeChapterGenerationProvider",
    "ProviderInvalidOutputError",
    "ProviderRateLimitedError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
]
