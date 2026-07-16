"""POSIX project workspace lifecycle beneath a configured workspace base."""

import os
from pathlib import Path, PurePosixPath, PureWindowsPath


class UnsafeProjectWorkspaceError(ValueError):
    """Raised when a project workspace cannot be safely derived or created."""


class ProjectWorkspace:
    """Derive project workspace roots from safe slugs beneath one configured base."""

    _STANDARD_DIRECTORIES = ("outline", "lore", "characters", "chapters", ".versions")

    def __init__(self, workspace_base_dir: Path) -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise OSError("ProjectWorkspace requires POSIX dir_fd and O_NOFOLLOW support")
        self.workspace_base_dir = Path(os.path.abspath(workspace_base_dir))

    def root_for(self, slug: str) -> Path:
        self._validate_slug(slug)
        root = self.workspace_base_dir / slug
        if not root.is_relative_to(self.workspace_base_dir):
            raise UnsafeProjectWorkspaceError("project workspace root escapes its base")
        return root

    def create(self, slug: str) -> Path:
        """Create a project's standard workspace directories idempotently."""
        root = self.root_for(slug)
        base_descriptor = self._open_or_create_base_directory()
        try:
            root_descriptor = self._open_or_create_directory(base_descriptor, slug)
            try:
                for directory in self._STANDARD_DIRECTORIES:
                    descriptor = self._open_or_create_directory(root_descriptor, directory)
                    os.close(descriptor)
            finally:
                os.close(root_descriptor)
        finally:
            os.close(base_descriptor)
        return root

    def _open_or_create_base_directory(self) -> int:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.workspace_base_dir.parts[1:]:
                child_descriptor = self._open_or_create_directory(descriptor, part)
                os.close(descriptor)
                descriptor = child_descriptor
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _open_or_create_directory(parent_descriptor: int, name: str) -> int:
        try:
            os.mkdir(name, dir_fd=parent_descriptor)
        except FileExistsError:
            pass
        return os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )

    @staticmethod
    def _validate_slug(slug: str) -> None:
        windows_path = PureWindowsPath(slug)
        if (
            not slug
            or slug in {".", ".."}
            or "/" in slug
            or "\\" in slug
            or PurePosixPath(slug).is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
        ):
            raise UnsafeProjectWorkspaceError("project slug must be one safe path component")
