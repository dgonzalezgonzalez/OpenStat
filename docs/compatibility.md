# OpenStat 0.1 compatibility contract

OpenStat is an independent experimental runtime. **Arbitrary do-file, current StataNow, full Mata and general SSC package compatibility are not implemented.** The repository contains a functioning subset with bounded verification, not a certification of equivalence. The complete target is cataloged separately in [feature-inventory.md](feature-inventory.md).

## Runtime surface

| Area | Implemented subset | Important boundaries |
|---|---|---|
| Lexer | Newline/semicolon delimiter, `*`, `//`, `/* */`, `///`, ordinary quoted strings | Compound quoting, full Unicode identifier rules, every abbreviation and nested textual grammar are incomplete. |
| Expressions | Numeric arithmetic/comparisons/logical operators, strings, one-based subscripts, selected math/probability/string functions, scalar results, `_n`, `_N`, `_rc`, `_b[]`, `_se[]` | No arbitrary Python evaluation. Finite numeric data use pandas/NumPy, usually float64; default storage and overflow behavior are not fully Stata-compatible. Extended missing `.a`–`.z`, date functions, factor/time-series operators and many functions are absent. |
| Numeric missing | Basic `.` arithmetic and comparison ordering above finite numbers | Stata's extended missings `.a`–`.z` and all mixed/string edge cases are not implemented. DTA files containing extended missing values fail explicitly on read. The *display* of the overflow ladder is implemented (see [formats.md](formats.md)). |
| Numeric display | `%w.dg`/`%w.df`/`%w.de`/`%w.dgc`, `%w.s`, `%tX` — byte-for-byte equal to Stata 19.5 on 385 815 measured renderings plus 7 160 fresh probes | The `c` variant of `%e` and the `%x`/`%o` integer formats are not implemented. The model is documented in [formats.md](formats.md). |
| Storage types | `byte`/`int`/`long`/`float`/`double`/`strN`, Stata's default display format per type, out-of-range integers becoming missing | `strL` is not implemented. `recast` changes the storage type but keeps the display format, as in Stata. |
| Macros/control | Ordinary locals/globals, simple nested expansion, brace `forvalues`/`foreach`/`while`/`if`, `args`, programs, do/run/include, capture/quietly/noisily | No full `syntax`, `marksample`, s-class, `else`, general prefix blocks, `continue`, compiled ado or plugin ABI. `version` accepts supported numeric directives but does not emulate historical releases. |
| Results | Scalars, basic `r()` and `e()`, coefficient/SE lookup, `e(b)`/`e(V)` matrices, in-memory estimates store/restore | No coefficient stripes, complete e/r result sets, constraints/estimation posting or full replay semantics. |
| Data | generate/replace, bounded egen, keep/drop, rename/order, sort/gsort, by/bysort, summarize/count/describe/list/tabulate/correlate, assert | No frames, extended duplicates/reshape/recode commands, weights, fuzzy variable abbreviations or complete storage/format semantics. Large output is bounded. `describe` matches Stata's layout byte-for-byte. |
| Dataset I/O | ReadStat `.dta`, pandas DTA114/117/118/119 writes, CSV and `.xlsx`, labels, validated append/merge, numeric reshape, collapse, preserve/restore | DTA118 labels/values tested; all other DTA versions/features remain unverified. Notes, characteristics, sort state, arbitrary formats, strL binary data and lossless metadata roundtrips are incomplete. Excel support is library based and not yet compared with Stata. |
| Statistics | regress, logit/logistic, probit, poisson, predict, basic test/ttest, estimates store/restore | OLS, HC1/clustered covariance and stored logistic results have Stata numerical comparisons. Other estimates require broader verification. Factor variables, interactions, weights, collinear-variable omission, panel/TS/survival/survey/MI/Bayes/SEM/lasso/HDFE and most postestimation are absent. Rank-deficient designs are rejected. |
| Matrices | Numeric literal/assignment, list/drop, multiply/transpose/identity and selected functions | No full Stata matrix language, names/stripes, or all scalar/matrix coercion rules. |
| Mata | Numeric matrices, assignment, basic algebra, transpose, I/J/rows/cols/sum and selected functions, st_matrix/st_numscalar, all-row numeric st_data | No typed declarations/functions, pointers/classes/structures/views, compiled libraries, complex/string matrices, complete optimization/probability/file API or colon operators. |
| Graphs | scatter, line, histogram, single twoway plot, titles, PNG/SVG/PDF export with bounded dimensions | Original OpenStat appearance. No pixel equality, full schemes/options, multi-plot composition, graph editor or GPH format. PNG test checks format/width only. |
| Packages | ssc install/dir, net install from(URL), which, sysdir set PLUS, compatible ado autoload, hash manifests | Descriptor/file acquisition only. `distinct` installation is tested separately from execution. Most SSC packages will fail on missing primitives. Compiled/plugin payloads are rejected. No package-wide execution guarantee. |
| Desktop | Familiar menu families, Results/history/command, editor, variables/properties, data viewer/editor, graph preview | Original controls/branding; not an exact clone of every Stata dialog, shortcut, accessibility behavior or preference. Windows native interaction and offscreen Qt checks; other OSes unverified. |

