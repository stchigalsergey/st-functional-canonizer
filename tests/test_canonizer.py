from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import st_canonizer as base  # noqa: E402
import st_canonizer_v2 as v2  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import validate_corpus  # noqa: E402


def program(body: str) -> str:
    return f"""
PROGRAM P
VAR
    old_value : BOOL;
    set_signal : BOOL;
    reset_signal : BOOL;
    memory : BOOL;
    x : REAL;
END_VAR
{body}
END_PROGRAM
"""


class CanonizerTests(unittest.TestCase):
    def test_reset_priority_two_if_latch_is_recognized(self):
        src = program("""
IF set_signal THEN memory := TRUE; END_IF
IF reset_signal THEN memory := FALSE; END_IF
""")
        self.assertEqual(v2.canonize(src), ["B_FAULT_LATCH"])

    def test_reverse_two_if_is_not_the_same_latch(self):
        src = program("""
IF reset_signal THEN memory := FALSE; END_IF
IF set_signal THEN memory := TRUE; END_IF
""")
        self.assertNotIn("B_FAULT_LATCH", v2.canonize(src))

    def test_latch_truth_table_has_reset_priority(self):
        for old in (False, True):
            for set_signal in (False, True):
                for reset_signal in (False, True):
                    expression = (old or set_signal) and not reset_signal
                    sequential = old
                    if set_signal:
                        sequential = True
                    if reset_signal:
                        sequential = False
                    self.assertEqual(expression, sequential)

    def test_case_v2_parses_consecutive_labels(self):
        src = program("""
CASE old_value OF
    0: memory := FALSE;
    10: memory := TRUE;
END_CASE
""")
        seq = v2.canonize(src)
        self.assertFalse(any(x.startswith("B_RAW") for x in seq), seq)

    def test_strict_lexer_rejects_unsupported_character(self):
        with self.assertRaises(base.UnsupportedCharacterError):
            v2.canonize(program("memory := old_value^;"))

    def test_permissive_mode_reproduces_legacy_skip(self):
        seq = v2.canonize(program("memory := old_value^;"), strict=False)
        self.assertTrue(seq)

    def test_limit_symbol_does_not_encode_bounds(self):
        a = v2.canonize(program("x := LIMIT(0.0, x, 100.0);"))
        b = v2.canonize(program("x := LIMIT(20.0, x, 80.0);"))
        self.assertEqual(a, ["B_LIMIT"])
        self.assertEqual(a, b)

    def test_expression_serializer_preserves_positional_argument_order(self):
        forward = base.Call("limit", [
            base.Lit("NUM", "0.0"), base.Var("x"), base.Lit("NUM", "100.0")])
        reversed_bounds = base.Call("limit", [
            base.Lit("NUM", "100.0"), base.Var("x"), base.Lit("NUM", "0.0")])
        self.assertNotEqual(
            base.canon_expr(forward, {}),
            base.canon_expr(reversed_bounds, {}),
        )

    def test_declaration_only_program_has_empty_sequence(self):
        self.assertEqual(v2.canonize(program("")), [])
        _, body = base.split_unit(program(";"))
        self.assertFalse(validate_corpus.has_executable_tokens(base.st_lex(body)))

    def test_levenshtein(self):
        self.assertEqual(base.block_levenshtein(["A", "B"], ["A", "C"]), 1)


if __name__ == "__main__":
    unittest.main()
