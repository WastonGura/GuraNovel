"""UTF-8 Markdown document storage."""

import os
import tempfile
from pathlib import Path

from app.workspace.paths import resolve_workspace_path


class MarkdownStore:
    """Read and atomically write Markdown documents beneath a workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def read(self, relative_path: str) -> str:
        return self._path(relative_path).read_text(encoding="utf-8")

    def write(self, relative_path: str, content: str) -> None:
        destination = self._path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        descriptor, temporary_name = tempfile.mkstemp(dir=destination.parent, prefix=".tmp-")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary_file:
                temporary_file.write(normalized_content)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_name, destination)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise

    def exists(self, relative_path: str) -> bool:
        return self._path(relative_path).is_file()

    def _path(self, relative_path: str) -> Path:
        return resolve_workspace_path(self.root, relative_path)
