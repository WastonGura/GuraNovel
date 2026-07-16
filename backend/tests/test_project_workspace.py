from pathlib import Path

import pytest

from app.workspace.project_workspace import ProjectWorkspace, UnsafeProjectWorkspaceError


def test_workspace_root_is_derived_from_the_configured_base_and_slug(tmp_path: Path) -> None:
    workspace = ProjectWorkspace(tmp_path / "configured-workspaces")

    assert workspace.root_for("my-novel") == tmp_path / "configured-workspaces" / "my-novel"


@pytest.mark.parametrize(
    "slug",
    [
        "",
        ".",
        "..",
        "draft/../novel",
        "draft/novel",
        r"draft\\novel",
        "/tmp/novel",
        "C:novel",
        r"C:\\novel",
        r"\\\\server\\share\\novel",
    ],
)
def test_workspace_root_rejects_unsafe_slug_forms(tmp_path: Path, slug: str) -> None:
    workspace = ProjectWorkspace(tmp_path / "configured-workspaces")

    with pytest.raises(UnsafeProjectWorkspaceError):
        workspace.root_for(slug)


def test_create_makes_exact_standard_project_workspace_layout(tmp_path: Path) -> None:
    base_dir = tmp_path / "configured-workspaces"
    workspace = ProjectWorkspace(base_dir)

    root = workspace.create("my-novel")

    assert root == base_dir / "my-novel"
    assert {path.name for path in root.iterdir()} == {
        "outline",
        "lore",
        "characters",
        "chapters",
        ".versions",
    }
    assert all(path.is_dir() for path in root.iterdir())


def test_create_is_idempotent_and_preserves_existing_workspace_contents(tmp_path: Path) -> None:
    workspace = ProjectWorkspace(tmp_path / "configured-workspaces")
    root = workspace.create("my-novel")
    chapter = root / "chapters" / "chapter-01.md"
    chapter.write_text("A chapter", encoding="utf-8")

    repeated_root = workspace.create("my-novel")

    assert repeated_root == root
    assert chapter.read_text(encoding="utf-8") == "A chapter"


@pytest.mark.parametrize("attack_target", ["base", "workspace"])
def test_create_rejects_symlinked_base_or_workspace_path(
    tmp_path: Path, attack_target: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    base_dir = tmp_path / "configured-workspaces"
    if attack_target == "base":
        base_dir.symlink_to(outside, target_is_directory=True)
    else:
        base_dir.mkdir()
        (base_dir / "my-novel").symlink_to(outside, target_is_directory=True)
    workspace = ProjectWorkspace(base_dir)

    with pytest.raises(OSError):
        workspace.create("my-novel")

    assert list(outside.iterdir()) == []
