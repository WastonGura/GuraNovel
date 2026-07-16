"""Safe path handling for workspace documents and version snapshots."""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafeWorkspacePathError(ValueError):
    """Raised when a user-provided path is not contained by its workspace."""


_DOCUMENT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_VERSION_SUFFIX_PATTERN = re.compile(r"v[0-9]{4,}")


def workspace_path_parts(relative_path: str) -> tuple[str, ...]:
    """Validate a workspace path and return its POSIX path components.

    Workspace APIs accept ``/`` as their sole path separator. Backslashes are
    rejected so paths have the same meaning on Linux and WSL, while Windows
    absolute and traversal forms are rejected explicitly as well.
    """
    windows_path = PureWindowsPath(relative_path)
    if PurePosixPath(relative_path).is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise UnsafeWorkspacePathError("workspace paths must be relative")
    if "\\" in relative_path:
        raise UnsafeWorkspacePathError("workspace paths must use POSIX '/' separators")
    if not relative_path or relative_path.endswith("/"):
        raise UnsafeWorkspacePathError("workspace paths must name a file")
    if "." in relative_path.split("/"):
        raise UnsafeWorkspacePathError("workspace paths must not contain dot components")

    parts = PurePosixPath(relative_path).parts
    if not parts or all(part == "." for part in parts):
        raise UnsafeWorkspacePathError("workspace paths must name a file")
    if ".." in parts or ".." in windows_path.parts:
        raise UnsafeWorkspacePathError("workspace paths must not contain traversal")

    return tuple(part for part in parts if part != ".")


def resolve_workspace_path(workspace_root: Path, relative_path: str) -> Path:
    """Resolve a validated user path only when it remains below ``workspace_root``."""
    parts = workspace_path_parts(relative_path)

    root = workspace_root.resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root):
        raise UnsafeWorkspacePathError("workspace path escapes its root")
    return candidate


def snapshot_path(document_id: str, suffix: str) -> Path:
    """Return ``.versions/<document_id>/<suffix>.md`` for safe identifiers."""
    if not _DOCUMENT_ID_PATTERN.fullmatch(document_id):
        raise UnsafeWorkspacePathError("document ID must be a safe path component")
    if not _VERSION_SUFFIX_PATTERN.fullmatch(suffix):
        raise UnsafeWorkspacePathError("snapshot suffix must be a version such as v0001")
    return Path(".versions") / document_id / f"{suffix}.md"


def version_snapshot_path(document_id: str, version: int) -> Path:
    """Return the standard snapshot path for a positive integer version."""
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("version must be a positive integer")
    return snapshot_path(document_id, f"v{version:04d}")
