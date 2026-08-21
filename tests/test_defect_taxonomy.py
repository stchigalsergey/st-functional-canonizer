from pathlib import Path
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import st_canonizer_v2 as canonizer  # noqa: E402
import st_defect_taxonomy as taxonomy  # noqa: E402


def program(body: str) -> str:
    return f"""
PROGRAM P
VAR
    old_value : BOOL;
    set_signal : BOOL;
    reset_signal : BOOL;
    memory : BOOL;
END_VAR
{body}
END_PROGRAM
"""


class OutputContextTests(unittest.TestCase):
    def test_output_state_is_unspecified_without_explicit_context(self):
        for symbol in ("B_SET_OUTPUT_OFF", "B_SET_OUTPUT_ON"):
            with self.subTest(symbol=symbol):
                self.assertEqual(
                    taxonomy.categorize_symbol(symbol),
                    taxonomy.FunctionalCategory.OUTPUT_UNSPECIFIED.value,
                )

    def test_safe_and_working_positions_require_explicit_matching_states(self):
        safe_only = taxonomy.ProjectContext(
            signal_id="damper_command",
            safe_state=taxonomy.OutputState.OFF,
        )
        complete = taxonomy.ProjectContext(
            signal_id="damper_command",
            safe_state=taxonomy.OutputState.OFF,
            working_state=taxonomy.OutputState.ON,
        )

        self.assertEqual(
            taxonomy.categorize_symbol("B_SET_OUTPUT_OFF", safe_only),
            taxonomy.FunctionalCategory.OUTPUT_SAFE_POSITION.value,
        )
        self.assertEqual(
            taxonomy.categorize_symbol("B_SET_OUTPUT_ON", safe_only),
            taxonomy.FunctionalCategory.OUTPUT_UNSPECIFIED.value,
        )
        self.assertEqual(
            taxonomy.categorize_symbol("B_SET_OUTPUT_ON", complete),
            taxonomy.FunctionalCategory.OUTPUT_WORKING_POSITION.value,
        )


class DeterministicEditScriptTests(unittest.TestCase):
    def test_swap_differs_between_historical_and_move_aware_rules(self):
        historical = taxonomy.edit_script(
            ["A", "B"],
            ["B", "A"],
            tie_break=taxonomy.TieBreakRule.HISTORICAL,
        )
        move_aware = taxonomy.edit_script(
            ["A", "B"],
            ["B", "A"],
            tie_break=taxonomy.TieBreakRule.MOVE_AWARE,
        )

        self.assertEqual(historical.distance, 2)
        self.assertEqual(
            [operation.kind for operation in historical.operations],
            [taxonomy.EditKind.REPLACE, taxonomy.EditKind.REPLACE],
        )
        self.assertEqual(move_aware.distance, 2)
        self.assertEqual(
            [operation.kind for operation in move_aware.operations],
            [taxonomy.EditKind.DELETE, taxonomy.EditKind.INSERT],
        )
        self.assertEqual(len(taxonomy.infer_move_candidates(move_aware.operations)), 1)
        self.assertNotEqual(historical.tie_break_id, move_aware.tie_break_id)

    def test_repeated_symbol_trace_is_byte_deterministic(self):
        first = taxonomy.edit_script(
            ["A", "A"],
            ["A"],
            tie_break=taxonomy.TieBreakRule.MOVE_AWARE,
        )
        second = taxonomy.edit_script(
            ["A", "A"],
            ["A"],
            tie_break=taxonomy.TieBreakRule.MOVE_AWARE,
        )

        self.assertTrue(first.ambiguous)
        self.assertEqual(len(first.operations), 1)
        self.assertEqual(first.operations[0].kind, taxonomy.EditKind.DELETE)
        self.assertEqual(first.operations[0].reference_index, 1)
        rendered_first = json.dumps(
            first.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        rendered_second = json.dumps(
            second.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        self.assertEqual(rendered_first.encode("utf-8"), rendered_second.encode("utf-8"))

    def test_delete_and_insert_use_explicit_missing_side(self):
        deletion = taxonomy.edit_script(["B_FIRE_INTERLOCK"], [])
        insertion = taxonomy.edit_script([], ["B_FEEDBACK_MONITOR"])

        self.assertEqual(deletion.operations[0].test_category, taxonomy.MISSING_CATEGORY)
        self.assertIsNone(deletion.operations[0].test_symbol)
        self.assertIsNone(deletion.operations[0].test_index)
        self.assertEqual(insertion.operations[0].reference_category, taxonomy.MISSING_CATEGORY)
        self.assertIsNone(insertion.operations[0].reference_symbol)
        self.assertIsNone(insertion.operations[0].reference_index)

    def test_unknown_tie_break_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown tie-break rule"):
            taxonomy.edit_script(["A"], ["B"], tie_break="not-a-rule")


class CanonicalLatchTests(unittest.TestCase):
    def test_formula_and_set_then_reset_are_equivalent(self):
        formula = program(
            "memory := (memory OR set_signal) AND NOT reset_signal;"
        )
        two_if = program("""
IF set_signal THEN memory := TRUE; END_IF
IF reset_signal THEN memory := FALSE; END_IF
""")

        self.assertEqual(canonizer.canonize(formula), ["B_FAULT_LATCH"])
        self.assertEqual(canonizer.canonize(two_if), ["B_FAULT_LATCH"])
        comparison = taxonomy.analyze_st(formula, two_if)
        self.assertEqual(comparison.distance, 0)
        self.assertEqual(comparison.operations, ())

    def test_reset_then_set_is_not_the_same_latch(self):
        formula = program(
            "memory := (memory OR set_signal) AND NOT reset_signal;"
        )
        reverse_two_if = program("""
IF reset_signal THEN memory := FALSE; END_IF
IF set_signal THEN memory := TRUE; END_IF
""")

        self.assertNotIn("B_FAULT_LATCH", canonizer.canonize(reverse_two_if))
        self.assertGreater(taxonomy.analyze_st(formula, reverse_two_if).distance, 0)


class ErrorProfileTests(unittest.TestCase):
    def test_profile_keeps_operation_kind_and_functional_categories_separate(self):
        output_context = {
            0: taxonomy.ProjectContext(
                signal_id="damper_command",
                safe_state=taxonomy.OutputState.OFF,
                working_state=taxonomy.OutputState.ON,
            )
        }
        replacement = taxonomy.edit_script(
            ["B_SET_OUTPUT_OFF"],
            ["B_SET_OUTPUT_ON"],
            reference_context=output_context,
            test_context=output_context,
        )
        deletion = taxonomy.edit_script(
            ["B_SET_OUTPUT_OFF"],
            [],
            reference_context=output_context,
        )

        profile = taxonomy.build_error_profile([replacement, deletion])
        entries = {
            (
                entry["kind"],
                entry["reference_category"],
                entry["test_category"],
            ): entry["count"]
            for entry in profile["entries"]
        }
        self.assertEqual(profile["comparisons"], 2)
        self.assertEqual(profile["total_operations"], 2)
        self.assertEqual(
            entries,
            {
                (
                    taxonomy.EditKind.DELETE.value,
                    taxonomy.FunctionalCategory.OUTPUT_SAFE_POSITION.value,
                    taxonomy.MISSING_CATEGORY,
                ): 1,
                (
                    taxonomy.EditKind.REPLACE.value,
                    taxonomy.FunctionalCategory.OUTPUT_SAFE_POSITION.value,
                    taxonomy.FunctionalCategory.OUTPUT_WORKING_POSITION.value,
                ): 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
