# OpenStat 0.1 compatibility contract

OpenStat is an independent experimental runtime. **Arbitrary do-file, current StataNow, full Mata and general SSC package compatibility are not implemented.** The repository contains a functioning subset with bounded verification, not a certification of equivalence. The complete target is cataloged separately in [feature-inventory.md](feature-inventory.md).

## Runtime surface

| Area | Implemented subset | Important boundaries |
|---|---|---|
| Lexer | Newline/semicolon delimiter, `*`, `//`, `/* */`, `///`, ordinary quoted strings | Compound quoting, full Unicode identifier rules, every abbreviation and nested textual grammar are incomplete. |
| Expressions | Numeric arithmetic/comparisons/logical operators, strings, one-based subscripts, selected math/probability/string functions, scalar results, `_n`, `_N`, `_rc`, `_b[]`, `_se[]` | No arbitrary Python evaluation. Finite numeric data use pandas/NumPy, usually float64; default storage and overflow behavior are not fully Stata-compatible. Extended missing `.a`–`.z`, date functions, factor/time-series operators and many functions are absent. |
| Numeric missing | Basic `.` arithmetic and comparison ordering above finite numbers | Stata's extended missings and all mixed/string edge cases are not implemented. DTA files containing extended missing values fail explicitly on read. |
| Macros/control | Ordinary locals/globals, simple nested expansion, brace `forvalues`/`foreach`/`while`/`if`, `args`, programs, do/run/include, capture/quietly/noisily | No full `syntax`, `marksample`, s-class, `else`, general prefix blocks, `continue`, compiled ado or plugin ABI. `version` accepts supported numeric directives but does not emulate historical releases. |
| Results | Scalars, basic `r()` and `e()`, coefficient/SE lookup, `e(b)`/`e(V)` matrices, in-memory estimates store/restore | No coefficient stripes, complete e/r result sets, constraints/estimation posting or full replay semantics. |
| Data | generate/replace, bounded egen, keep/drop, rename/order, sort/gsort, by/bysort, summarize/count/describe/list/tabulate/correlate, assert | No frames, extended duplicates/reshape/recode commands, weights, fuzzy variable abbreviations or complete storage/format semantics. Large output is bounded. |
| Dataset I/O | ReadStat `.dta`, pandas DTA114/117/118/119 writes, CSV and `.xlsx`, labels, validated append/merge, numeric reshape, collapse, preserve/restore | DTA118 labels/values tested; all other DTA versions/features remain unverified. Notes, characteristics, sort state, arbitrary formats, strL binary data and lossless metadata roundtrips are incomplete. Excel support is library based and not yet compared with Stata. |
| Statistics | regress, logit/logistic, probit, poisson, predict, basic test/ttest, estimates store/restore | OLS, HC1/clustered covariance and stored logistic results have Stata numerical comparisons. Other estimates require broader verification. Factor variables, interactions, weights, collinear-variable omission, panel/TS/survival/survey/MI/Bayes/SEM/lasso/HDFE and most postestimation are absent. Rank-deficient designs are rejected. |
| Matrices | Numeric literal/assignment, list/drop, multiply/transpose/identity and selected functions | No full Stata matrix language, names/stripes, or all scalar/matrix coercion rules. |
| Mata | Numeric matrices, assignment, basic algebra, transpose, I/J/rows/cols/sum and selected functions, st_matrix/st_numscalar, all-row numeric st_data | No typed declarations/functions, pointers/classes/structures/views, compiled libraries, complex/string matrices, complete optimization/probability/file API or colon operators. |
| Graphs | scatter, line, histogram, single twoway plot, titles, PNG/SVG/PDF export with bounded dimensions | Original OpenStat appearance. No pixel equality, full schemes/options, multi-plot composition, graph editor or GPH format. PNG test checks format/width only. |
| Packages | ssc install/dir, net install from(URL), which, sysdir set PLUS, compatible ado autoload, hash manifests | Descriptor/file acquisition only. `distinct` installation is tested separately from execution. Most SSC packages will fail on missing primitives. Compiled/plugin payloads are rejected. No package-wide execution guarantee. |
| Desktop | Familiar menu families, Results/history/command, editor, variables/properties, data viewer/editor, graph preview | Original controls/branding; not an exact clone of every Stata dialog, shortcut, accessibility behavior or preference. Windows native interaction and offscreen Qt checks; other OSes unverified. |

Only explicitly parsed commands/options are accepted. Missing features should fail, although a subset interpreter can still have undiscovered semantic differences in accepted syntax. A successful return code is not a guarantee of Stata-equivalent results. Chained comparisons are rejected; use explicit comparisons joined with `&`. Matrix operations enforce the implemented dimension rules; singular `invsym()` conventions are not implemented and fail explicitly. Numerical models use independent library implementations; optimizer, convergence, missing-data, covariance and sample-selection differences need per-case verification. OpenStat's RNG is reproducible within its own implementation and does not match Stata's RNG.

## Execution and file behavior

Commands execute in order and mutate the current dataset. Runtime errors stop subsequent commands; earlier changes persist. A 100,000-statement step budget and 60-level execution nesting budget bound loops. Stop is cooperative between statements; a running numerical/library call or network timeout must finish before cancellation takes effect. GUI edits are disabled while the worker owns the engine.

Batch working directory defaults to the caller's current directory. `do`/`run` use a fresh local-macro scope; `include` shares scope. Files are ordinary local files; explicit `replace` overwrites supported export targets. Do-files and ado programs can read/write within the account's filesystem permissions. There is no script sandbox. Python AST attributes/imports/arbitrary calls are unavailable in the expression interpreter.

`set more`, `set linesize`, and `set rmsg` are accepted presentation directives; paging/timing output is not replicated. Temporary-file paths and package files are separate from the public source tree.

## Package transport and licensing

SSC's canonical `http://fmwww.bc.edu/repec/bocode/` endpoint is used because HTTPS did not negotiate successfully on this host at verification time. HTTP downloads are not transport-authenticated. The installer records source URLs and SHA-256 hashes for provenance; a newly recorded hash does not authenticate a remote file. Explicit `net install ..., from(https://...)` uses normal certificate validation. Installation downloads files without executing them; later ado execution still uses this runtime's supported subset.

The package manager validates names and descriptor paths, bounds downloads, stages complete acquisitions before replacement, and refuses native/compiled executables. Package files retain their upstream licenses and are not covered by OpenStat's MIT license. No third-party package contents are checked into this repository.

## Evidence and coverage

[qa/report.json](../qa/report.json) is the machine-readable result of the checked-in deterministic examples. Tolerances: absolute 1e-9, relative 1e-8. Checks include summary scalars/missing ordering; loops/macros/programs/capture; OLS coefficients, SEs, N, residual degrees of freedom, R²; DTA118 values/labels; preserve/restore; merge/append/reshape; a Mata product; PNG width; unknown-command/option errors; optional SSC download equality. This is sample coverage, not exhaustive command coverage.

The oracle is the installed **StataNow/MP 19.5**, not a verified copy of the latest October 2026 revision. Current feature documentation was consulted for target inventory, not used as evidence of implemented parity. The [REA investigation](inspection.md) records resource evidence, hashes, native-provider limits and remaining unknowns. OpenStat runs independently of the oracle; Stata is used only by the optional QA harness.
