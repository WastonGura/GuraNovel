from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
FORBIDDEN_EXACT = (
    "sqlalchemy",
    "app.services.document_service",
    "app.services.chapter_production_v2_service",
    "app.services.chapter_production_repository",
    "app.services.provider_attempt_store",
)
FORBIDDEN_PREFIXES = (
    "sqlalchemy.",
    "app.agents",
    "app.llm",
    "app.workspace",
)


def _fresh_python(source: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=BACKEND,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_importing_services_package_does_not_eagerly_load_exports() -> None:
    result = _fresh_python(
        "import sys\n"
        "import app.services as services\n"
        f"exact={FORBIDDEN_EXACT!r}; prefixes={FORBIDDEN_PREFIXES!r}\n"
        "assert all(name not in exact and not name.startswith(prefixes) "
        "for name in sys.modules)\n"
        "assert 'DocumentService' not in vars(services)\n"
        "from app.services import DocumentService\n"
        "from app.services.document_service import DocumentService as expected\n"
        "assert DocumentService is expected\n"
    )

    assert result.returncode == 0, result.stderr


def test_importing_pure_attempt_contracts_keeps_database_authority_unloaded() -> None:
    result = _fresh_python(
        "import sys\n"
        "import app.services.provider_attempt_contracts\n"
        f"exact={FORBIDDEN_EXACT!r}; prefixes={FORBIDDEN_PREFIXES!r}\n"
        "assert all(name not in exact and not name.startswith(prefixes) "
        "for name in sys.modules)\n"
    )

    assert result.returncode == 0, result.stderr


def test_concurrent_first_export_resolution_is_deadlock_free() -> None:
    names = (
        "DocumentService", "ChapterService", "ChapterProductionService",
        "ChapterProductionV2Service", "ProjectService", "ProjectCreationService",
        "ProjectMaintenanceFoundationService", "MaintenanceChangeService",
        "ProjectMaintenanceService",
    )
    result = _fresh_python(
        "import threading\n"
        "import app.services as services\n"
        f"names={names!r}\n"
        "barrier=threading.Barrier(len(names)); errors=[]\n"
        "def load(name):\n"
        "  try:\n"
        "    barrier.wait(); getattr(services, name)\n"
        "  except BaseException as error:\n"
        "    errors.append(type(error).__name__)\n"
        "threads=[threading.Thread(target=load,args=(name,)) for name in names]\n"
        "[thread.start() for thread in threads]\n"
        "[thread.join(30) for thread in threads]\n"
        "assert not any(thread.is_alive() for thread in threads), 'hung'\n"
        "assert not errors, errors\n"
    )

    assert result.returncode == 0, result.stderr
