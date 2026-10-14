"""Verify the product version across manifests and API metadata."""
from pathlib import Path
import ast
import json
import re
import tomllib


def check_versions(root: Path) -> str:
    project = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))["project"]
    lock = tomllib.loads((root / "backend/uv.lock").read_text(encoding="utf-8"))
    packages = [item for item in lock["package"] if item["name"] == project["name"]]
    if len(packages) != 1:
        raise ValueError("Expected one backend distribution in uv.lock")
    config = ast.parse((root / "backend/app/core/config.py").read_text(encoding="utf-8"))
    api_versions = [ast.literal_eval(node.value) for node in ast.walk(config)
                    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                    and node.target.id == "app_version"]
    if len(api_versions) != 1:
        raise ValueError("Expected one API app_version")
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    npm_lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    versions = {"backend": project["version"], "uv.lock": packages[0]["version"], "API": api_versions[0],
                "frontend": frontend["version"], "npm lock": npm_lock["version"],
                "npm root": npm_lock["packages"][""]["version"]}
    if len(set(versions.values())) != 1:
        raise ValueError(f"Version mismatch: {versions}")
    version = versions["backend"]
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError(f"Invalid product version: {version}")
    return version


if __name__ == "__main__":
    print(f"Product version consistent: {check_versions(Path(__file__).resolve().parents[1])}")
