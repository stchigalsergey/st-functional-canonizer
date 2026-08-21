"""Verify the exact STMutants v3 archive used by the Article 3 audit."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile


EXPECTED_SIZE = 213_554
EXPECTED_MD5 = "823f7fe9838a8fc382562d43bc07858a"
EXPECTED_SHA256 = (
    "c0355e78ff4e17b00cb63425b399fd441d0179e24628ad7678789934ad118da7"
)
EXPECTED_FILES = 110


def hash_file(path: Path) -> tuple[int, str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            md5.update(chunk)
            sha256.update(chunk)
    return size, md5.hexdigest(), sha256.hexdigest()


def inspect_archive(path: Path) -> dict:
    size, md5, sha256 = hash_file(path)
    with ZipFile(path) as archive:
        files = [entry for entry in archive.infolist() if not entry.is_dir()]
        txt_files = [entry for entry in files if entry.filename.lower().endswith(".txt")]
        corrupt_member = archive.testzip()

    checks = {
        "size_bytes": size == EXPECTED_SIZE,
        "publisher_md5": md5 == EXPECTED_MD5,
        "project_sha256": sha256 == EXPECTED_SHA256,
        "file_entries": len(files) == EXPECTED_FILES,
        "all_entries_are_txt": len(txt_files) == len(files),
        "zip_crc": corrupt_member is None,
    }
    return {
        "schema": "stmutants-archive-verification/1",
        "path": path.name,
        "observed": {
            "size_bytes": size,
            "md5": md5,
            "sha256": sha256,
            "file_entries": len(files),
            "txt_entries": len(txt_files),
            "first_corrupt_member": corrupt_member,
        },
        "checks": checks,
        "matches_article3_input": all(checks.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        report = inspect_archive(args.archive)
    except (OSError, BadZipFile) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["matches_article3_input"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
