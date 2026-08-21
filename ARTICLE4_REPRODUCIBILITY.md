# Reproducing the public Article 4 synthetic results

## Scope

Version 0.3.0 is a partial computational artifact for Article 4. It publishes
a deterministic synthetic generator, a contextual edit-profile API, tests and
aggregate evidence. It does not publish industrial Structured Text, the
private induced codebook, real-project task files, or third-party datasets.

The public experiment answers two limited questions:

1. Do the implemented canonicalization rules assign distance zero to the
   generator-defined equivalent variants?
2. Do the deliberately injected synthetic changes produce non-zero distance,
   and how often does a selected optimal Levenshtein path contain the same
   upper-level edit kind as the generator label?

It does not test program behavior, compiler acceptance, automatic defect
severity, or performance on independently generated LLM code.

## One-command check

From a source checkout:

```powershell
python -m pip install -e .
python scripts/reproduce_article4.py
```

The command regenerates all examples in memory, compares the expected public
summary fields and implementation hashes with
`reproducibility/article4_manifest.json`, and exits non-zero on a mismatch.
It does not create a corpus unless an output path is supplied explicitly:

```powershell
python scripts/reproduce_article4.py `
  --output article4_generated\synthetic.jsonl `
  --report article4_generated\verification.json
```

The generated directory is ignored by Git. The JSONL is synthetic, but it is
kept out of the repository so that the source, manifest and tests remain the
single maintained artifact.

## Deterministic v0.3 generator

The generator contains 16 templates and produces 282 reference records in a
fixed order. Each record has one intended-equivalent rendering and at most
four applicable controlled mutations. The equivalent rendering uses
consistent identifier renaming and, where applicable, the alternative
reset-priority latch form.

The historical workspace generator derived per-record random seeds through
Python's process-randomized `hash()`. Its aggregate values were stable, but
its JSONL bytes were not. Version 0.3 derives all style flags directly from
SHA-256. The v0.3 JSONL is therefore a new deterministic regeneration: its
checksum is recorded in the manifest, but it is not claimed to be
byte-identical to every historical workspace run.

With the default limit of four defects per reference, seven mutation labels
are represented. The configured FSM-transition mutation is eighth in protocol
order and therefore has zero occurrences in the reported 990 variants. These
counts describe generator construction, not frequencies of errors made by an
LLM.

## Public expected results

The full v0.3 synthetic run contains:

- 282 reference programs across categories A=166, C=36, D=8 and E=72;
- 16 templates and 27 distinct canonical block sequences;
- 282/282 intended-equivalent variants with block distance zero;
- 990/990 controlled changes with non-zero block distance;
- no generation log entries under strict canonicalization.

The full check comprises 1,272 comparisons: 282 reference/equivalent pairs and
990 reference/changed pairs. The generator renders 1,554 program texts:
282 references, 282 equivalents and 990 changed variants.

The first two records from each pair of template and canonical sequence form a
fixed 56-record working subset with 188 controlled changes. Under the
historical unit-cost Levenshtein backtrace rule, 152/188 changes have at least
one selected operation whose upper-level kind—replacement, deletion or
insertion—matches the generator label.

The 152/188 value is edit-kind agreement under one versioned tie-breaking
rule. It is not functional-category accuracy, precision, recall, F1, defect
detection accuracy or a severity score. The public manifest stores the exact
`tie_break_id`, per-label counts and the descriptive edit profile.

## Functional categories and output safety

Numeric distance, selected edit kind and functional category are separate
layers. The default category resolver labels `B_SET_OUTPUT_OFF`,
`B_SET_OUTPUT_ON` and `B_SET_OUTPUT` as `OUTPUT_UNSPECIFIED`.
It assigns safe or working position only when the caller supplies an explicit
per-occurrence project context whose commanded state matches the declared
safe or working state.

Consequently, `FALSE`, zero and `OFF` do not mean safe by themselves.
The state opposite a declared safe state is not automatically treated as the
working state. The synthetic generator's mutation labels come from its known
construction protocol; they are not inferred by this contextual resolver.

Several optimal edit scripts may have the same distance. Each comparison
records whether the optimum is ambiguous and the rule used to select one
path. The optional move-aware rule can expose a simple permutation as a
delete/insert pair, but a possible move remains an interpretation rather than
a native Levenshtein operation.

## Non-public and differently represented results

The manuscript's six controlled real-project cases are not redistributed.
Their 4/6 edit-kind contribution used the same coarse canonical representation
as the synthetic 152/188 calculation. The combined reported 156/194 result is
therefore only partially publicly reproducible.

The six real-code rows in the manuscript's controlled comparison table are a
different experiment: they use the private threshold representation
`D(5,1)`, not the coarse representation used for 152/188 and 4/6. Those
rows must not be mixed with the edit-kind agreement calculation. Their source
and induced codebook remain private, so the public artifact records only this
boundary and does not claim end-to-end reproduction.

Article 4 also discusses Article 3 calculations on STMutants. They remain
governed by `reproducibility/article3_manifest.json` and
`THIRD_PARTY.md`; STMutants is not bundled here.

## Claim boundaries

The artifact does not establish:

- behavioral equivalence or safety of any program;
- successful compilation or execution of all generated variants;
- taxonomy completeness or independently expert-validated labels;
- automatic functional-category accuracy or severity prediction;
- precision, recall, F1 or localization accuracy;
- an error profile of real LLM-generated ST;
- a universal distance threshold.

The 282/282 result is an internal invariance check for transformations
implemented by the generator. The 990/990 result is a distinguishability check
for deliberately constructed mutations. New claims require an independent
frozen evaluation corpus and, for safety-related categories, explicit project
context and expert assessment.
