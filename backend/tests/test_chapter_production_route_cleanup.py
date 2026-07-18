from uuid import uuid4

import pytest

from app.api.deps import ChapterGenerationComposition
import app.api.routes_chapter_production as production_routes
from app.llm import ChapterGenerationProvenance, ProviderTimeoutError, ProviderUnavailableError


class _ProviderWithFailingClose:
    async def aclose(self) -> None:
        raise RuntimeError("upstream close detail")


def _generation() -> ChapterGenerationComposition:
    return ChapterGenerationComposition(
        _ProviderWithFailingClose(),  # type: ignore[arg-type]
        ChapterGenerationProvenance("fake", "deterministic-fake-v1", "chapter-production-v1"),
    )


@pytest.mark.anyio
async def test_start_normalizes_close_failure_after_success_and_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SuccessfulService:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def start_production(self, *_: object) -> object:
            return type("Started", (), {"workflow_run_id": uuid4()})()

        async def get_production_run(self, *_: object) -> str:
            return "completed"

    monkeypatch.setattr(production_routes, "ChapterProductionService", SuccessfulService)
    with pytest.raises(ProviderUnavailableError) as error:
        await production_routes.start_chapter_production(
            uuid4(), uuid4(), session=object(), generation=_generation()
        )
    assert "upstream close detail" not in str(error.value)

    class FailingService(SuccessfulService):
        async def start_production(self, *_: object) -> object:
            raise ProviderTimeoutError()

    monkeypatch.setattr(production_routes, "ChapterProductionService", FailingService)
    with pytest.raises(ProviderTimeoutError):
        await production_routes.start_chapter_production(
            uuid4(), uuid4(), session=object(), generation=_generation()
        )
