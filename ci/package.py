"""Builds a versioned release archive (dist/taskflow-api-<version>.zip) for the Jenkins pipeline."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402


def main() -> int:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"taskflow-api-{__version__}.zip"
    files = [p for p in (ROOT / "app").rglob("*.py")] + [ROOT / "requirements.txt"]
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            zf.write(path, path.relative_to(ROOT).as_posix())
    print(f"Created {archive.relative_to(ROOT)} ({archive.stat().st_size} bytes, {len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
