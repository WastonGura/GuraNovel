"""UTF-8 Markdown document storage."""

import errno
import os
import uuid
from pathlib import Path

from app.workspace.paths import resolve_workspace_path


class MarkdownStore:
    """Read and atomically write Markdown documents beneath a workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def read(self, relative_path: str) -> str:
        return self._path(relative_path).read_text(encoding="utf-8")

    def write(self, relative_path: str, content: str) -> None:
        self._path(relative_path)
        path_parts = Path(relative_path).parts
        parent_parts, filename = path_parts[:-1], path_parts[-1]
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        parent_descriptor = self._open_workspace_directory(parent_parts)
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = self._create_temporary_file(parent_descriptor)
            try:
                temporary_file = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
            except BaseException:
                os.close(descriptor)
                raise
            with temporary_file:
                temporary_file.write(normalized_content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            self._fsync_directory(parent_descriptor)
        except BaseException:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_descriptor)
                except FileNotFoundError:
                    pass
            raise
        finally:
            os.close(parent_descriptor)

    def exists(self, relative_path: str) -> bool:
        return self._path(relative_path).is_file()

    def _path(self, relative_path: str) -> Path:
        return resolve_workspace_path(self.root, relative_path)

    def _open_workspace_directory(self, parent_parts: tuple[str, ...]) -> int:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(self.root, directory_flags)
        try:
            for part in parent_parts:
                try:
                    os.mkdir(part, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child_descriptor
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _create_temporary_file(parent_descriptor: int) -> tuple[int, str]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        while True:
            temporary_name = f".tmp-{uuid.uuid4().hex}"
            try:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            return descriptor, temporary_name

    @staticmethod
    def _fsync_directory(descriptor: int) -> None:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
