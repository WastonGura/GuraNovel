from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_versions import check_versions


class VersionsTest(unittest.TestCase):
    def test_consistent_manifests(self):
        version = check_versions(Path(__file__).resolve().parents[2])
        self.assertRegex(version, r"^\d+\.\d+\.\d+")

    def test_each_manifest_mismatch_is_rejected(self):
        source = Path(__file__).resolve().parents[2]
        paths = ["backend/pyproject.toml", "backend/uv.lock", "backend/app/core/config.py",
                 "frontend/package.json", "frontend/package-lock.json"]
        for changed in paths:
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                for relative in paths:
                    target = root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    text = (source / relative).read_text(encoding="utf-8")
                    if relative == changed:
                        current = check_versions(source)
                        text = text.replace(current, '0.99.0')
                    target.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Version mismatch"):
                    check_versions(root)


if __name__ == "__main__":
    unittest.main()
