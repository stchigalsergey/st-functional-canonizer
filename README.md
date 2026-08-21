# ST Functional Canonizer

Research artifact for converting IEC 61131-3 Structured Text (ST) into a
sequence of symbols, inducing a corpus-dependent typed-signature codebook, and
comparing ordered sequences with Levenshtein distance.  The implementation
targets a practical CODESYS-oriented subset.  It is not an IEC 61131-3
compiler, a defect detector, or a proof of program equivalence.

Version 0.3.0 is a **partial computational artifact** for Articles 3 and 4.
It contains the reusable algorithms, a deterministic synthetic generator and
tests, but no proprietary industrial corpus or third-party mutation dataset.
The repository reproduces the public synthetic component of Article 4; it
cannot regenerate results that depend on the private corpus or codebook.

## Repository layout

- `src/st_canonizer.py` — strict-by-default lexical analysis, AST classes,
  local recognizers and Levenshtein distance;
- `src/st_canonizer_v2.py` — improved parsing of consecutive `CASE` labels;
- `src/st_signature_induction.py` — strict-by-default typed signatures,
  deterministic `K`/`m` induction, ordered sequences, and a generic tail;
- `src/st_defect_taxonomy.py` — deterministic edit scenarios and contextual
  functional categories for Article 4;
- `src/st_article4_synthetic.py` — deterministic, synthetic-only Article 4
  generator and aggregate report;
- `legacy/` — the unmodified v1/v2 files used for the historical corpus
  figures;
- `tests/` — standard-library regression tests;
- `examples/demo.py` — a minimal use example;
- `scripts/validate_corpus.py` — generic validation of a directory of ST files;
- `scripts/corpus_fingerprint.py` — a stable SHA-256 fingerprint of an exact
  text corpus snapshot;
- `scripts/induce_signatures.py` — source-checkout CLI for Article 3 induction
  and parameter sweep;
- `scripts/check_article3_sweep.py` — comparison of a local sweep with the
  anonymized expected aggregates;
- `scripts/reproduce_article4.py` — end-to-end verification of the public
  synthetic Article 4 results;
- `reproducibility/article3_manifest.json` — input identities and anonymized
  expected aggregate results;
- `reproducibility/article4_manifest.json` — exact public synthetic results,
  implementation hashes and claim boundaries;
- `REPRODUCIBILITY.md` — exact commands and the public/private boundary;
- `ARTICLE4_REPRODUCIBILITY.md` — Article 4 experiment boundaries and commands;
- `THIRD_PARTY.md` — provenance and licensing of excluded data;
- `examples/article3_synthetic/` — a small public corpus for an end-to-end
  smoke test.

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
python scripts/induce_signatures.py induce path\to\st-files --k 5 --m 1 --output codebook.json
python scripts/induce_signatures.py sweep path\to\st-files --k 5 --m 0 1 2 3 --output sweep.json
python scripts/induce_signatures.py sweep examples\article3_synthetic --k 2 --m 0 1 --parent-context
python scripts/verify_stmutants_archive.py path\to\Mutations.zip
python scripts/reproduce_article4.py
```

The public API is strict by default:

```python
from st_canonizer_v2 import canonize

symbols = canonize(source)                 # unsupported characters raise
legacy_symbols = canonize(source, strict=False)  # permissive reproduction
```

Typed-signature induction is also strict by default.  It rejects both unknown
lexical characters and parser-recovery `RAW` statements:

```python
from pathlib import Path
from st_signature_induction import induce_directory, sweep_directory

report = induce_directory(Path("corpus"), k=5, m=1)
sweep = sweep_directory(Path("corpus"), k=5, m_values=(0, 1, 2, 3))
```

Every input `.st` file is treated as a self-contained unit.  For an extracted
method convention in which `X__Y.st` depends on declarations in sibling
`X.st`, enable the compatibility mode explicitly:

```powershell
python scripts/induce_signatures.py sweep path\to\st-files --parent-context --permissive
```

`--permissive` reproduces the historical lexer behaviour and can silently
discard unsupported characters.  The generated JSON still contains a strict
lexical audit with affected relative filenames, character counts, and exact
characters.  `--allow-raw` is a separate explicit opt-out from rejecting
parser recovery nodes.

Output is deterministic for identical bytes and options: files and tied
frequencies use explicit stable ordering, frequent templates receive
content-addressed `SIG:<sha256>` symbols, and rare templates become `GEN:*`
symbols without changing statement order.  Reports contain root-relative paths
and a corpus manifest hash, not the absolute corpus path.

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

The Article 3 typed-signature schema has additional, explicitly retained
research limitations:

- variable names are replaced by role and type, so identities of two variables
  with the same role/type may collapse;
- call arguments are sorted and formal labels are removed, including for
  position-sensitive functions;
- `CASE` label values and loop conditions/bounds are not represented;
- `m` controls expression depth and `K` controls the frequency threshold;
  neither value is a universal optimum;
- a codebook describes its input corpus and must be frozen before it is applied
  to an independent evaluation corpus.

Typed signatures retain called-function and declared-type names.  Generated
reports also contain relative input filenames.  Review a report before sharing
it if those identifiers are confidential.

The exact Article 3 commands, expected values, data checksums and limitations
are recorded in [REPRODUCIBILITY.md](REPRODUCIBILITY.md).  Do not publish a raw
industrial-corpus report: even without source text it contains filenames and
complete frequent signatures.

## Article 4 edit profiles

Article 4 keeps three layers separate: numeric Levenshtein distance, the
selected edit kind (`replace`, `delete`, or `insert`), and the functional
category assigned to each affected symbol.  Output commands are
`OUTPUT_UNSPECIFIED` unless the caller explicitly supplies project metadata
that identifies safe and working command states.  In particular, `FALSE`,
zero, and `B_SET_OUTPUT_OFF` do not establish safety by themselves.

An optimal Levenshtein path can be ambiguous.  Every result therefore records
a versioned `tie_break_id`.  The historical rule reproduces the reported
synthetic edit-kind aggregate; the optional move-aware rule represents a
simple swap as deletion plus insertion and reports possible moves separately.
Neither rule proves behavioral equivalence or engineering severity.

See [ARTICLE4_REPRODUCIBILITY.md](ARTICLE4_REPRODUCIBILITY.md) for the exact
public result, exclusions and regeneration command.

## Reproducibility note

The papers report historical permissive results together with an audit of
their limitations.  Use files under `legacy/` only for provenance of the
historical parser.  Use files under `src/` for new experiments.  Article 3
reports should be generated by `st_signature_induction`, which records `K`,
`m`, strictness flags, the input manifest, the strict lexical audit, codebook
coverage, generic-tail symbols, and (unless omitted) both full and encoded
ordered sequences.  The anonymized expected aggregates are stored separately
from private raw reports in
`reproducibility/article3_manifest.json`.

1. number of input files;
2. number of expected declaration-only outputs and unexpected empty outputs;
3. number of exceptions;
4. number of emitted symbols and `B_RAW` symbols;
5. strict lexical failures;
6. corpus fingerprint from `corpus_fingerprint.py`.

## License

This project is released under the [MIT License](LICENSE).

Copyright (c) 2026 Sergey Evgenievich Shchigal.
