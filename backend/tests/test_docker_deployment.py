from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _lines(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def test_docker_build_contexts_exclude_local_and_secret_state() -> None:
    assert {
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".env",
        ".env.*",
        ".envrc",
        ".git",
    } <= _lines(ROOT / "backend/.dockerignore")
    assert {
        "node_modules",
        "dist",
        "coverage",
        ".env",
        ".env.*",
        ".envrc",
        ".npmrc",
        ".git",
    } <= _lines(ROOT / "frontend/.dockerignore")


def test_backend_image_installs_and_runs_only_runtime_dependencies() -> None:
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8")
    sync_steps = [line for line in dockerfile.splitlines() if line.startswith("RUN uv sync")]

    assert sync_steps
    assert all("--no-dev" in line for line in sync_steps)
    assert "uv run" not in dockerfile
    assert "/app/.venv/bin/alembic upgrade head" in dockerfile
    assert "/app/.venv/bin/uvicorn app.main:app" in dockerfile


def test_compose_avoids_database_port_conflicts_and_allows_public_port_overrides() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]

    assert "ports" not in services["db"]
    assert services["backend"]["ports"] == [
        "${GURANOVEL_BIND_ADDRESS:-127.0.0.1}:${GURANOVEL_BACKEND_PORT:-8000}:8000"
    ]
    assert services["frontend"]["ports"] == [
        "${GURANOVEL_BIND_ADDRESS:-127.0.0.1}:${GURANOVEL_FRONTEND_PORT:-5173}:80"
    ]


def test_frontend_waits_for_a_healthy_backend() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))[
        "services"
    ]
    healthcheck = services["backend"]["healthcheck"]

    assert healthcheck["test"][0:2] == ["CMD", "/app/.venv/bin/python"]
    assert "urllib.request" in healthcheck["test"][3]
    assert "127.0.0.1:8000/api/v1/health" in healthcheck["test"][3]
    assert services["frontend"]["depends_on"] == {
        "backend": {"condition": "service_healthy"}
    }


def test_readmes_document_observable_compose_lifecycle() -> None:
    for name in ("README.md", "README.en.md"):
        readme = (ROOT / name).read_text(encoding="utf-8")

        assert "docker compose up -d --build --wait" in readme
        assert "docker compose ps" in readme
        assert "docker compose logs --tail=100 backend frontend db" in readme
        assert (
            "curl --noproxy '*' -fsS http://127.0.0.1:8000/api/v1/health" in readme
        )
        assert (
            "curl --noproxy '*' -fsS http://127.0.0.1:5173/api/v1/health" in readme
        )
        assert "curl -fsS http://localhost:" not in readme
        assert "GURANOVEL_BACKEND_PORT" in readme
        assert "GURANOVEL_FRONTEND_PORT" in readme
        assert "GURANOVEL_BIND_ADDRESS=0.0.0.0" in readme
        assert "127.0.0.1" in readme
        assert "docker compose down" in readme
        assert "docker compose down -v" in readme