Only explicitly parsed commands/options are accepted. Missing features should fail, although a subset interpreter can still have undiscovered semantic differences in accepted syntax. A successful return code is not a guarantee of Stata-equivalent results. Chained comparisons are rejected; use explicit comparisons joined with `&`. Matrix operations enforce the implemented dimension rules; singular `invsym()` conventions are not implemented and fail explicitly. Numerical models use independent library implementations; optimizer, convergence, missing-data, covariance and sample-selection differences need per-case verification. OpenStat's RNG **does** match Stata's: it is Stata's MT64 variant with the ziggurat normal, verified draw-for-draw against Stata 19.5 (see [random-numbers.md](random-numbers.md)).

### `r()`, `e()` and `c()`

These are measured against Stata 19.5 and follow it exactly:

- In **macro position** (`` `r(mean)' ``) the expansion is *text*: the value
  rendered with `%18.0g` and the padding removed. This is confirmed in
  Stata's own binary, where the format string is named `_fmt_18p0g` and is
  used by `_retval_get_as_string`, `_macsub`, `_macgen` and `_macincr`.
- In **expression position** (`r(mean)`) the value is a number and is shown
  with `%10.0g`.
- An **unknown** name expands to the empty string in macro position. In
  expression position an unknown `r()`/`e()` is the *missing value*, and an
  unknown `c()` is the error `c(name) undefined` (rc 133).
- The `c()` table holds only the names Stata 19.5 defines. `c(SCS)`,
  `c(n_processors)`, `c(endian)`, `c(release)`, `c(cmd)`, `c(S_level)`,
  `c(nobs)`, `c(node)`, `c(ado_version)` and similar are undefined in
  Stata 19 and are undefined here too.
- `local x = <expr>` and `global x = <expr>` store the same `%18.0g` text,
  so a macro keeps full precision even though its display rounds.


## Execution and file behavior

Commands execute in order and mutate the current dataset. Runtime errors stop subsequent commands; earlier changes persist. A 100,000-statement step budget and 60-level execution nesting budget bound loops. Stop is cooperative between statements; a running numerical/library call or network timeout must finish before cancellation takes effect. GUI edits are disabled while the worker owns the engine.

Batch working directory defaults to the caller's current directory. `do`/`run` use a fresh local-macro scope; `include` shares scope. Files are ordinary local files; explicit `replace` overwrites supported export targets. Do-files and ado programs can read/write within the account's filesystem permissions. There is no script sandbox. Python AST attributes/imports/arbitrary calls are unavailable in the expression interpreter.

`set more`, `set linesize`, `set rmsg` and `set type float|double` are accepted directives; paging/timing output is not replicated. `set type` changes the storage type that an untyped `generate` creates — Stata's default is `float`. Temporary-file paths and package files are separate from the public source tree.

## Package transport and licensing

SSC's canonical `http://fmwww.bc.edu/repec/bocode/` endpoint is used because HTTPS did not negotiate successfully on this host at verification time. HTTP downloads are not transport-authenticated. The installer records source URLs and SHA-256 hashes for provenance; a newly recorded hash does not authenticate a remote file. Explicit `net install ..., from(https://...)` uses normal certificate validation. Installation downloads files without executing them; later ado execution still uses this runtime's supported subset.

The package manager validates names and descriptor paths, bounds downloads, stages complete acquisitions before replacement, and refuses native/compiled executables. Package files retain their upstream licenses and are not covered by OpenStat's MIT license. No third-party package contents are checked into this repository.

## Evidence and coverage

[qa/report.json](../qa/report.json) is the machine-readable result of the checked-in deterministic examples. Tolerances: absolute 1e-9, relative 1e-8. Checks include summary scalars/missing ordering; loops/macros/programs/capture; OLS coefficients, SEs, N, residual degrees of freedom, R²; DTA118 values/labels; preserve/restore; merge/append/reshape; a Mata product; PNG width; unknown-command/option errors; optional SSC download equality. This is sample coverage, not exhaustive command coverage.

The oracle is the installed **StataNow/SE 19.5** at `/Applications/StataNow`, not a verified copy of the latest October 2026 revision. Current feature documentation was consulted for target inventory, not used as evidence of implemented parity. The [REA investigation](inspection.md) records resource evidence, hashes, native-provider limits and remaining unknowns. OpenStat runs independently of the oracle; Stata is used only by the optional QA harness.

Differential coverage at the time of writing: **9/9** `qa/conformance` cases pass against Stata 19.5 (`qa/conformance_run.py --all --diff`), **2/2** published replication packages pass (`qa/replication_run.py`), and `pytest -q` reports **89 passed, 90 subtests passed**. The `%w.dg` display model additionally matches Stata on a 385 815-measurement corpus and 7 160 targeted probes; the RNG matches draw-for-draw; `describe` matches byte-for-byte. These are the *measured* claims this repository makes — anything not covered by one of those harnesses is unverified and should be described as such.

