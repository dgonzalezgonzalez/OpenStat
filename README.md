<p align="center"><img src="assets/logo.svg" width="420" alt="OpenStat"></p>

An independent, MIT-licensed statistical workstation with a command console, do-file editor, data browser, graphs, and a Python-powered analytical runtime.

**Compatibility remains under active development. OpenStat cannot yet run every do-file, the full Mata language, or arbitrary SSC packages.** Unsupported commands and options report errors. Passing the included examples establishes only those examples. See the [compatibility contract](docs/compatibility.md), [feature target inventory](docs/feature-inventory.md), and [REA investigation](docs/inspection.md).

![OpenStat workstation with synthetic data](docs/screenshot.png)

## Install and launch

Python 3.11–3.13 recommended. Windows was tested locally; Linux and macOS remain unverified. The independent runtime requires no external analytical application.

```powershell
git clone https://github.com/dgonzalezgonzalez/OpenStat.git
cd OpenStat
python -m venv .venv
.venv\Scripts\python -m pip install ".[desktop]"
.venv\Scripts\python -m openstat
```

Windows users can then double-click `OpenStat.cmd`. On macOS/Linux, use `.venv/bin/python` instead. For development, install `-e ".[desktop,test]"`. Qt Essentials supplies the desktop; headless installations need only `pip install .`.

Run a do-file without the GUI:

```sh
python -m openstat --batch examples/quickstart.do --json result.json
```

Batch runs use the current directory unless `--cwd DIRECTORY` is supplied. A script failure stops execution, produces a return code and source line, and sets a nonzero process exit status. JSON contains the full runtime return code. Commands already completed before an error retain their changes.

## What works

- Native desktop: Results, command entry/history, do-file syntax highlighting, line numbers, find, open/save, run selection, variables/properties, editable Data Browser, graph previews, and keyboard shortcuts. Background execution is serialized; Stop interrupts at statement boundaries.
- A bounded do-file language: comments, continuations, delimiters, local/global macros, simple loops, programs with positional `args`, stored `r()`/`e()` results, `capture`, `quietly`, `if`/`in`, variable wildcards and ranges.
- Data generation, filtering, sorting, grouped calculations, summaries, CSV/Excel import/export, append, validated merges, numeric long/wide reshape, collapse, preserve/restore, labels, encode/decode, and strict numeric/string conversions.
- Reading supported DTA variants through ReadStat; writing 114/117/118/119 through pandas. The live conformance suite tests **DTA118 numeric/string data and labels**, not every DTA feature. Extended missing values are rejected when loading instead of silently collapsed.
- OLS, HC1/cluster covariance, binary logit/probit, Poisson, predictions, basic hypothesis/mean tests, and matrix arithmetic through maintained numerical libraries. OLS, robust/clustered covariance and stored logistic results have bounded reference comparisons; other models need broader validation.
- A small interpreted Mata subset: real numeric matrices, assignment, transpose, matrix multiplication, selected algebra functions, `st_matrix()` and `st_numscalar()`; no compiled Mata, functions, classes, pointers, views, or `.mlib` execution.
- SSC and `net install` descriptor/file downloads, isolated package directory, manifests with hashes, and autoloading of compatible ado programs. Downloading a package does **not** prove it can execute. Most substantial community packages need syntax and primitives that are still missing.

## Quick example

```text
clear all
set obs 12
generate x = _n
generate z = mod(_n,3)
generate y = 1 + 2*x + .5*z + mod(_n,2)
regress y x z
display _b[x]
scatter y x
graph export "example.png", replace width(900)
```

The GUI uses an original OpenStat visual identity with a familiar statistical-workstation arrangement and original branding.

## Verification

```sh
python -m pip install ".[desktop,test]"
python -m pytest -q
python qa/run_differential.py
python qa/run_differential.py --online-ssc --case ssc
```

The optional differential runner requires a separately installed licensed reference engine. It generates temporary synthetic inputs, starts an isolated reference child with a maximum 30-second case timeout, and compares scalars, exported CSVs, DTA labels and PNG dimensions. It does not execute personal do-files or interfere with existing sessions. Network testing is opt-in. [Checked-in results](qa/report.json) identify tested cases, hashes, tolerances, timings, return codes, and reference version. [QA instructions](qa/README.md) explain the stored reference values used by offline tests.

**Verification boundary:** the installed reference snapshot and its revision are recorded in the inspection documents. Reference revisions, features, and numerical cases require explicit verification. No exhaustive equivalence is claimed from a small test suite.

## Development priorities

1. Formal grammar, macro scopes, strings, missing values, storage types, and adversarial semantic tests.
2. Ado `syntax`/parsing primitives and package dependency discovery, tested package by package.
3. Matrix/Mata language and numerical conformance; extended DTA metadata/long-string coverage.
4. Statistical families, postestimation, factor/time-series operators, weights, panel/survey/MI/Bayesian workflows.
5. Native installers, cross-platform UI checks, richer graphs/reporting, and bounded conformance corpora.

Contributions should include a short synthetic reproducer, precise expected behavior, source/provenance, and numerical tolerances. Proprietary implementations, private scripts, and user datasets do not belong in this repository. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License and provenance

OpenStat's original code, documentation and logo are [MIT licensed](LICENSE). Dependencies and downloaded third-party packages keep their own licenses; see [THIRD_PARTY.md](THIRD_PARTY.md).
