from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "src"))

import st_canonizer as base  # noqa: E402
import st_signature_induction as induction  # noqa: E402


def program(
    body: str,
    *,
    input_name: str = "input_signal",
    output_name: str = "output_signal",
    local_name: str = "local_state",
) -> str:
    return f"""
PROGRAM SyntheticUnit
VAR_INPUT
    {input_name} : BOOL;
END_VAR
VAR_OUTPUT
    {output_name} : BOOL;
END_VAR
VAR
    {local_name} : BOOL;
    value : REAL;
END_VAR
{body}
END_PROGRAM
"""


class TypedSignatureTests(unittest.TestCase):
    def test_names_do_not_change_typed_signature(self):
        first = program(
            "output_signal := input_signal AND local_state;",
        )
        second = program(
            "command := sensor AND memory;",
            input_name="sensor",
            output_name="command",
            local_name="memory",
        )
        first_sequence = induction.signature_sequence(first, m=1)
        second_sequence = induction.signature_sequence(second, m=1)
        self.assertEqual(first_sequence, second_sequence)
        self.assertEqual(
            first_sequence,
            ("A O:BOOL:=(I:BOOLANDL:BOOL)",),
        )

    def test_statement_order_is_preserved(self):
        forward = induction.signature_sequence(program("""
output_signal := TRUE;
local_state := FALSE;
"""))
        reversed_order = induction.signature_sequence(program("""
local_state := FALSE;
output_signal := TRUE;
"""))
        self.assertEqual(forward, tuple(reversed(reversed_order)))
        self.assertNotEqual(forward, reversed_order)

    def test_m_controls_nested_expression_depth(self):
        source = program(
            "output_signal := input_signal AND NOT local_state;",
        )
        self.assertEqual(
            induction.signature_sequence(source, m=0),
            ("A O:BOOL:=~",),
        )
        self.assertEqual(
            induction.signature_sequence(source, m=1),
            ("A O:BOOL:=(I:BOOLAND~)",),
        )
        self.assertEqual(
            induction.signature_sequence(source, m=2),
            ("A O:BOOL:=(I:BOOLANDNOT(L:BOOL))",),
        )

    def test_article3_schema_sorts_call_arguments(self):
        first = program("value := LIMIT(0.0, value, 100.0);")
        second = program("value := LIMIT(100.0, value, 0.0);")
        self.assertEqual(
            induction.signature_sequence(first, m=2),
            induction.signature_sequence(second, m=2),
        )

    def test_strict_lexer_is_default(self):
        source = program("output_signal := input_signal^;")
        with self.assertRaises(base.UnsupportedCharacterError):
            induction.signature_sequence(source)

    def test_parser_recovery_is_rejected_by_default(self):
        source = program("GOTO missing_label;")
        with self.assertRaises(induction.UnsupportedStatementError):
            induction.signature_sequence(source)
        self.assertEqual(
            induction.signature_sequence(source, reject_raw=False),
            ("RAW",),
        )

    def test_template_identifier_is_content_addressed(self):
        signature = "A O:BOOL:=BF"
        symbol = induction.template_symbol(signature)
        self.assertTrue(symbol.startswith("SIG:"))
        self.assertEqual(symbol, induction.template_symbol(signature))
        self.assertNotEqual(
            symbol,
            induction.template_symbol("A O:BOOL:=BT"),
        )


