"""Compare a local Article 3 sweep with the published aggregate manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXPECTED = ROOT / "reproducibility" / "article3_manifest.json"

ROW_FIELDS = {
    "sequence_elements": "total_sequence_elements",
    "distinct_signatures": "distinct_signatures",
    "frequent_templates": "frequent_template_count",
    "covered_occurrences": "frequent_template_occurrences",
    "encoded_alphabet_size": "encoded_alphabet_size",
    "templates_for_50_percent": "templates_for_50_percent",
    "templates_for_80_percent": "templates_for_80_percent",
    "templates_for_90_percent": "templates_for_90_percent",
    "templates_for_95_percent": "templates_for_95_percent",
    "raw_occurrences": "raw_signature_occurrences",
}


def _expect(errors: list[str], label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        errors.append(f"{label}: expected {expected!r}, got {actual!r}")


def compare_report(report: dict, manifest: dict) -> list[str]:
    """Return aggregate-only mismatch descriptions."""

    errors: list[str] = []
    configuration = report.get("configuration", {})
    _expect(errors, "configuration.k", configuration.get("k"), 5)
    _expect(errors, "configuration.m_values", configuration.get("m_values"), [0, 1, 2, 3])
    _expect(errors, "configuration.strict_lexer", configuration.get("strict_lexer"), False)
    _expect(errors, "configuration.reject_raw", configuration.get("reject_raw"), True)
    _expect(errors, "configuration.parent_context", configuration.get("parent_context"), True)

    expected_input = manifest["inputs"]["industrial_corpus"]
    corpus = report.get("corpus", {})
    _expect(errors, "corpus.file_count", corpus.get("file_count"), expected_input["top_level_st_files"])
    _expect(errors, "corpus.manifest_sha256", corpus.get("manifest_sha256"), expected_input["sha256"])

    expected_audit = manifest["expected_results"]["strict_lexical_audit"]
    audit = report.get("strict_lexical_audit", {})
    _expect(
        errors,
        "strict_lexical_audit.files_with_unsupported_characters",
        audit.get("files_with_unsupported_characters"),
        expected_audit["affected_units"],
    )
    _expect(
        errors,
        "strict_lexical_audit.unsupported_character_occurrences",
        audit.get("unsupported_character_occurrences"),
        expected_audit["unsupported_occurrences"],
    )
    _expect(
        errors,
        "strict_lexical_audit.character_count",
        audit.get("characters", {}).get(expected_audit["character"]),
        expected_audit["unsupported_occurrences"],
    )

    expected_rows = {
        row["m"]: row
        for row in manifest["expected_results"]["parser_v2_permissive_sweep_k5"]
    }
    report_rows = report.get("rows", [])
    if not isinstance(report_rows, list):
        errors.append("sweep.rows: expected a list")
        report_rows = []
    actual_rows = {
        row.get("m"): row
        for row in report_rows
        if isinstance(row, dict) and isinstance(row.get("m"), int)
    }
    _expect(errors, "sweep.row_count", len(report_rows), len(expected_rows))
    _expect(errors, "sweep.m_values", sorted(actual_rows), sorted(expected_rows))
    for m, expected in sorted(expected_rows.items()):
        actual = actual_rows.get(m)
        if actual is None:
            continue
        for expected_name, actual_name in ROW_FIELDS.items():
            _expect(
                errors,
                f"m={m}.{actual_name}",
                actual.get(actual_name),
                expected[expected_name],
            )
        coverage = actual.get("frequent_template_coverage")
        coverage_percent = round(100.0 * coverage, 3) if isinstance(coverage, (int, float)) else None
        _expect(errors, f"m={m}.coverage_percent", coverage_percent, expected["coverage_percent"])
    return errors


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    args = parser.parse_args()
    try:
        errors = compare_report(_load_json(args.report), _load_json(args.expected))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    if errors:
        print("Article 3 sweep does not match the expected aggregate manifest:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Article 3 sweep matches the expected aggregate manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
