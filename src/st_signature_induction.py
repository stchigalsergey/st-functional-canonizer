# -*- coding: utf-8 -*-
"""Deterministic induction of typed ST statement signatures.

This module implements the corpus-dependent representation described in
Article 3.  It accepts caller-provided Structured Text sources, preserves the
pre-order of statements, and builds a finite codebook from signatures whose
corpus frequency is at least ``k``.  Rare signatures are encoded with an
explicit generic-tail symbol such as ``GEN:A`` or ``GEN:IF``.

The implementation is strict by default in two independent ways:

* unsupported lexical characters are rejected by :func:`st_lex`;
* parser recovery nodes (``Raw``) are rejected before a codebook is induced.

Permissive operation is available only through explicit API arguments or CLI
flags.  This artifact is a static representation tool, not an IEC 61131-3
compiler and not a proof of program equivalence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence

from st_canonizer import (
    EDGE_TYPES,
    TIMER_TYPES,
    _COMMUTATIVE,
    _MASTER,
    Assign,
    Bin,
    Call,
    CallStmt,
    Case,
    If,
    Lit,
    Loop,
    NamedArg,
    Raw,
    Simple,
    Unary,
    Var,
    split_unit,
    st_lex,
)
from st_canonizer_v2 import ParserV2


SCHEMA_VERSION = "st-signature-induction/1"
SIGNATURE_SCHEMA = "article3-typed-signature-v1"
IMPLEMENTATION_VERSION = "0.2.0"
DEFAULT_K = 5
DEFAULT_M = 1
DEFAULT_SWEEP_M = (0, 1, 2, 3)
DEFAULT_PARENT_SEPARATOR = "__"

_PARENT_VAR_SECTION = re.compile(
    r"\b(VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|VAR_GLOBAL|VAR)\b"
    r".*?\bEND_VAR\b",
    re.IGNORECASE | re.DOTALL,
)

ROLE_ABBR = {
    "IN": "I",
    "OUT": "O",
    "INOUT": "IO",
    "LOCAL": "L",
    "GLOBAL": "G",
    None: "?",
}


class SignatureInductionError(ValueError):
    """Base error for invalid input to the signature-induction pipeline."""


class UnsupportedStatementError(SignatureInductionError):
    """Raised when the parser had to recover an unsupported statement."""


class CorpusInputError(SignatureInductionError):
    """Raised when a corpus directory is absent, empty, or ambiguous."""


@dataclass(frozen=True)
class CorpusSource:
    """One decoded corpus unit with a root-relative, POSIX-style name."""

    relative_path: str
    text: str
    raw_bytes: bytes
    context_from: str | None = None


def _validate_k_m(k: int, m: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise SignatureInductionError("k must be an integer >= 1")
    if isinstance(m, bool) or not isinstance(m, int) or m < 0:
        raise SignatureInductionError("m must be an integer >= 0")


def _typed_path(path: str, variables: Mapping[str, tuple[str, str]]) -> str:
    base = path.split(".", 1)[0]
    tail = path[len(base):]
    role, variable_type = variables.get(base.lower(), (None, None))
    return "%s:%s%s" % (
        ROLE_ABBR.get(role, "?"),
        variable_type or "?",
        tail,
    )


def _literal_class(node: Lit) -> str:
    if node.kind == "BOOL":
        return "BT" if node.val.upper() == "TRUE" else "BF"
    if node.kind == "NUM":
        # Kept identical to the Article 3 prototype so published aggregates
        # can be reproduced when the same parser and corpus snapshot are used.
        is_zero = node.val.replace(".", "").strip("0") == ""
        return "N0" if is_zero else "N"
    if node.kind == "TIME":
        return "T"
    return "S"


def expression_signature(
    node: object,
    variables: Mapping[str, tuple[str, str]],
    m: int,
    depth: int = 0,
) -> str:
    """Return the Article 3 typed signature of an expression node.

    Leaves (variables and literals) remain visible at every ``m``.  Composite
    structure at depth ``m`` or deeper is folded to ``~``.  As in the reported
    Article 3 experiment, all call arguments are sorted after their formal
    labels have been removed.  This makes the representation insensitive to
    that textual order, but it must not be interpreted as semantic equivalence
    for functions with positional parameters.
    """

    if isinstance(node, Lit):
        return _literal_class(node)
    if isinstance(node, Var):
        return _typed_path(node.path, variables)
    if depth >= m:
        return "~"
    if isinstance(node, Unary):
        return "%s(%s)" % (
            node.op,
            expression_signature(node.e, variables, m, depth + 1),
        )
    if isinstance(node, Bin):
        left = expression_signature(node.l, variables, m, depth + 1)
        right = expression_signature(node.r, variables, m, depth + 1)
        if node.op in _COMMUTATIVE:
            left, right = sorted((left, right))
        return "(%s%s%s)" % (left, node.op, right)
    if isinstance(node, Call):
        function_name = node.name.split(".")[-1]
        arguments = sorted(
            expression_signature(
                argument.val if isinstance(argument, NamedArg) else argument,
                variables,
                m,
                depth + 1,
            )
            for argument in node.args
        )
        return "%s(%s)" % (function_name, ",".join(arguments))
    raise SignatureInductionError(
        "unsupported expression node: %s" % type(node).__name__
    )


def _statement_signature(
    statement: object,
    variables: Mapping[str, tuple[str, str]],
    m: int,
    *,
    reject_raw: bool,
) -> tuple[str, list[list[object]]]:
    if isinstance(statement, Assign):
        signature = "A %s:=%s" % (
            _typed_path(statement.lhs, variables),
            expression_signature(statement.rhs, variables, m),
        )
        return signature, []
    if isinstance(statement, CallStmt):
        instance_name = statement.call.name.split(".")[0]
        _, instance_type = variables.get(instance_name.lower(), (None, None))
        call_kind = (
            instance_type
            if instance_type in (TIMER_TYPES | EDGE_TYPES)
            else (instance_type or "FB")
        )
        arguments = sorted(
            expression_signature(
                argument.val if isinstance(argument, NamedArg) else argument,
                variables,
                m,
            )
            for argument in statement.call.args
        )
        return "C %s(%s)" % (call_kind, ",".join(arguments)), []
    if isinstance(statement, If):
        conditions = ";".join(
            expression_signature(condition, variables, m)
            for condition, _ in statement.branches
        )
        bodies = [body for _, body in statement.branches]
        if statement.els is not None:
            bodies.append(statement.els)
        signature = "IF[n=%d%s]{%s}" % (
            len(statement.branches),
            "+e" if statement.els is not None else "",
            conditions,
        )
        return signature, bodies
    if isinstance(statement, Case):
        bodies = [body for _, body in statement.cases]
        if statement.els is not None:
            bodies.append(statement.els)
        return (
            "CASE[%s]" % expression_signature(statement.sel, variables, m),
            bodies,
        )
    if isinstance(statement, Loop):
        return "LOOP:%s" % statement.kind, [statement.body]
    if isinstance(statement, Simple):
        return "KW:%s" % statement.kw, []
    if isinstance(statement, Raw):
        if reject_raw:
            excerpt = " ".join(statement.text.split())[:120]
            raise UnsupportedStatementError(
                "parser recovery produced RAW for unsupported syntax"
                + (": " + excerpt if excerpt else "")
            )
        return "RAW", []
    raise SignatureInductionError(
        "unsupported statement node: %s" % type(statement).__name__
    )


def _walk_statements(
    statements: Iterable[object],
    variables: Mapping[str, tuple[str, str]],
    m: int,
    output: list[str],
    *,
    reject_raw: bool,
) -> None:
    """Pre-order traversal that preserves source and branch order."""

    for statement in statements:
        signature, child_bodies = _statement_signature(
            statement,
            variables,
            m,
            reject_raw=reject_raw,
        )
        output.append(signature)
        for body in child_bodies:
            _walk_statements(
                body,
                variables,
                m,
                output,
                reject_raw=reject_raw,
            )


def signature_sequence(
    source: str,
    m: int = DEFAULT_M,
    *,
    strict: bool = True,
    reject_raw: bool = True,
) -> tuple[str, ...]:
    """Convert one self-contained ST unit to an ordered signature sequence.

    ``strict=True`` rejects unsupported lexical characters.  ``reject_raw``
    additionally rejects syntax that the parser could only retain as a
    recovery node.  Both safeguards are enabled by default.
    """

    _validate_k_m(1, m)
    variables, body = split_unit(source)
    tokens = st_lex(body, strict=strict)
    statements = ParserV2(tokens).parse_stmt_list()
    result: list[str] = []
    _walk_statements(
        statements,
        variables,
        m,
        result,
        reject_raw=reject_raw,
    )
    return tuple(result)


def generic_kind(signature: str) -> str:
    """Return the statement-kind label used for the rare-signature tail."""

    if signature.startswith("IF["):
        return "IF"
    if signature.startswith("CASE["):
        return "CASE"
    if signature.startswith("LOOP:"):
        return "LOOP"
    if signature.startswith("KW:"):
        return "KW"
    if signature.startswith("A "):
        return "A"
    if signature.startswith("C "):
        return "C"
    if signature == "RAW":
        return "RAW"
    raise SignatureInductionError("cannot derive generic kind from %r" % signature)


def generic_symbol(signature: str) -> str:
    """Return the explicit finite-alphabet symbol for a rare signature."""

    return "GEN:" + generic_kind(signature)


def template_symbol(signature: str) -> str:
    """Return a stable identifier derived only from the complete signature."""

    digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return "SIG:" + digest


def encode_sequence(
    sequence: Sequence[str],
    frequent_signatures: set[str] | frozenset[str],
) -> tuple[str, ...]:
    """Encode an ordered sequence with frequent templates and generic tail."""

    return tuple(
        template_symbol(signature)
        if signature in frequent_signatures
        else generic_symbol(signature)
        for signature in sequence
    )


def _stable_frequency_order(counts: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _coverage_mark(
    ranked: Sequence[tuple[str, int]],
    total: int,
    share: float,
) -> int | None:
    if total == 0:
        return None
    cumulative = 0
    for rank, (_, count) in enumerate(ranked, 1):
        cumulative += count
        if cumulative / total >= share:
            return rank
    return None


def _manifest_sha256(sources: Sequence[CorpusSource]) -> str:
    digest = hashlib.sha256()
    for source in sources:
        digest.update(source.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.raw_bytes)
        digest.update(b"\0")
    return digest.hexdigest()


def strict_lexical_audit(
    sources: Sequence[CorpusSource],
    *,
    encoding: str = "utf-8",
) -> dict:
    """Report characters that strict lexical analysis would reject.

    The audit examines each original file exactly once.  Parent declarations
    prepended for the optional extracted-method mode are therefore not counted
    a second time.
    """

    characters: Counter[str] = Counter()
    affected_files: list[str] = []
    for source in sources:
        original_text = source.raw_bytes.decode(encoding, errors="strict")
        local = Counter(
            match.group()
            for match in _MASTER.finditer(original_text)
            if match.lastgroup == "MISMATCH"
        )
        if local:
            affected_files.append(source.relative_path)
            characters.update(local)
    return {
        "files_with_unsupported_characters": len(affected_files),
        "unsupported_character_occurrences": sum(characters.values()),
        "characters": dict(sorted(characters.items())),
        "affected_files": affected_files,
    }


def discover_st_files(root: Path, *, recursive: bool = False) -> list[Path]:
    """Discover ST files in a deterministic, platform-independent order."""

    if not root.is_dir():
        raise CorpusInputError("corpus root is not a directory")
    candidates = root.rglob("*") if recursive else root.iterdir()
    files = [
        path
        for path in candidates
        if path.is_file() and path.suffix.casefold() == ".st"
    ]
    files.sort(
        key=lambda path: (
            path.relative_to(root).as_posix().casefold(),
            path.relative_to(root).as_posix(),
        )
    )
    if not files:
        raise CorpusInputError("no .st files found in the corpus directory")

    relative_names = [path.relative_to(root).as_posix() for path in files]
    folded = [name.casefold() for name in relative_names]
    if len(folded) != len(set(folded)):
        raise CorpusInputError(
            "case-insensitive duplicate relative paths make ordering ambiguous"
        )
    return files


def load_corpus(
    root: Path,
    *,
    recursive: bool = False,
    encoding: str = "utf-8",
    parent_context: bool = False,
    parent_separator: str = DEFAULT_PARENT_SEPARATOR,
) -> tuple[CorpusSource, ...]:
    """Load caller-provided ST files without embedding an absolute root path.

    With ``parent_context=True``, an extracted unit named ``X__Y.st`` receives
    the declaration sections from ``X.st`` in the same directory.  This mode
    is explicit because the filename convention is not part of IEC 61131-3.
    """

    if parent_context and not parent_separator:
        raise CorpusInputError("parent_separator must not be empty")

    sources: list[CorpusSource] = []
    for path in discover_st_files(root, recursive=recursive):
        relative_path = path.relative_to(root).as_posix()
        raw_bytes = path.read_bytes()
        try:
            text = raw_bytes.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise CorpusInputError(
                "%s cannot be decoded with %s: %s"
                % (relative_path, encoding, exc)
            ) from exc
        sources.append(CorpusSource(relative_path, text, raw_bytes))

    if parent_context:
        by_relative_name = {
            source.relative_path.casefold(): source for source in sources
        }
        contextualized: list[CorpusSource] = []
        for source in sources:
            relative = PurePosixPath(source.relative_path)
            if parent_separator not in relative.stem:
                contextualized.append(source)
                continue
            parent_stem = relative.stem.split(parent_separator, 1)[0]
            parent_relative = (
                relative.parent / (parent_stem + ".st")
            ).as_posix()
            parent = by_relative_name.get(parent_relative.casefold())
            if parent is None:
                contextualized.append(source)
                continue
            declarations = "\n".join(
                match.group() for match in _PARENT_VAR_SECTION.finditer(parent.text)
            )
            analysis_text = (
                declarations + "\n" + source.text
                if declarations
                else source.text
            )
            contextualized.append(CorpusSource(
                source.relative_path,
                analysis_text,
                source.raw_bytes,
                context_from=parent.relative_path,
            ))
        sources = contextualized
    return tuple(sources)


def _sequences_for_sources(
    sources: Sequence[CorpusSource],
    m: int,
    *,
    strict: bool,
    reject_raw: bool,
) -> list[tuple[str, tuple[str, ...]]]:
    sequences: list[tuple[str, tuple[str, ...]]] = []
    for source in sources:
        try:
            sequence = signature_sequence(
                source.text,
                m,
                strict=strict,
                reject_raw=reject_raw,
            )
        except (ValueError, SyntaxError) as exc:
            raise CorpusInputError("%s: %s" % (source.relative_path, exc)) from exc
        sequences.append((source.relative_path, sequence))
    return sequences


def _summary_from_sequences(
    sequences: Sequence[tuple[str, tuple[str, ...]]],
    *,
    k: int,
    m: int,
) -> tuple[dict, Counter[str], frozenset[str]]:
    counts: Counter[str] = Counter()
    for _, sequence in sequences:
        counts.update(sequence)

    ranked = _stable_frequency_order(counts)
    total = sum(counts.values())
    frequent = frozenset(signature for signature, count in counts.items() if count >= k)
    covered = sum(counts[signature] for signature in frequent)
    tail_signatures = [signature for signature in counts if signature not in frequent]
    tail_symbols = sorted({generic_symbol(signature) for signature in tail_signatures})
    summary = {
        "m": m,
        "k": k,
        "total_sequence_elements": total,
        "distinct_signatures": len(counts),
        "frequent_template_count": len(frequent),
        "frequent_template_occurrences": covered,
        "frequent_template_coverage": covered / total if total else 0.0,
        "raw_signature_occurrences": counts.get("RAW", 0),
        "generic_tail_symbols": tail_symbols,
        "encoded_alphabet_size": len(frequent) + len(tail_symbols),
        "templates_for_50_percent": _coverage_mark(ranked, total, 0.50),
        "templates_for_80_percent": _coverage_mark(ranked, total, 0.80),
        "templates_for_90_percent": _coverage_mark(ranked, total, 0.90),
        "templates_for_95_percent": _coverage_mark(ranked, total, 0.95),
    }
    return summary, counts, frequent


def induce_sources(
    sources: Sequence[CorpusSource],
    *,
    k: int = DEFAULT_K,
    m: int = DEFAULT_M,
    strict: bool = True,
    reject_raw: bool = True,
    recursive: bool = False,
    encoding: str = "utf-8",
    parent_context: bool = False,
    parent_separator: str = DEFAULT_PARENT_SEPARATOR,
    include_sequences: bool = True,
) -> dict:
    """Induce a deterministic codebook from already loaded corpus sources."""

    _validate_k_m(k, m)
    if not sources:
        raise CorpusInputError("the corpus source list is empty")
    sequences = _sequences_for_sources(
        sources,
        m,
        strict=strict,
        reject_raw=reject_raw,
    )
    summary, counts, frequent = _summary_from_sequences(
        sequences,
        k=k,
        m=m,
    )
    ranked = _stable_frequency_order(counts)

    templates = []
    for rank, (signature, frequency) in enumerate(ranked, 1):
        if signature not in frequent:
            continue
        templates.append({
            "rank": rank,
            "symbol": template_symbol(signature),
            "signature": signature,
            "frequency": frequency,
            "share": frequency / summary["total_sequence_elements"]
            if summary["total_sequence_elements"]
            else 0.0,
        })

    generic_rows = []
    for symbol in summary["generic_tail_symbols"]:
        kind = symbol.split(":", 1)[1]
        relevant = [
            (signature, frequency)
            for signature, frequency in counts.items()
            if signature not in frequent and generic_kind(signature) == kind
        ]
        generic_rows.append({
            "symbol": symbol,
            "kind": kind,
            "distinct_signatures": len(relevant),
            "frequency": sum(frequency for _, frequency in relevant),
        })

    report = {
        "schema": SCHEMA_VERSION,
        "implementation_version": IMPLEMENTATION_VERSION,
        "signature_schema": SIGNATURE_SCHEMA,
        "configuration": {
            "k": k,
            "m": m,
            "strict_lexer": strict,
            "reject_raw": reject_raw,
            "recursive": recursive,
            "encoding": encoding,
            "parent_context": parent_context,
            "parent_separator": parent_separator if parent_context else None,
        },
        "corpus": {
            "file_count": len(sources),
            "files": [source.relative_path for source in sources],
            "manifest_sha256": _manifest_sha256(sources),
            "parent_context_links": [
                {"unit": source.relative_path, "parent": source.context_from}
                for source in sources
                if source.context_from is not None
            ],
        },
        "strict_lexical_audit": strict_lexical_audit(
            sources,
            encoding=encoding,
        ),
        "summary": summary,
        "codebook": {
            "frequent_templates": templates,
            "generic_tail": generic_rows,
        },
    }
    if include_sequences:
        report["units"] = [
            {
                "file": relative_path,
                "full_sequence": list(sequence),
                "encoded_sequence": list(encode_sequence(sequence, frequent)),
            }
            for relative_path, sequence in sequences
        ]
    return report


def induce_directory(
    root: Path,
    *,
    k: int = DEFAULT_K,
    m: int = DEFAULT_M,
    strict: bool = True,
    reject_raw: bool = True,
    recursive: bool = False,
    encoding: str = "utf-8",
    parent_context: bool = False,
    parent_separator: str = DEFAULT_PARENT_SEPARATOR,
    include_sequences: bool = True,
) -> dict:
    """Load a user directory and return its deterministic induced codebook."""

    sources = load_corpus(
        root,
        recursive=recursive,
        encoding=encoding,
        parent_context=parent_context,
        parent_separator=parent_separator,
    )
    return induce_sources(
        sources,
        k=k,
        m=m,
        strict=strict,
        reject_raw=reject_raw,
        recursive=recursive,
        encoding=encoding,
        parent_context=parent_context,
        parent_separator=parent_separator,
        include_sequences=include_sequences,
    )


def sweep_sources(
    sources: Sequence[CorpusSource],
    *,
    k: int = DEFAULT_K,
    m_values: Sequence[int] = DEFAULT_SWEEP_M,
    strict: bool = True,
    reject_raw: bool = True,
    recursive: bool = False,
    encoding: str = "utf-8",
    parent_context: bool = False,
    parent_separator: str = DEFAULT_PARENT_SEPARATOR,
) -> dict:
    """Calculate deterministic dictionary/coverage rows for several ``m``."""

    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise SignatureInductionError("k must be an integer >= 1")
    if not sources:
        raise CorpusInputError("the corpus source list is empty")
    normalized_m = sorted(set(m_values))
    if not normalized_m:
        raise SignatureInductionError("at least one m value is required")
    for m in normalized_m:
        _validate_k_m(k, m)

    rows = []
    for m in normalized_m:
        sequences = _sequences_for_sources(
            sources,
            m,
            strict=strict,
            reject_raw=reject_raw,
        )
        summary, _, _ = _summary_from_sequences(sequences, k=k, m=m)
        rows.append(summary)

    return {
        "schema": SCHEMA_VERSION,
        "implementation_version": IMPLEMENTATION_VERSION,
        "signature_schema": SIGNATURE_SCHEMA,
        "configuration": {
            "k": k,
            "m_values": normalized_m,
            "strict_lexer": strict,
            "reject_raw": reject_raw,
            "recursive": recursive,
            "encoding": encoding,
            "parent_context": parent_context,
            "parent_separator": parent_separator if parent_context else None,
        },
        "corpus": {
            "file_count": len(sources),
            "files": [source.relative_path for source in sources],
            "manifest_sha256": _manifest_sha256(sources),
            "parent_context_links": [
                {"unit": source.relative_path, "parent": source.context_from}
                for source in sources
                if source.context_from is not None
            ],
        },
        "strict_lexical_audit": strict_lexical_audit(
            sources,
            encoding=encoding,
        ),
        "rows": rows,
    }


def sweep_directory(
    root: Path,
    *,
    k: int = DEFAULT_K,
    m_values: Sequence[int] = DEFAULT_SWEEP_M,
    strict: bool = True,
    reject_raw: bool = True,
    recursive: bool = False,
    encoding: str = "utf-8",
    parent_context: bool = False,
    parent_separator: str = DEFAULT_PARENT_SEPARATOR,
) -> dict:
    """Load a user directory once and sweep the selected depth values."""

    sources = load_corpus(
        root,
        recursive=recursive,
        encoding=encoding,
        parent_context=parent_context,
        parent_separator=parent_separator,
    )
    return sweep_sources(
        sources,
        k=k,
        m_values=m_values,
        strict=strict,
        reject_raw=reject_raw,
        recursive=recursive,
        encoding=encoding,
        parent_context=parent_context,
        parent_separator=parent_separator,
    )


def deterministic_json(payload: Mapping[str, object]) -> str:
    """Serialize a report with stable key order, indentation, and final newline."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("root", type=Path, help="directory containing .st files")
    parser.add_argument("--k", type=_positive_int, default=DEFAULT_K)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument(
        "--parent-context",
        action="store_true",
        help=(
            "for X__Y.st, prepend declaration sections from sibling X.st "
            "(explicit extracted-unit compatibility mode)"
        ),
    )
    parser.add_argument(
        "--parent-separator",
        default=DEFAULT_PARENT_SEPARATOR,
        help="separator used by --parent-context (default: __)",
    )
    parser.add_argument(
        "--permissive",
        "--permissive-lexer",
        dest="permissive_lexer",
        action="store_true",
        help="silently skip unsupported characters (unsafe historical mode)",
    )
    parser.add_argument(
        "--allow-raw",
        action="store_true",
        help="retain parser-recovery RAW signatures instead of failing",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write deterministic UTF-8 JSON here instead of stdout",
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    induce_parser = subparsers.add_parser(
        "induce",
        help="induce one codebook and ordered sequences",
    )
    _add_common_arguments(induce_parser)
    induce_parser.add_argument("--m", type=_nonnegative_int, default=DEFAULT_M)
    induce_parser.add_argument(
        "--omit-sequences",
        action="store_true",
        help="omit per-file full and encoded ordered sequences",
    )

    sweep_parser = subparsers.add_parser(
        "sweep",
        help="compare dictionary size and coverage across m values",
    )
    _add_common_arguments(sweep_parser)
    sweep_parser.add_argument(
        "--m",
        type=_nonnegative_int,
        nargs="+",
        default=list(DEFAULT_SWEEP_M),
        metavar="M",
    )
    return parser


def _emit_json(payload: Mapping[str, object], output: Path | None) -> None:
    rendered = deterministic_json(payload)
    if output is None:
        print(rendered, end="")
    else:
        output.write_text(rendered, encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = build_arg_parser().parse_args(argv)
    strict = not args.permissive_lexer
    reject_raw = not args.allow_raw
    if not strict:
        print(
            "WARNING: permissive lexer enabled; unsupported characters will be "
            "discarded. Use only for historical compatibility and inspect "
            "strict_lexical_audit in the JSON report.",
            file=sys.stderr,
        )
    if not reject_raw:
        print(
            "WARNING: RAW parser-recovery signatures are allowed; successful "
            "output does not imply that all statements were understood.",
            file=sys.stderr,
        )
    try:
        if args.command == "induce":
            payload = induce_directory(
                args.root,
                k=args.k,
                m=args.m,
                strict=strict,
                reject_raw=reject_raw,
                recursive=args.recursive,
                encoding=args.encoding,
                parent_context=args.parent_context,
                parent_separator=args.parent_separator,
                include_sequences=not args.omit_sequences,
            )
        else:
            payload = sweep_directory(
                args.root,
                k=args.k,
                m_values=args.m,
                strict=strict,
                reject_raw=reject_raw,
                recursive=args.recursive,
                encoding=args.encoding,
                parent_context=args.parent_context,
                parent_separator=args.parent_separator,
            )
        _emit_json(payload, args.output)
    except (OSError, SignatureInductionError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
