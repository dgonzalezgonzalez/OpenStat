# OpenStat 0.1 compatibility contract

OpenStat is an independent experimental runtime. **Arbitrary do-file, current StataNow, full Mata and general SSC package compatibility are not implemented.** The repository contains a functioning subset with bounded verification, not a certification of equivalence. The complete target is cataloged separately in [feature-inventory.md](feature-inventory.md).

## Runtime surface

| Area | Implemented subset | Important boundaries |
|---|---|---|
| Lexer | Newline/semicolon delimiter, `*`, `//`, `/* */` (nested), `///`, ordinary and compound (`\`"…"'`) quoting, `#delimit ;`/`#delimit cr`, inline `{ … }` block splitting | Not every abbreviation and nested textual grammar is implemented; `#delimit` accepts only `;` and `cr`. |
| Expressions | Numeric arithmetic/comparisons/logical operators, strings, one-based subscripts, selected math/probability/string functions, scalar results, `_n`, `_N`, `_rc`, `_b[]`, `_se[]` | No arbitrary Python evaluation. Finite numeric data use pandas/NumPy, usually float64; default storage and overflow behavior are not fully Stata-compatible. Extended missing `.a`–`.z`, date functions, factor/time-series operators and many functions are absent. |
| Numeric missing | Basic `.` arithmetic and comparison ordering above finite numbers | Stata's extended missings `.a`–`.z` and all mixed/string edge cases are not implemented. DTA files containing extended missing values fail explicitly on read. The *display* of the overflow ladder is implemented (see [formats.md](formats.md)). |
| Numeric display | `%w.dg`/`%w.df`/`%w.de`/`%w.dgc`, `%w.s`, `%tX` — byte-for-byte equal to Stata 19.5 on 385 815 measured renderings plus 7 160 fresh probes | The `c` variant of `%e` and the `%x`/`%o` integer formats are not implemented. The model is documented in [formats.md](formats.md). |
| Storage types | `byte`/`int`/`long`/`float`/`double`/`strN`, Stata's default display format per type, out-of-range integers becoming missing | `strL` is not implemented. `recast` changes the storage type but keeps the display format, as in Stata. |
| Macros/control | Ordinary locals/globals, compound quoting, nested and extended expansion (`: word count`, `: type`, `: subinstr`, …), brace `forvalues`/`foreach`/`while`/`if`/`else`, `continue`/`break`/`continue, break`, `exit [, clear]`, `error #`, `program` with `rclass`/`byable`/`sortpreserve`, `syntax` (varlist/varname/anything/if/in/using/`=exp`/weight elements; typed options with unambiguous abbreviations), `marksample`/`mark`/`markout`, `tempvar`/`tempname`/`tempfile`, `args`, `gettoken`, do/run/include, capture/quietly/noisily | No s-class, no compiled ado or plugin ABI; `version` accepts numeric directives but does not emulate historical releases; `statsby`, resampling and survey prefixes are absent. `marksample` follows the measured marker rules (weights, missing values, string variables) but `by`-sample handling only covers the `byable` cases in `tests/test_programming.py`. |
| Results | Scalars, `r()`/`e()` with measured macro- and expression-position rendering, coefficient/SE lookup, `e(b)`/`e(V)` matrices, `e(N)`/`e(df_r)`/`e(df_a)`/`e(N_clust)` and the fitting statistics, in-memory estimates store/restore | No coefficient stripes, constraints or full replay semantics; result sets are the measured subset, not every `ereturn`. |
| Data | generate/replace, bounded egen, keep/drop, rename/order, sort/gsort, by/bysort, summarize/count/describe/codebook/inspect/list/tabulate/correlate/tabstat/mean/total/centile, assert, duplicates, isid, encode/decode, destring/tostring, recode, contract, expand, cross/joinby, `varlist` ranges/wildcards and factor variables (`i.`, `c.`, `##`, base levels) in model specifications | No frames, no time-series operators outside a bounded `L.`/`F.`/`D.`/`S.` set, no `reshape` beyond numeric long/wide, no fuzzy abbreviations. Large output is bounded. `describe` matches Stata's layout byte-for-byte. |
| Dataset I/O | ReadStat `.dta`, pandas DTA114/117/118/119 writes, CSV and `.xlsx`, labels, validated append/merge, numeric reshape, collapse, preserve/restore, extended-missing rejection on load | DTA118 labels/values tested; other DTA versions/features remain unverified. Notes, characteristics, sort state, arbitrary formats, strL binary data and lossless metadata roundtrips are incomplete. Excel support is library based and not yet compared with Stata. |
| Statistics | `regress`, `areg`, `reghdfe` (multi-way absorb, `keepsingletons`, `e(df_a)` semantics), `xtreg`, `ivregress 2sls`, `logit`/`logistic`/`probit`/`poisson`, `predict`, `test`, `ttest`, `margins`, weights (`fweight`/`aweight`/`pweight`), robust/cluster VCE, estimates store/restore | Bounded numerical comparisons for OLS/robust/cluster, HDFE, 2SLS and stored logistic results (`qa/cases/*.do`). Other estimators, multiway clustering beyond the single-cluster case, `svy`/`mi`/`bayes`/`sem`/lasso and most postestimation are absent. A saturated fit (`n == k`) reports coefficients with missing inference, as Stata does; `n < k` is `r(2001)`. |
| Matrices | Numeric literal/assignment, list/drop, multiply/transpose/identity and selected functions | No full Stata matrix language, names/stripes, or all scalar/matrix coercion rules. |
| Mata | Numeric matrices, assignment, basic algebra, transpose, I/J/rows/cols/sum, `if`/`for`/`while`, user functions, `st_matrix`/`st_numscalar`, all-row numeric `st_data` | No typed declarations, pointers/classes/structures/views, compiled libraries, complex/string matrices, complete optimization/probability/file API or colon operators. |
| Graphs | scatter, line, histogram, single twoway plot, titles, PNG/SVG/PDF export with bounded dimensions | Original OpenStat appearance. No pixel equality, full schemes/options, multi-plot composition, graph editor or GPH format. PNG test checks format/width only. |
| Output | `esttab`/`estout`-style table rendering, `putdocx`/`putpdf`-free HTML/Markdown/LaTeX table output through `openstat/output.py` | Bounded table layouts; no Stata document formats, collections or `etable`. |
| Packages | `ssc describe/install/dir`, `net install` from(a URL or local directory), `all`, `from()`, `which`, `sysdir set PLUS`, `adopath ++/+/-`, compatible ado autoload, hash manifests | Descriptor/file acquisition only. `distinct` is the one package verified to install *and* execute. Most SSC packages will fail on missing primitives. Compiled/plugin payloads are rejected. No package-wide execution guarantee. |
| Desktop | Familiar menu families, Results/history/command, editor, variables/properties, data viewer/editor, graph preview | Original controls/branding; not an exact clone of every Stata dialog, shortcut, accessibility behaviour or preference. The measured desktop gap list is in [ui-parity.md](ui-parity.md): docking, Review table, Viewer, Preferences and option-bound dialogs are recorded as missing. |

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

