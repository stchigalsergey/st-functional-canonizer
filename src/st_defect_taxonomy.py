"""Interpretable edit scenarios for canonical Structured Text sequences.

The module deliberately separates three questions:

* whether two canonical sequences differ;
* which elementary edit kinds occur in one deterministic optimal script;
* how the affected symbols are interpreted in an explicitly supplied project
  context.

The default category resolver never infers that ``FALSE``, zero, or an
``*_OFF`` symbol is a safe process state.  Output semantics require an
explicit :class:`SymbolContext` supplied by the caller.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable, Mapping, Sequence


MISSING_CATEGORY = "∅"


class EditKind(str, Enum):
    """Elementary operations of Levenshtein distance."""

    REPLACE = "replace"
    DELETE = "delete"
    INSERT = "insert"


class TieBreakRule(str, Enum):
    """Versioned rules for selecting one of several optimal edit scripts."""

    HISTORICAL = "levenshtein-unit/backtrace-diagonal-delete-insert/v1"
    MOVE_AWARE = "levenshtein-unit/forward-keep-delete-insert-replace/v1"


class FunctionalCategory(str, Enum):
    """Preliminary functional categories used by the Article 4 artifact."""

    OUTPUT_SAFE_POSITION = "OUTPUT_SAFE_POSITION"
    OUTPUT_WORKING_POSITION = "OUTPUT_WORKING_POSITION"
    OUTPUT_UNSPECIFIED = "OUTPUT_UNSPECIFIED"
    PROTECTION_INTERLOCK = "PROTECTION_INTERLOCK"
    FEEDBACK_MONITOR = "FEEDBACK_MONITOR"
    FAULT_LATCH_RESET = "FAULT_LATCH_RESET"
    TIMER_EDGE = "TIMER_EDGE"
    CONDITION_RANGE_CHECK = "CONDITION_RANGE_CHECK"
    INITIALIZATION_RESET = "INITIALIZATION_RESET"
    DATA_IO = "DATA_IO"
    TYPE_CONVERSION = "TYPE_CONVERSION"
    OTHER = "OTHER"


class OutputState(str, Enum):
    """Commanded state recorded in project metadata."""

    OFF = "off"
    ON = "on"
    OTHER = "other"


@dataclass(frozen=True)
class ProjectContext:
    """Metadata for one symbol occurrence.

    ``category_override`` can assign a project-specific or multi-domain
    category directly.  Output safety is inferred only when the commanded
    state encoded by the canonical symbol matches ``safe_state`` explicitly.
    A state opposite to ``safe_state`` is not automatically considered a
    working state; ``working_state`` must also be supplied explicitly.
    """

    signal_id: str | None = None
    safe_state: OutputState | str | None = None
    working_state: OutputState | str | None = None
    category_override: FunctionalCategory | str | None = None
    tags: tuple[str, ...] = ()


# A concise alias for callers that attach metadata to sequence positions.
SymbolContext = ProjectContext


@dataclass(frozen=True)
class EditOperation:
    """One non-matching operation in a deterministic optimal edit script."""

    kind: EditKind
    reference_index: int | None
    test_index: int | None
    reference_symbol: str | None
    test_symbol: str | None
    reference_category: str
    test_category: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["kind"] = self.kind.value
        return result


@dataclass(frozen=True)
class MoveCandidate:
    """A delete/insert pair that may represent a moved symbol."""

    delete_operation_index: int
    insert_operation_index: int
    symbol: str
    reference_index: int
    test_index: int


@dataclass(frozen=True)
class EditResult:
    """Distance, chosen script, and an ambiguity indicator.

    ``optimal_script_count_capped`` is 1 when the optimum is unique and 2 when
    at least two optimal scripts exist.  Counts are intentionally capped at 2
    because the artifact only needs to expose ambiguity, not enumerate an
    exponentially large path set.
    """

    distance: int
    operations: tuple[EditOperation, ...]
    optimal_script_count_capped: int
    tie_break_id: str

    @property
    def ambiguous(self) -> bool:
        return self.optimal_script_count_capped > 1

    def to_dict(self) -> dict[str, object]:
        return {
            "distance": self.distance,
            "ambiguous": self.ambiguous,
            "optimal_script_count_capped": self.optimal_script_count_capped,
            "tie_break_id": self.tie_break_id,
            "operations": [operation.to_dict() for operation in self.operations],
        }


_STRUCTURAL_CATEGORIES: dict[str, FunctionalCategory] = {
    "B_FIRE_INTERLOCK": FunctionalCategory.PROTECTION_INTERLOCK,
    "B_FROST_PROTECTION": FunctionalCategory.PROTECTION_INTERLOCK,
    "B_FEEDBACK_MONITOR": FunctionalCategory.FEEDBACK_MONITOR,
    "B_FAULT_LATCH": FunctionalCategory.FAULT_LATCH_RESET,
    "B_TIMER": FunctionalCategory.TIMER_EDGE,
    "B_EDGE": FunctionalCategory.TIMER_EDGE,
    "B_LIMIT": FunctionalCategory.CONDITION_RANGE_CHECK,
    "B_IF": FunctionalCategory.CONDITION_RANGE_CHECK,
    "readreal": FunctionalCategory.DATA_IO,
    "read16": FunctionalCategory.DATA_IO,
    "word_to_byte": FunctionalCategory.TYPE_CONVERSION,
}

_OUTPUT_SYMBOLS = {
    "B_SET_OUTPUT_OFF",
    "B_SET_OUTPUT_ON",
    "B_SET_OUTPUT",
}


def _category_value(value: FunctionalCategory | str) -> str:
    return value.value if isinstance(value, FunctionalCategory) else str(value)


def _output_state(prefix: str) -> OutputState:
    if prefix == "B_SET_OUTPUT_OFF":
        return OutputState.OFF
    if prefix == "B_SET_OUTPUT_ON":
        return OutputState.ON
    return OutputState.OTHER


def _states_equal(actual: OutputState, expected: OutputState | str | None) -> bool:
    if expected is None:
        return False
    return actual.value == (expected.value if isinstance(expected, OutputState) else str(expected))


def categorize_symbol(symbol: str | None, context: ProjectContext | None = None) -> str:
    """Return a category without guessing project-specific output safety."""

    if symbol is None:
        return MISSING_CATEGORY
    if context is not None and context.category_override is not None:
        return _category_value(context.category_override)

    prefix = symbol.split(":", 1)[0]
    if prefix in _OUTPUT_SYMBOLS:
        state = _output_state(prefix)
        if context is not None and _states_equal(state, context.safe_state):
            return FunctionalCategory.OUTPUT_SAFE_POSITION.value
        if context is not None and _states_equal(state, context.working_state):
            return FunctionalCategory.OUTPUT_WORKING_POSITION.value
        return FunctionalCategory.OUTPUT_UNSPECIFIED.value
    return _STRUCTURAL_CATEGORIES.get(prefix, FunctionalCategory.OTHER).value


def _context_at(
    contexts: Mapping[int, ProjectContext] | None,
    index: int | None,
) -> ProjectContext | None:
    if contexts is None or index is None:
        return None
    return contexts.get(index)


def _validate_contexts(
    contexts: Mapping[int, ProjectContext] | None,
    sequence_length: int,
    label: str,
) -> None:
    if contexts is None:
        return
    for index, context in contexts.items():
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError(f"{label} context index must be an integer: {index!r}")
        if not 0 <= index < sequence_length:
            raise IndexError(
                f"{label} context index {index} is outside sequence length {sequence_length}"
            )
        if not isinstance(context, ProjectContext):
            raise TypeError(
                f"{label} context at index {index} must be ProjectContext"
            )


def _count_optimal_scripts(
    distances: list[list[int]],
    reference: Sequence[str],
    test: Sequence[str],
) -> list[list[int]]:
    counts = [[0] * (len(test) + 1) for _ in range(len(reference) + 1)]
    counts[0][0] = 1
    for i in range(len(reference) + 1):
        for j in range(len(test) + 1):
            if i == 0 and j == 0:
                continue
            total = 0
            target = distances[i][j]
            if i > 0 and distances[i - 1][j] + 1 == target:
                total += counts[i - 1][j]
            if j > 0 and distances[i][j - 1] + 1 == target:
                total += counts[i][j - 1]
            if i > 0 and j > 0:
                cost = 0 if reference[i - 1] == test[j - 1] else 1
                if distances[i - 1][j - 1] + cost == target:
                    total += counts[i - 1][j - 1]
            counts[i][j] = min(2, total)
    return counts


def edit_script(
    reference: Sequence[str],
    test: Sequence[str],
    *,
    reference_context: Mapping[int, ProjectContext] | None = None,
    test_context: Mapping[int, ProjectContext] | None = None,
    tie_break: TieBreakRule | str = TieBreakRule.HISTORICAL,
) -> EditResult:
    """Compute one deterministic optimal Levenshtein edit script.

    The historical rule reproduces the Article 4 aggregate by tracing from the
    end with diagonal, deletion, insertion priority.  The optional move-aware
    rule traces from the start with keep, deletion, insertion, replacement
    priority and represents a simple swap as a delete/insert pair.  The
    selected path is reproducible, while ``ambiguous`` records whether another
    optimum exists.
    """

    try:
        rule = TieBreakRule(tie_break)
    except ValueError as error:
        raise ValueError(f"unknown tie-break rule: {tie_break!r}") from error

    reference = tuple(reference)
    test = tuple(test)
    rows, columns = len(reference), len(test)
    _validate_contexts(reference_context, rows, "reference")
    _validate_contexts(test_context, columns, "test")
    distances = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(rows + 1):
        distances[i][0] = i
    for j in range(columns + 1):
        distances[0][j] = j
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            substitution = 0 if reference[i - 1] == test[j - 1] else 1
            distances[i][j] = min(
                distances[i - 1][j] + 1,
                distances[i][j - 1] + 1,
                distances[i - 1][j - 1] + substitution,
            )

    counts = _count_optimal_scripts(distances, reference, test)

    def replacement(reference_index: int, test_index: int) -> EditOperation:
        return EditOperation(
            kind=EditKind.REPLACE,
            reference_index=reference_index,
            test_index=test_index,
            reference_symbol=reference[reference_index],
            test_symbol=test[test_index],
            reference_category=categorize_symbol(
                reference[reference_index], _context_at(reference_context, reference_index)
            ),
            test_category=categorize_symbol(
                test[test_index], _context_at(test_context, test_index)
            ),
        )

    def deletion(reference_index: int) -> EditOperation:
        return EditOperation(
            kind=EditKind.DELETE,
            reference_index=reference_index,
            test_index=None,
            reference_symbol=reference[reference_index],
            test_symbol=None,
            reference_category=categorize_symbol(
                reference[reference_index],
                _context_at(reference_context, reference_index),
            ),
            test_category=MISSING_CATEGORY,
        )

    def insertion(test_index: int) -> EditOperation:
        return EditOperation(
            kind=EditKind.INSERT,
            reference_index=None,
            test_index=test_index,
            reference_symbol=None,
            test_symbol=test[test_index],
            reference_category=MISSING_CATEGORY,
            test_category=categorize_symbol(
                test[test_index], _context_at(test_context, test_index)
            ),
        )

    operations: list[EditOperation] = []
    if rule is TieBreakRule.HISTORICAL:
        i, j = rows, columns
        while i > 0 or j > 0:
            if i > 0 and j > 0:
                substitution = 0 if reference[i - 1] == test[j - 1] else 1
                if distances[i][j] == distances[i - 1][j - 1] + substitution:
                    if substitution:
                        operations.append(replacement(i - 1, j - 1))
                    i -= 1
                    j -= 1
                    continue
            if i > 0 and distances[i][j] == distances[i - 1][j] + 1:
                operations.append(deletion(i - 1))
                i -= 1
                continue
            if j == 0 or distances[i][j] != distances[i][j - 1] + 1:
                raise AssertionError("invalid Levenshtein backtrace")
            operations.append(insertion(j - 1))
            j -= 1
        operations.reverse()
    else:
        remaining = [[0] * (columns + 1) for _ in range(rows + 1)]
        for i in range(rows, -1, -1):
            remaining[i][columns] = rows - i
        for j in range(columns, -1, -1):
            remaining[rows][j] = columns - j
        for i in range(rows - 1, -1, -1):
            for j in range(columns - 1, -1, -1):
                substitution = 0 if reference[i] == test[j] else 1
                remaining[i][j] = min(
                    remaining[i + 1][j] + 1,
                    remaining[i][j + 1] + 1,
                    remaining[i + 1][j + 1] + substitution,
                )
        i = j = 0
        while i < rows or j < columns:
            if (i < rows and j < columns and reference[i] == test[j]
                    and remaining[i][j] == remaining[i + 1][j + 1]):
                i += 1
                j += 1
                continue
            if i < rows and remaining[i][j] == remaining[i + 1][j] + 1:
                operations.append(deletion(i))
                i += 1
                continue
            if j < columns and remaining[i][j] == remaining[i][j + 1] + 1:
                operations.append(insertion(j))
                j += 1
                continue
            if (i < rows and j < columns
                    and remaining[i][j] == remaining[i + 1][j + 1] + 1):
                operations.append(replacement(i, j))
                i += 1
                j += 1
                continue
            raise AssertionError("invalid forward Levenshtein trace")

    return EditResult(
        distance=distances[rows][columns],
        operations=tuple(operations),
        optimal_script_count_capped=counts[rows][columns],
        tie_break_id=rule.value,
    )


def analyze_st(
    reference_source: str,
    test_source: str,
    *,
    strict: bool = True,
    reference_context: Mapping[int, ProjectContext] | None = None,
    test_context: Mapping[int, ProjectContext] | None = None,
    tie_break: TieBreakRule | str = TieBreakRule.HISTORICAL,
) -> EditResult:
    """Canonize two ST units and return their interpretable edit result."""

    from st_canonizer_v2 import canonize

    return edit_script(
        canonize(reference_source, strict=strict),
        canonize(test_source, strict=strict),
        reference_context=reference_context,
        test_context=test_context,
        tie_break=tie_break,
    )


def infer_move_candidates(operations: Sequence[EditOperation]) -> tuple[MoveCandidate, ...]:
    """Greedily pair equal deleted and inserted symbols as possible moves.

    This post-processing does not change Levenshtein distance and must not be
    confused with a native transposition operation.
    """

    unused_insertions = {
        index for index, operation in enumerate(operations)
        if operation.kind is EditKind.INSERT
    }
    moves: list[MoveCandidate] = []
    for delete_index, deletion in enumerate(operations):
        if deletion.kind is not EditKind.DELETE or deletion.reference_symbol is None:
            continue
        insertion_index = next((
            index for index in sorted(unused_insertions)
            if operations[index].test_symbol == deletion.reference_symbol
        ), None)
        if insertion_index is None:
            continue
        insertion = operations[insertion_index]
        unused_insertions.remove(insertion_index)
        moves.append(MoveCandidate(
            delete_operation_index=delete_index,
            insert_operation_index=insertion_index,
            symbol=deletion.reference_symbol,
            reference_index=deletion.reference_index,
            test_index=insertion.test_index,
        ))
    return tuple(moves)


_EXPECTED_KIND_PREFIXES = {
    "REPLACE": EditKind.REPLACE,
    "DELETE": EditKind.DELETE,
    "INSERT": EditKind.INSERT,
    "ЗАМЕНА": EditKind.REPLACE,
    "УДАЛЕНИЕ": EditKind.DELETE,
    "ВСТАВКА": EditKind.INSERT,
}


def matches_expected_edit_kind(
    result: EditResult,
    taxonomy_label: str,
    *,
    recognize_moves: bool = False,
) -> bool:
    """Compare only the top-level edit kind with an external label.

    This is the limited criterion behind the Article 4 ``156/194`` result; it
    is not category-classification accuracy.  A permutation has no elementary
    Levenshtein kind and therefore returns ``False`` unless ``recognize_moves``
    is explicitly enabled.
    """

    prefix = taxonomy_label.split(":", 1)[0].upper()
    if prefix in {"MOVE", "PERMUTATION", "ПЕРЕСТАНОВКА"}:
        return recognize_moves and bool(infer_move_candidates(result.operations))
    expected = _EXPECTED_KIND_PREFIXES.get(prefix)
    return expected is not None and any(
        operation.kind is expected for operation in result.operations
    )


def build_error_profile(results: Iterable[EditResult]) -> dict[str, object]:
    """Aggregate edit operations into a deterministic descriptive profile.

    The result describes observed edits.  It is not normalized by category
    opportunities and must not be reported as model accuracy or defect risk.
    """

    counts: dict[tuple[str, str, str], int] = {}
    comparisons = 0
    ambiguous = 0
    tie_break_ids: set[str] = set()
    for result in results:
        comparisons += 1
        ambiguous += int(result.ambiguous)
        tie_break_ids.add(result.tie_break_id)
        for operation in result.operations:
            key = (
                operation.kind.value,
                operation.reference_category,
                operation.test_category,
            )
            counts[key] = counts.get(key, 0) + 1
    total = sum(counts.values())
    entries = []
    for (kind, reference_category, test_category), count in sorted(counts.items()):
        entries.append({
            "kind": kind,
            "reference_category": reference_category,
            "test_category": test_category,
            "count": count,
            "share": count / total if total else 0.0,
        })
    return {
        "schema_version": "article4-error-profile/v1",
        "comparisons": comparisons,
        "ambiguous_comparisons": ambiguous,
        "total_operations": total,
        "tie_break_ids": sorted(tie_break_ids),
        "entries": entries,
    }
