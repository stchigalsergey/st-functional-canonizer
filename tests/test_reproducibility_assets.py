import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_article3_sweep as checker  # noqa: E402
import verify_stmutants_archive as stmutants  # noqa: E402


class ReproducibilityAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest_path = ROOT / "reproducibility" / "article3_manifest.json"
        cls.manifest = json.loads(cls.manifest_path.read_text(encoding="utf-8"))

    def _matching_report(self):
        expected_input = self.manifest["inputs"]["industrial_corpus"]
        expected_audit = self.manifest["expected_results"]["strict_lexical_audit"]
        rows = []
        for expected in self.manifest["expected_results"]["parser_v2_permissive_sweep_k5"]:
            row = {"m": expected["m"]}
            for expected_name, actual_name in checker.ROW_FIELDS.items():
                row[actual_name] = expected[expected_name]
            row["frequent_template_coverage"] = expected["coverage_percent"] / 100.0
            rows.append(row)
        return {
            "configuration": {
                "k": 5,
                "m_values": [0, 1, 2, 3],
                "strict_lexer": False,
                "reject_raw": True,
                "parent_context": True,
            },
            "corpus": {
                "file_count": expected_input["top_level_st_files"],
                "manifest_sha256": expected_input["sha256"],
            },
            "strict_lexical_audit": {
                "files_with_unsupported_characters": expected_audit["affected_units"],
                "unsupported_character_occurrences": expected_audit["unsupported_occurrences"],
                "characters": {
                    expected_audit["character"]: expected_audit["unsupported_occurrences"]
                },
            },
            "rows": rows,
        }

    def test_expected_manifest_is_anonymized_and_well_formed(self):
        rendered = self.manifest_path.read_text(encoding="utf-8")
        self.assertEqual(self.manifest["artifact_release"], "0.2.0")
        self.assertEqual(
            self.manifest["expected_results"]["parser_v2_permissive_sweep_k5"][1]["frequent_templates"],
            238,
        )
        self.assertNotIn(":\\", rendered)
        self.assertNotIn("FB_AirFilter", rendered)

    def test_checker_accepts_matching_aggregates_and_rejects_drift(self):
        report = self._matching_report()
        self.assertEqual(checker.compare_report(report, self.manifest), [])
        report["rows"][1]["frequent_template_count"] = 237
        errors = checker.compare_report(report, self.manifest)
        self.assertTrue(any("frequent_template_count" in error for error in errors))

    def test_stmutants_verifier_rejects_a_different_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "other.zip"
            with ZipFile(archive, "w") as target:
                target.writestr("Mutations/Synthetic/1.txt", "PROGRAM P END_PROGRAM")
            report = stmutants.inspect_archive(archive)
        self.assertFalse(report["matches_article3_input"])
        self.assertEqual(report["path"], "other.zip")


if __name__ == "__main__":
    unittest.main()
