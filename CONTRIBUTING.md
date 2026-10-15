# Contributing

OpenStat aims to grow an independently implemented and explicitly measured compatibility surface. Start with a small supported behavior and a synthetic do-file. Describe the expected semantics, edge cases, and relevant public documentation. Test against a licensed reference installation when available; tests must also run from checked-in synthetic reference values without Stata.

Do not contribute proprietary Stata sources, decompiled implementations, copyrighted assets/help corpora, package sources without suitable permission, personal datasets, credentials, or license information. Keep machine-specific REA evidence under ignored `.local/`. Public findings must identify their evidence type and limits.

Use strict command/option parsing. Missing primitives should produce errors rather than guessed results. Favor atomic validation before data/file mutation. Add numerical tolerances and missing-data/shape/rank cases for statistical changes. Include regression tests for corrected semantic errors.

Install `pip install -e ".[desktop,test]"`, run `python -m pytest -q`, and use `qa/run_differential.py` for short live conformance cases. Network cases are opt-in. Update stored references only after a genuine reference run; never change expected values merely to match the implementation. No required test should depend on private files or a Stata installation.