The oracle is an installed **StataNow/SE 19.5** (`/Applications/StataNow` on
macOS, `C:\Program Files\StataNow19\StataMP-64.exe` on the Windows host used
for the measurements above), not a verified copy of the latest October 2026
revision. Current feature documentation was consulted for target inventory, not
used as evidence of implemented parity. The [REA investigation](inspection.md)
records resource evidence, hashes, native-provider limits and remaining
unknowns. OpenStat runs independently of the oracle; Stata is used only by the
optional QA harness.

Differential coverage at the time of writing (23 October 2026, measured on this
Windows host): **10/10** `qa/conformance` cases pass against Stata 19.5
(`qa/conformance_run.py --all`), **20 of 21** checked-in differential cases pass
(`qa/run_differential.py`; the 21st is the optional SSC download, skipped when
no local package copy is supplied), **2/2** published replication packages pass
(`qa/replication_run.py`: `bdmmm_jpe_randomdata` and
`dropout_jpe_descriptives`, both AEA/JPE), and `pytest -q` reports **252
passed, 4 skipped, 90 subtests passed**. The `%w.dg` display model matches
Stata on a 385 815-measurement corpus and 7 160 targeted probes; the RNG
matches draw-for-draw; `describe` matches byte-for-byte. These are the
*measured* claims this repository makes — anything not covered by one of those
harnesses is unverified and should be described as such.

Replication also exposes a **performance** gap that is not a correctness gap:
`dropout_jpe_descriptives` takes 1.6 s in Stata and about 145 s in OpenStat.
The harness records both times, and a slow case is never reported as a pass on
the strength of its return code alone.

`qa/gap_report.py` rolls the three suites into one ranked queue
(`qa/gap-report.md`, `qa/gap-report.json`). It compares each suite against the
recorded baseline and attributes a failing case to the module that owns it, so
a regression is named rather than discovered later.

### Measured facts that are easy to get wrong

- **A saturated regression fits.** Stata 19.5 returns rc 0 for `regress y i.x`
  with four observations and four coefficients: `e(df_r) = 0`, `b[_cons] = 1`,
  every standard error missing. OpenStat matches. It is `n < k`, not `n <= k`,
  that is `r(2001)` "insufficient residual degrees of freedom".
- **`reghdfe`'s `e(df_a)` is not a residual degree of freedom.** It is the
  absorbed rank (two-way absorb of 20 `unit` levels and 6 `time` levels gives
  25), minus the parameters of any absorbed effect nested within the clustering
  variable (clustering on a variable that nests `unit` leaves 5). The *scale*
  still divides by `N - k - rank + 1`, so a fully nested specification has
  `e(df_a) = 0` while `e(rmse)` keeps its usual divisor. Both were measured on
  19.5 and are asserted by `qa/cases/hdfe.do`.
- **`syntax` option abbreviations are unambiguous prefixes**, and `` `0' `` is
  the whole argument string including the options — `syntax` parses `` `0' ``,
  so a program that dropped the comma part would silently fall back to every
  option's default. Numeric local names (`` `1' ``…`` `9' ``) are programmer
  arguments and must expand.

