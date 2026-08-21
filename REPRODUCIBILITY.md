# Reproducing the Article 3 results

## Reproducibility boundary

Version 0.2.0 is a partial computational artifact.  It publishes the reusable
typed-signature and codebook implementation, synthetic tests, input
fingerprints and aggregate reference values.  It does not publish the
industrial Structured Text corpus, the induced private codebook, or the
STMutants archive.

The repository is therefore sufficient to inspect and test the algorithm on
new or synthetic inputs.  Exact industrial-corpus values can be checked only
by a lawful holder of the matching 189-file snapshot.  Thresholded STMutants
values additionally require the codebook induced from that snapshot.

## Public smoke test

The synthetic example exercises the extracted-method filename convention,
strict parsing and a two-value depth sweep:

```powershell
python -m pip install -e .
python -m unittest discover -s tests -v
python scripts/induce_signatures.py sweep `
  examples\article3_synthetic `
  --k 2 --m 0 1 --parent-context
```

Strict lexical analysis and rejection of parser-recovery `RAW` statements are
the defaults for all new experiments.

## Industrial corpus identity

Expected top-level `.st` files: 189.

Expected manifest SHA-256:

`912f0e345c830e766300a167bdaca38b944f1a6b2361e146e97fd420336dcc25`

The manifest algorithm sorts relative POSIX paths case-insensitively and, for
each file, hashes the UTF-8 path, a NUL byte, the unmodified file bytes and a
second NUL byte.  Only the top-level `.st` files participate.  This is the
`manifest_sha256` produced by `st_signature_induction`; it is intentionally
different from the older generic fingerprint printed by
`scripts/corpus_fingerprint.py`.

The corpus contains extracted units named `X__Y.st`.  The reported Article 3
signatures add declaration sections from sibling `X.st`, so exact reproduction
requires the explicit `--parent-context` option.

## Exact sweep command

Run locally against an authorized copy of the matching snapshot:

```powershell
python scripts/induce_signatures.py sweep `
  path\to\the-189-file-corpus `
  --k 5 --m 0 1 2 3 `
  --parent-context `
  --permissive `
  --output local_article3_sweep.json
```

`--permissive` is required only to reproduce the historical experiment.  It
prints a warning because unsupported characters are discarded.  The JSON
still contains an independent strict lexical audit.  Without `--permissive`,
the same corpus correctly fails on unsupported CODESYS pointer dereferences.

Do not publish `local_article3_sweep.json`: it contains relative industrial
filenames.  An `induce` report additionally contains complete signatures and
the induced codebook.

Compare the local report with the anonymized reference manifest:

```powershell
python scripts/check_article3_sweep.py local_article3_sweep.json
```

The checker prints only aggregate mismatches; it does not echo filenames or
signatures from the private report.

## Audited results

Historical Parser v1, `m=1`, `K=5`:

- sequence elements: 7,819;
- distinct signatures: 2,732;
- `RAW` occurrences: 183;
- frequent templates: 233;
- covered occurrences: 4,263;
- coverage: 54.521%.

Corrected ParserV2 with the historical permissive lexer, `m=1`, `K=5`:

- sequence elements: 7,794;
- distinct signatures: 2,754;
- `RAW` occurrences: 0;
- frequent templates: 238;
- covered occurrences: 4,209;
- coverage: 54.003%;
- generic rare-tail symbols: 5;
- complete threshold alphabet: 243 symbols.

`RAW=0` does not establish complete lexical coverage.  The strict audit found
339 unsupported `^` characters in 58 of the 189 units.  The historical
permissive lexer discarded them.  ParserV2 and lexical strictness are separate
properties and must be reported separately.

All rows for `m=0..3`, the historical row and the external aggregate values
are machine-readable in
[`reproducibility/article3_manifest.json`](reproducibility/article3_manifest.json).

## STMutants

Obtain STMutants separately from its versioned Figshare record; see
[THIRD_PARTY.md](THIRD_PARTY.md).  Verify the downloaded archive before use:

```powershell
python scripts/verify_stmutants_archive.py path\to\Mutations.zip
```

The published archive contains 110 files.  The STMutants paper reports 108
mutants after observability/equivalence filtering, but the Article 3
calculation used all 110 files present in the archive.  Its denominator is
therefore 110 and must not be described as a result on the retained 108-item
subset.

The stored STMutants values count variants whose static representation differs
from a within-program modal sequence.  They do not measure semantic defect
detection, compilation, execution or test-suite mutation score.

## Known representation limits

The released implementation deliberately preserves the schema used for the
reported numbers.  It sorts function-call arguments after removing formal
labels, omits `CASE` label values and loop headers/bounds, and collapses
variable identity to role and declared type.  These choices are documented
limitations, not claims of semantic equivalence.  Changing them requires a new
schema version and new aggregate results.
