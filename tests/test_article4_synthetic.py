from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import st_article4_synthetic as synthetic  # noqa: E402


class Article4SyntheticCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.generation_log = synthetic.generate_records()
        cls.summary = synthetic.summarize_records(
            cls.records,
            cls.generation_log,
        )

    def test_full_corpus_public_aggregates(self):
        full = self.summary["full_corpus"]

        self.assertEqual(full["reference_records"], 282)
        self.assertEqual(full["equivalent_distance_zero"], 282)
        self.assertEqual(full["defect_variants"], 990)
        self.assertEqual(full["defects_with_nonzero_distance"], 990)
        self.assertEqual(full["template_count"], 16)
        self.assertEqual(full["unique_block_sequences"], 27)
        self.assertEqual(full["template_sequence_strata"], 28)
        self.assertEqual(full["category_counts"], {"A": 166, "C": 36, "D": 8, "E": 72})
        self.assertEqual(full["comparisons"], 1272)
        self.assertEqual(full["rendered_program_texts"], 1554)
        self.assertEqual(full["raw_symbols"], 0)
        self.assertEqual(
            full["references_by_defect_count"],
            {"1": 12, "2": 28, "3": 46, "4": 196},
        )
        self.assertEqual(self.summary["generation_log_entries"], 0)
        self.assertEqual(self.generation_log, [])

    def test_working_subset_public_aggregates(self):
        subset = self.summary["working_subset"]

        self.assertEqual(subset["reference_records"], 56)
        self.assertEqual(subset["defect_variants"], 188)
        self.assertEqual(subset["declared_edit_kind_matches"], 152)

    def test_corpus_and_subset_hashes_are_pinned(self):
        self.assertEqual(
            self.summary["full_corpus"]["jsonl_sha256"],
            "bf92e6c383969c08f2953fc19d1a4d7143bc7eed439182ecc35a043af4ae3a43",
        )
        self.assertEqual(
            self.summary["working_subset"]["jsonl_sha256"],
            "3ec06d350a724134f83870e685e37d5064f6ae1bda95d91c50a9c61f051db733",
        )

    def test_four_defect_cap_represents_seven_taxonomy_labels_but_not_fsm(self):
        taxonomy = self.summary["full_corpus"]["taxonomy"]

        self.assertEqual(len(taxonomy), 7)
        self.assertNotIn("ЗАМЕНА:ПЕРЕХОД-FSM", taxonomy)
        self.assertEqual(
            {
                label: values["defect_variants"]
                for label, values in taxonomy.items()
            },
            {
                "ВСТАВКА:ВЫХОД-АКТИВНЫЙ": 136,
                "ЗАМЕНА:ВЫХОД-БЕЗОПАСНЫЙ->ВЫХОД-АКТИВНЫЙ": 214,
                "ПЕРЕСТАНОВКА:ЗАЩИТА": 60,
                "УДАЛЕНИЕ:ГАРД/ОГРАНИЧЕНИЕ": 46,
                "УДАЛЕНИЕ:ЗАЩЁЛКА/СБРОС": 186,
                "УДАЛЕНИЕ:ЗАЩИТА/БЛОКИРОВКА": 202,
                "УДАЛЕНИЕ:КОНТРОЛЬ-ОС": 146,
            },
        )
        self.assertEqual(
            self.summary["protocol"],
            {
                "record_schema_version": "article4-synthetic/v1",
                "strict_canonicalization": True,
                "maximum_defects_per_reference": 4,
                "configured_mutation_types": 8,
                "represented_mutation_types": 7,
                "configured_but_not_represented": ["ЗАМЕНА:ПЕРЕХОД-FSM"],
            },
        )
        self.assertTrue(all(len(record["defects"]) <= 4 for record in self.records))

    def test_stable_flags_do_not_use_python_hash_and_repeat_exactly(self):
        params = {"fb_t": 3, "guard": False}
        reordered_params = {"guard": False, "fb_t": 3}

        with patch("builtins.hash", side_effect=AssertionError("hash() must not be used")):
            first = synthetic._stable_flags(
                "tpl_fan_contactor",
                params,
                style_seed=0,
            )
            second = synthetic._stable_flags(
                "tpl_fan_contactor",
                reordered_params,
                style_seed=0,
            )

        self.assertEqual(first, (False, False, False, True))
        self.assertEqual(first, second)

    def test_jsonl_has_one_record_per_line_and_a_final_newline(self):
        rendered = synthetic.records_jsonl(self.records)

        self.assertTrue(rendered.endswith("\n"))
        self.assertFalse(rendered.endswith("\n\n"))
        self.assertEqual(rendered.count("\n"), len(self.records))

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "article4-synthetic.jsonl"
            synthetic.write_jsonl(output, self.records)
            written = output.read_bytes()

        self.assertTrue(written.endswith(b"\n"))
        self.assertFalse(written.endswith(b"\r\n"))


if __name__ == "__main__":
    unittest.main()
