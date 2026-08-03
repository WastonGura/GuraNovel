"""Provider-neutral language-model application boundaries."""

from app.llm.contracts import (
    ChapterGenerationProvider,
    ChapterGenerationProvenance,
    ChapterGenerationRequest,
    ChapterGenerationResponse,
    ChapterGenerationResult,
    GatewayChapterGenerationProvider,
    RawChapterGenerationOutput,
    chapter_generation_profile,
    validate_chapter_generation_output,
    validate_chapter_generation_response,
)
from app.llm.fake_provider import FakeChapterGenerationProvider
from app.llm.gateway import (
    StructuredOutputGateway,
    StructuredOutputProfile,
    StructuredOutputProvenance,
    StructuredOutputRequest,
    StructuredOutputResponse,
)
from app.llm.openai_compatible_provider import OpenAICompatibleChapterGenerationProvider
from app.llm.errors import (
    ProviderInvalidOutputError,
    ProviderConfigurationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

__all__ = [
    "ChapterGenerationProvider",
    "ChapterGenerationProvenance",
    "ChapterGenerationRequest",
    "ChapterGenerationResponse",
    "ChapterGenerationResult",
    "FakeChapterGenerationProvider",
    "GatewayChapterGenerationProvider",
    "OpenAICompatibleChapterGenerationProvider",
    "ProviderInvalidOutputError",
    "ProviderConfigurationError",
    "ProviderRateLimitedError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RawChapterGenerationOutput",
    "StructuredOutputGateway",
    "StructuredOutputProfile",
    "StructuredOutputProvenance",
    "StructuredOutputRequest",
    "StructuredOutputResponse",
    "chapter_generation_profile",
    "validate_chapter_generation_output",
    "validate_chapter_generation_response",
]
