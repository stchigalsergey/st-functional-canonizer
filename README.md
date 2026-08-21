# ST Functional Canonizer

Research artifact for converting IEC 61131-3 Structured Text (ST) into a
sequence of symbols and comparing two sequences with Levenshtein distance.
The implementation targets a practical CODESYS-oriented subset used in an
industrial-ventilation study.  It is not an IEC 61131-3 compiler or a proof of
program equivalence.

## Repository layout

- `src/st_canonizer.py` — strict-by-default lexical analysis, AST classes,
  local recognizers and Levenshtein distance;
- `src/st_canonizer_v2.py` — improved parsing of consecutive `CASE` labels;
- `legacy/` — the unmodified v1/v2 files used for the historical corpus
  figures;
- `tests/` — standard-library regression tests;
- `examples/demo.py` — a minimal use example;
- `scripts/validate_corpus.py` — generic validation of a directory of ST files;
- `scripts/corpus_fingerprint.py` — a stable SHA-256 fingerprint of an exact
  text corpus snapshot.

No production project, proprietary ST source, or third-party dataset is
included.  Obtain external corpora from their original publishers and verify
the snapshot before comparing numbers.

Comments in the unmodified `legacy/` files may mention historical workspace
reports that are not part of this public artifact.  They are retained only to
preserve the exact implementation used for the reported historical runs.

## Quick start

Python 3.10 or newer is sufficient; the project has no runtime dependencies.

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
python examples/demo.py
python scripts/validate_corpus.py path\to\st-files
python scripts/corpus_fingerprint.py path\to\st-files
```

The public API is strict by default:

```python
from st_canonizer_v2 import canonize

symbols = canonize(source)                 # unsupported characters raise
legacy_symbols = canonize(source, strict=False)  # permissive reproduction
```

## What is and is not normalized

The current pipeline uses local recognition rules.  It does not run the
`canon_expr` serializer as a general AST-normalization pass.  In particular:

- a reset-priority latch is recognized only when the SET `IF` precedes the
  RESET `IF`;
- positional function arguments keep their order;
- `B_LIMIT` records that `LIMIT(...)` is present, but does not validate or
  encode its bounds;
- `FALSE`/zero is an *off* value, not universally a safe process state;
- `B_RAW` and a lack of exceptions are diagnostics, not evidence of semantic
  correctness.

The unmodified legacy lexer silently discarded unsupported characters.  The
public implementation raises `UnsupportedCharacterError` by default so that
such syntax cannot disappear before parsing.

## Reproducibility note

The paper reports both historical permissive results and an audit of their
limitations.  Use files under `legacy/` only for reproducing those historical
figures.  Use files under `src/` for new experiments.  A corpus result should
always include:

1. number of input files;
2. number of expected declaration-only outputs and unexpected empty outputs;
3. number of exceptions;
4. number of emitted symbols and `B_RAW` symbols;
5. strict lexical failures;
6. corpus fingerprint from `corpus_fingerprint.py`.

## License

This project is released under the [MIT License](LICENSE).

Copyright (c) 2026 Sergey Evgenievich Shchigal.
