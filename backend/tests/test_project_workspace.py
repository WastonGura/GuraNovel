import os
import stat
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


@pytest.mark.parametrize("slug", [None, 25, "my-novel\x00hidden"])
def test_workspace_root_rejects_non_string_or_nul_slug(
    tmp_path: Path, slug: object
) -> None:
    workspace = ProjectWorkspace(tmp_path / "configured-workspaces")

    with pytest.raises(UnsafeProjectWorkspaceError):
        workspace.root_for(slug)  # type: ignore[arg-type]


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


def test_create_makes_service_private_workspace_directories(tmp_path: Path) -> None:
    base_dir = tmp_path / "configured-workspaces"
    workspace = ProjectWorkspace(base_dir)

    root = workspace.create("my-novel")

    for directory in (base_dir, root, *(root / name for name in workspace._STANDARD_DIRECTORIES)):
        directory_status = directory.stat()
        assert directory_status.st_uid == os.geteuid()
        assert stat.S_IMODE(directory_status.st_mode) == 0o700


@pytest.mark.parametrize("target", ["base", "root", "child"])
def test_create_rejects_existing_group_or_world_writable_managed_directories(
    tmp_path: Path, target: str
) -> None:
    base_dir = tmp_path / "configured-workspaces"
    base_dir.mkdir(mode=0o700)
    root = base_dir / "my-novel"
    if target in {"root", "child"}:
        root.mkdir(mode=0o700)
    if target == "child":
        (root / "chapters").mkdir(mode=0o700)

    insecure_directory = {
        "base": base_dir,
        "root": root,
        "child": root / "chapters",
    }[target]
    insecure_directory.chmod(0o770)

    with pytest.raises(UnsafeProjectWorkspaceError, match="service-private"):
        ProjectWorkspace(base_dir).create("my-novel")


def test_create_rejects_existing_managed_directory_not_owned_by_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_dir = tmp_path / "configured-workspaces"
    base_dir.mkdir(mode=0o700)
    original_fstat = os.fstat

    def fake_base_owner(descriptor: int) -> os.stat_result:
        directory_status = original_fstat(descriptor)
        if os.path.samefile(f"/proc/self/fd/{descriptor}", base_dir):
            return os.stat_result(
                (
                    directory_status.st_mode,
                    directory_status.st_ino,
                    directory_status.st_dev,
                    directory_status.st_nlink,
                    os.geteuid() + 1,
                    directory_status.st_gid,
                    directory_status.st_size,
                    directory_status.st_atime,
                    directory_status.st_mtime,
                    directory_status.st_ctime,
                )
            )
        return directory_status

    monkeypatch.setattr("app.workspace.project_workspace.os.fstat", fake_base_owner)

    with pytest.raises(UnsafeProjectWorkspaceError, match="service-private"):
        ProjectWorkspace(base_dir).create("my-novel")


def test_create_rejects_base_beneath_a_non_sticky_writable_ancestor(tmp_path: Path) -> None:
    writable_ancestor = tmp_path / "writable-ancestor"
    writable_ancestor.mkdir(mode=0o700)
    writable_ancestor.chmod(0o777)

    with pytest.raises(UnsafeProjectWorkspaceError, match="ancestor"):
        ProjectWorkspace(writable_ancestor / "configured-workspaces").create("my-novel")


def test_create_allows_service_owned_base_beneath_a_sticky_ancestor(tmp_path: Path) -> None:
    sticky_ancestor = tmp_path / "sticky-ancestor"
    sticky_ancestor.mkdir(mode=0o700)
    sticky_ancestor.chmod(0o1777)

    root = ProjectWorkspace(sticky_ancestor / "configured-workspaces").create("my-novel")

    assert root.is_dir()


def test_create_rejects_an_insecure_root_replaced_after_initial_creation(tmp_path: Path) -> None:
    workspace = ProjectWorkspace(tmp_path / "configured-workspaces")
    root = workspace.create("my-novel")
    root.rename(root.with_name("original-workspace"))
    root.mkdir(mode=0o700)
    root.chmod(0o777)

    with pytest.raises(UnsafeProjectWorkspaceError, match="service-private"):
        workspace.create("my-novel")


def test_create_closes_child_descriptor_when_closing_parent_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = ProjectWorkspace(tmp_path / "configured-workspaces")
    opened_descriptors: list[int] = []
    closed_descriptors: list[int] = []
    original_open = os.open
    original_close = os.close
    failed_parent_close = False

    def track_open(*args: object, **kwargs: object) -> int:
        descriptor = original_open(*args, **kwargs)  # type: ignore[arg-type]
        opened_descriptors.append(descriptor)
        return descriptor

    def fail_first_parent_close(descriptor: int) -> None:
        nonlocal failed_parent_close
        closed_descriptors.append(descriptor)
        if descriptor == opened_descriptors[0] and not failed_parent_close:
            failed_parent_close = True
            raise OSError("simulated parent close failure")
        original_close(descriptor)

    monkeypatch.setattr("app.workspace.project_workspace.os.open", track_open)
    monkeypatch.setattr("app.workspace.project_workspace.os.close", fail_first_parent_close)

    try:
        with pytest.raises(OSError, match="simulated parent close failure"):
            workspace.create("my-novel")

        assert len(opened_descriptors) >= 2
        assert opened_descriptors[1] in closed_descriptors
    finally:
        for descriptor in opened_descriptors:
            try:
                original_close(descriptor)
            except OSError:
                pass


def test_workspace_security_boundary_is_documented() -> None:
    readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert "pathname, not a durable file-descriptor capability" in readme
    assert "Same-EUID processes remain trusted deployment principals" in readme
    assert "Persistent FD capability/reconciler work is out of scope" in readme