class CorpusInductionTests(unittest.TestCase):
    def _make_corpus(self, root: Path) -> None:
        # Create in reverse lexical order to ensure discovery does not inherit
        # filesystem insertion order.
        (root / "z.ST").write_text(
            program("""
output_signal := FALSE;
IF input_signal THEN
    local_state := FALSE;
END_IF
"""),
            encoding="utf-8",
        )
        (root / "A.st").write_text(
            program("""
output_signal := FALSE;
local_state := TRUE;
"""),
            encoding="utf-8",
        )

    def test_k_threshold_and_generic_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_corpus(root)
            report = induction.induce_directory(root, k=2, m=1)

        self.assertEqual(report["corpus"]["files"], ["A.st", "z.ST"])
        self.assertEqual(report["summary"]["total_sequence_elements"], 5)
        self.assertEqual(report["summary"]["frequent_template_count"], 1)
        self.assertEqual(report["summary"]["frequent_template_occurrences"], 2)
        self.assertEqual(report["summary"]["frequent_template_coverage"], 0.4)
        self.assertEqual(report["summary"]["raw_signature_occurrences"], 0)
        self.assertEqual(
            report["summary"]["generic_tail_symbols"],
            ["GEN:A", "GEN:IF"],
        )
        self.assertEqual(report["summary"]["encoded_alphabet_size"], 3)

        units = {row["file"]: row for row in report["units"]}
        common_symbol = induction.template_symbol("A O:BOOL:=BF")
        self.assertEqual(
            units["A.st"]["encoded_sequence"],
            [common_symbol, "GEN:A"],
        )
        self.assertEqual(
            units["z.ST"]["encoded_sequence"],
            [common_symbol, "GEN:IF", "GEN:A"],
        )

    def test_reports_are_byte_deterministic_and_root_relative(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_corpus(root)
            first = induction.induce_directory(root, k=2, m=1)
            second = induction.induce_directory(root, k=2, m=1)
            rendered_first = induction.deterministic_json(first)
            rendered_second = induction.deterministic_json(second)

        self.assertEqual(rendered_first, rendered_second)
        self.assertNotIn(str(root), rendered_first)
        self.assertNotIn(root.as_posix(), rendered_first)
        self.assertTrue(rendered_first.endswith("\n"))

    def test_sweep_sorts_and_deduplicates_m_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._make_corpus(root)
            report = induction.sweep_directory(
                root,
                k=2,
                m_values=(2, 0, 1, 2),
            )

        self.assertEqual(report["configuration"]["m_values"], [0, 1, 2])
        self.assertEqual([row["m"] for row in report["rows"]], [0, 1, 2])
        self.assertTrue(all(row["k"] == 2 for row in report["rows"]))

    def test_recursive_discovery_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "nested"
            nested.mkdir()
            (nested / "unit.st").write_text(
                program("output_signal := FALSE;"),
                encoding="utf-8",
            )
            with self.assertRaises(induction.CorpusInputError):
                induction.induce_directory(root)
            report = induction.induce_directory(root, recursive=True)

        self.assertEqual(report["corpus"]["files"], ["nested/unit.st"])

    def test_parent_context_is_explicit_and_uses_sibling_declarations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Controller.st").write_text("""
FUNCTION_BLOCK Controller
VAR_INPUT input_signal : BOOL; END_VAR
VAR_OUTPUT output_signal : BOOL; END_VAR
END_FUNCTION_BLOCK
""", encoding="utf-8")
            (root / "Controller__Run.st").write_text("""
METHOD Run
output_signal := input_signal;
END_METHOD
""", encoding="utf-8")
            without_context = induction.induce_directory(root, k=1, m=1)
            with_context = induction.induce_directory(
                root,
                k=1,
                m=1,
                parent_context=True,
            )

        without_units = {
            row["file"]: row["full_sequence"]
            for row in without_context["units"]
        }
        with_units = {
            row["file"]: row["full_sequence"]
            for row in with_context["units"]
        }
        self.assertEqual(
            without_units["Controller__Run.st"],
            ["A ?:?:=?:?"],
        )
        self.assertEqual(
            with_units["Controller__Run.st"],
            ["A O:BOOL:=I:BOOL"],
        )
        self.assertEqual(
            with_context["corpus"]["parent_context_links"],
            [{"unit": "Controller__Run.st", "parent": "Controller.st"}],
        )

    def test_permissive_report_still_contains_strict_lexical_audit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "pointer.st").write_text(
                program("output_signal := input_signal^;"),
                encoding="utf-8",
            )
            with self.assertRaises(induction.CorpusInputError):
                induction.induce_directory(root)
            report = induction.induce_directory(root, strict=False)

        self.assertFalse(report["configuration"]["strict_lexer"])
        self.assertEqual(
            report["strict_lexical_audit"],
            {
                "files_with_unsupported_characters": 1,
                "unsupported_character_occurrences": 1,
                "characters": {"^": 1},
                "affected_files": ["pointer.st"],
            },
        )

    def test_invalid_parameters_and_empty_corpus_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(induction.CorpusInputError):
                induction.induce_directory(root)
            (root / "unit.st").write_text(
                program("output_signal := FALSE;"),
                encoding="utf-8",
            )
            with self.assertRaises(induction.SignatureInductionError):
                induction.induce_directory(root, k=0)
            with self.assertRaises(induction.SignatureInductionError):
                induction.induce_directory(root, m=-1)

    def test_cli_warns_for_each_explicit_safety_opt_out(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unit.st").write_text(
                program("GOTO missing_label;"),
                encoding="utf-8",
            )
            output = root / "report.json"
            stderr = StringIO()
            with redirect_stderr(stderr):
                return_code = induction.main([
                    "induce",
                    str(root),
                    "--permissive",
                    "--allow-raw",
                    "--omit-sequences",
                    "--output",
                    str(output),
                ])
            rendered = output.read_text(encoding="utf-8")

        self.assertEqual(return_code, 0)
        self.assertIn("WARNING: permissive lexer enabled", stderr.getvalue())
        self.assertIn("WARNING: RAW parser-recovery signatures", stderr.getvalue())
        self.assertIn('"strict_lexer": false', rendered)
        self.assertIn('"reject_raw": false', rendered)


if __name__ == "__main__":
    unittest.main()
