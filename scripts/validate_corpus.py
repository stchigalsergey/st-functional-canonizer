"""Validate every .st file under a directory and print JSON diagnostics."""

from argparse import ArgumentParser
from collections import Counter
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import st_canonizer as base  # noqa: E402
import st_canonizer_v2 as v2  # noqa: E402


def has_executable_tokens(tokens):
    """Return true when the body contains more than empty separators."""
    return any(token.kind != "SEMI" for token in tokens)


def validate(root: Path, strict: bool):
    result = Counter()
    result["files"] = 0
    details = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() != ".st":
            continue
        result["files"] += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            _, body = base.split_unit(text)
            tokens = base.st_lex(body, strict=strict)
            symbols = v2.canonize(text, strict=strict)
        except base.UnsupportedCharacterError as exc:
            result["lexical_failures"] += 1
            details.append({"file": path.relative_to(root).as_posix(),
                            "kind": "unsupported_character", "message": str(exc)})
            continue
        except Exception as exc:  # diagnostic script must count all failures
            result["exceptions"] += 1
            details.append({"file": path.relative_to(root).as_posix(),
                            "kind": type(exc).__name__, "message": str(exc)})
            continue
        if not symbols and not has_executable_tokens(tokens):
            result["empty_expected"] += 1
            continue
        if not symbols:
            result["empty_unexpected"] += 1
            details.append({"file": path.relative_to(root).as_posix(),
                            "kind": "unexpected_empty_output"})
            continue
        result["nonempty_outputs"] += 1
        result["symbols"] += len(symbols)
        raw = sum(symbol.startswith("B_RAW") for symbol in symbols)
        result["raw_symbols"] += raw
        if raw:
            result["files_with_raw"] += 1
    payload = dict(sorted(result.items()))
    payload["raw_share"] = (
        result["raw_symbols"] / result["symbols"] if result["symbols"] else 0.0
    )
    payload["strict"] = strict
    payload["details"] = details
    return payload


def main():
    parser = ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument(
        "--permissive", action="store_true",
        help="reproduce legacy skipping of unsupported characters",
    )
    args = parser.parse_args()
    print(json.dumps(
        validate(args.root.resolve(), strict=not args.permissive),
        ensure_ascii=False, indent=2,
    ))


if __name__ == "__main__":
    main()
