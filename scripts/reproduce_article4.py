#!/usr/bin/env python3
"""Regenerate and verify the public synthetic evidence for Article 4."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from st_article4_synthetic import (  # noqa: E402
    generate_records,
    summarize_records,
    write_jsonl,
)


DEFAULT_MANIFEST = ROOT / "reproducibility" / "article4_manifest.json"


def text_sha256_lf(path: Path) -> str:
    """Hash text with LF line endings so checkout policy cannot change identity."""

    content = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(content).hexdigest()


def differences(expected, actual, path="$"):
    """Return paths where an expected JSON subset differs from actual data."""

    if isinstance(expected, dict) and isinstance(actual, dict):
        output = []
        for key in sorted(expected):
            child = f"{path}.{key}"
            if key not in actual:
                output.append(f"{child}: missing")
            else:
                output.extend(differences(expected[key], actual[key], child))
        return output
    if isinstance(expected, list) and isinstance(actual, list):
        output = []
        if len(expected) != len(actual):
            output.append(f"{path}: length {len(actual)} != {len(expected)}")
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            output.extend(
                differences(expected_item, actual_item, f"{path}[{index}]")
            )
        return output
    if expected != actual:
        return [f"{path}: {actual!r} != {expected!r}"]
    return []


def verify(manifest_path: Path, output_path: Path | None = None) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records, log = generate_records()
    actual_summary = summarize_records(records, log)
    summary_differences = differences(
        manifest["expected_public_summary"],
        actual_summary,
    )

    file_checks = {}
    for relative_path, expected_hash in manifest["implementation_lf_sha256"].items():
        actual_hash = text_sha256_lf(ROOT / relative_path)
        file_checks[relative_path] = {
            "expected_sha256": expected_hash,
            "actual_sha256": actual_hash,
            "ok": actual_hash == expected_hash,
        }

    if output_path is not None:
        write_jsonl(output_path, records)

    ok = not summary_differences and all(
        check["ok"] for check in file_checks.values()
    )
    return {
        "schema_version": "article4-reproduction-check/v1",
        "ok": ok,
        "manifest": str(manifest_path.resolve()),
        "summary_matches": not summary_differences,
        "summary_differences": summary_differences,
        "implementation_checks": file_checks,
        "actual_summary": actual_summary,
    }


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--output",
        type=Path,
        help="optional path for regenerated synthetic JSONL",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="optional path for the verification report",
    )
    args = parser.parse_args(argv)

    report = verify(args.manifest, args.output)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
