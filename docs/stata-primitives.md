# Stata primitives — shared vocabulary contract

This document names the Stata syntax primitives that more than one command
family shares, states each one's interface in our own words, and records what
OpenStat does with it. Test-case authors, conformance cases and reviewers cite
a primitive by its name from this catalogue instead of re-deriving "what `by`
means" from a different help page each time.

- Catalogue: `qa/stata_primitives.json` (96 primitives, 15 areas).
- Loader / CLI / validator: `qa/stata_primitives.py`.
- Reference runtime: **StataNow 19.5** on this host. Interface facts were read
  from the installed help corpus at `C:\Program Files\StataNow19\ado\base`
  (`.sthlp`) and from `qa/stata_targets.json`; behaviour was measured with
  `qa/stata_run.py`. Measurement date: 2026-10-22.
- Only interface facts are recorded — primitive names, grammar shapes, keyword
  and option names, observed behaviour. No StataCorp prose, tables or examples
  are reproduced.

## Status vocabulary

| Status | Meaning |
|---|---|
| `implemented` | Present, and no measured case diverges. |
| `partial` | Present, but a specific named case diverges; the divergence is in `openstat_note` and under "Gaps". |
| `absent` | Not implemented; the runtime must fail explicitly (rc 199 or the fitting Stata code) rather than guess. |
| `unknown` | Not yet established. No record uses it today. |

Kinds are the fixed eight: `qualifier`, `weight`, `operator`, `type`,
`result-class`, `prefix`, `option-group`, `command-group` (the last covers both
command families and standalone commands such as `program` or `mark`).

## Querying the catalogue

```sh
python qa/stata_primitives.py --summary            # per-area and per-status counts
python qa/stata_primitives.py --check              # schema + every source path resolved
python qa/stata_primitives.py --primitive ts_S     # one full record
python qa/stata_primitives.py --area factor-variables
python qa/stata_primitives.py --status partial --json
```

Python API: `primitives()`, `primitive(name)`, `openstat_status(name)`.
`--check` fails on a malformed record *or* on a `sources` entry that does not
resolve on this host (a `sthlp:` path under the Stata help root, or a `targets:`
subject key in `qa/stata_targets.json`). Use `--stata-root` / `STATA_BASE` when
the corpus lives elsewhere.

---

## 1. Qualifiers — if / in

Observation selection shared by every command that takes a varlist.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `if_qualifier` | qualifier | implemented | `[varlist] if exp`; `exp` evaluated over all observations, then intersected with `in`. |
| `in_qualifier` | qualifier | partial | `[varlist] in range`, `range = # \| #/# \| #/l \| f/#`; `f` first, `l` last, negatives count back. |
| `if_in_combination` | qualifier | implemented | Both may appear; the effective sample is the intersection. |

Gaps:

- **in_qualifier** — observation `0` is accepted and yields an empty selection. Stata 19.5 rejects `count in 0/2` with rc 198 `'0' invalid observation number`; OpenStat returns rc 0.

## 2. by — by / bysort

Repeat a command over groups of observations.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `by_prefix` | qualifier | partial | `by varlist: command`; requires pre-sorted data. |
| `bysort_prefix` | qualifier | partial | `bysort varlist: command`; stable sort first, then per-group runs. |
| `by_sort_override` | qualifier | partial | `by g (s): command` — the parenthesised varlist sets processing order, not grouping. |
| `by_unsorted_error` | qualifier | implemented | Plain `by` on unsorted data fails with rc 5. |

Gaps:

- **by_prefix**, **bysort_prefix** — the body is restricted to an allow-list (`egen`, `generate`, `replace`, `summarize`, `count`, `assert`, `tabulate`, `list`, `total`, `mean`). `by g: regress y z` is rc 0 in Stata and rc 199 here; the block form `by g: { ... }` is not recognised.
- **by_sort_override** — OpenStat sorts by `g` and `s` together rather than by `s` within `g`, so group order can differ when the two conflict. Unsettled; wants a conformance case with two group variables.

## 3. Weights — aw / fw / pw / iw

The bracketed weight specification and the four weight types.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `weight_bracket` | weight | partial | `[weighttype=exp]`, after the varlist and before the comma. |
| `weight_aweight` | weight | implemented | `[aw=exp]`; inverse-variance information in the variance formula. |
| `weight_fweight` | weight | implemented | `[fw=exp]`; each row stands for `exp` observations, `N` is the weight sum. |
| `weight_pweight` | weight | implemented | `[pw=exp]`; inverse probability of selection. |
| `weight_iweight` | weight | absent | `[iw=exp]`; importance weights, accepted by few commands. |

Gaps:

- **weight_bracket** — parsed in the model-specification path (`openstat/design.py`) only, not as a command-level grammar. In descriptive commands the bracket is read as a varlist: `summarize y [fw=w]` summarizes `w` as a second variable and reports `r(N)=6`, where Stata reports `r(N)=12` (the weight sum).
- **weight_fweight** — applied in estimation, but `summarize` keeps the row count instead of the weight sum (same measurement).
- **weight_aweight**, **weight_pweight** — applied and produce weighted standard errors, but no point estimate has been compared numerically with Stata yet.
- **weight_iweight** — recognised by the parser, then refused with `importance weights not yet implemented` (rc 199).

## 4. Factor variables

Indicator (factor), continuous, base-level and interaction terms.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `fv_i` | operator | partial | `i.var`; one indicator per level, base level omitted. |
| `fv_c` | operator | implemented | `c.var`; keeps the variable continuous inside a term. |
| `fv_ib_base` | operator | implemented | `ib#.var \| b#.var \| ib(first).var \| ib(last).var`. |
| `fv_ibn` | operator | implemented | `ibn.var`; every level, no base. |
| `fv_ib_freq` | operator | absent | `ib(freq).var`; most frequent level as base. |
| `fv_o` | operator | absent | `o.var \| o(numlist).var`; levels to omit. |
| `fv_hash` | operator | implemented | `a#b[#c...]`; interaction only. |
| `fv_doublehash` | operator | implemented | `a##b`; expands to `a`, `b`, `a#b`. |
| `fv_numlist` | operator | absent | `i(numlist).var \| ib(#n).var`; level selection by list or rank. |
| `fv_omitted_coefficient` | operator | implemented | The base level appears in `e(b)` as `<level>b.name = 0`. |

Gaps:

- **fv_i** — estimation commands only (`regress`, `logit`, `ivregress`, `xtreg`, …). `summarize i.g` is rc 0 in Stata and rc 111 here, because the parser hands `i.g` to the varlist resolver.
- **fv_ib_freq**, **fv_o**, **fv_numlist** — no parser branch; `o2.g`, `ib(freq).g`, `i(1/2).g` and `ib(#2).g` all fail with rc 111 (`variable ... not found`). Stata accepts all four.

## 5. Time-series operators

`L.` `F.` `D.` `S.` plus the numlist and repeated-operator forms.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `ts_L` | operator | implemented | `L.var \| L#.var \| L0.var`; lag, gaps stay missing. |
| `ts_F` | operator | implemented | `F.var \| F#.var`; lead. |
| `ts_D` | operator | implemented | `D.var \| D#.var`; `var_t - var_(t-#)`. |
| `ts_S` | operator | partial | `S.var \| S#.var`; seasonal difference; `S1` = `D1`, `S2` ≠ `D2`. |
| `ts_numlist` | operator | absent | `L(numlist).var \| D(1/2).var`; a set of operated variables. |
| `ts_repeat` | operator | absent | `LLL.var \| L2L.var`; repeated or combined operators, canonicalised. |
| `ts_requires_tsset` | operator | implemented | Any operator without a declared time variable stops. |

Gaps:

- **ts_S** — re-applies the one-period difference `#` times, so it computes `D#`. Measured on `y = 2*_n`: `S2.y` is 4 in Stata (`y5 - y3`) and 0 here.
- **ts_numlist** — only `operator#.var` is matched. `list y L(2).y` is rc 0 in Stata and rc 111 here. (In expression position Stata itself rejects `L(2).y` with rc 133 `unknown function L()`, so the varlist case is the one that matters.)
- **ts_repeat** — one operator letter per term; `list y LLL.y` is rc 111 here.

## 6. Storage types

`byte` / `int` / `long` / `float` / `double` / `str#` / `strL`.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `type_byte` | type | implemented | `-127..100`, default `%8.0g`; out-of-range → missing. |
| `type_int` | type | implemented | `-32767..32740`, default `%8.0g`. |
| `type_long` | type | implemented | `-2147483647..2147483620`, default `%12.0g`. |
| `type_float` | type | implemented | Default `%9.0g`; what an untyped `generate` creates. |
| `type_double` | type | implemented | Default `%10.0g`. |
| `type_str` | type | implemented | `str#`, `2 <= # <= 2045`; longer values truncated. |
| `type_strL` | type | partial | Values beyond 2045 bytes; no declared width. |
| `type_recast` | type | implemented | `recast type varlist [, force]`; rc 459 without `force`. |

Gaps:

- **type_strL** — the token is accepted and `describe` says `strL`, but the column is an ordinary object column: no 2045-byte boundary and no distinct read/write path.

## 7. Display formats

The `%fmt` grammar and the rendering rules behind it.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `fmt_g` | type | implemented | `%w.dg`; shortest exact form, Stata's fixed/scientific switch. |
| `fmt_f` | type | partial | `%w.df`; fixed point with `d` decimals. |
| `fmt_e` | type | partial | `%w.de`; scientific, two-digit signed exponent. |
| `fmt_s` | type | implemented | `%w.ds`; right justified, truncating or padding. |
| `fmt_c` | type | implemented | `%w.dfc`, `%w.dgc`; thousands separators. |
| `fmt_date` | type | absent | `%td… %tc… %tw %tm %tq %th %ty %tb`. |
| `fmt_widen` | type | implemented | A value that does not fit is widened, never truncated. |

Gaps:

- **fmt_f** — padding is spaces only; the zero-fill flag in the width is ignored. `display %06.2f 3.5` gives `003.50` in Stata and `3.50` here.
- **fmt_e** — the exponent is not zero-padded to two digits and trailing zeros are stripped. `%12.4e 31415.9265` gives `  3.1416e+04` in Stata and `3.142e+4` here.
- **fmt_date** — the date-format regex is known but there is no rendering branch: `display %td 20000` is rc 0 in Stata and rc 198 here, and a `%td` variable prints its raw number.

The `%g` model itself is complete and matched byte-for-byte; read
[`docs/formats.md`](formats.md) before touching it.

## 8. Stored results — r() / e() / s() / c()

Result classes and the macro-versus-expression distinction.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `rclass` | result-class | implemented | `r(N)`, `r(mean)`, `r(sum_w)`, … from general commands. |
| `eclass` | result-class | implemented | `e(N)`, `e(b)`, `e(V)`, `e(ll)`, … from estimation. |
| `sclass` | result-class | absent | `s(name)` from parsing commands, listed by `sreturn list`. |
| `cclass` | result-class | implemented | `c(N)`, `c(k)`, `c(pi)`, `c(version)`, `c(pwd)`, `c(rc)`, … |
| `macro_position` | result-class | implemented | `` `r(x)' `` → `%18.0g` text, padding stripped. |
| `expression_position` | result-class | implemented | `r(x)` → number, `%10.0g`; unset `r()`/`e()` is missing. |
| `result_listing` | result-class | partial | `return list`, `ereturn list`, `sreturn list`. |

Gaps:

- **sclass** — `macros.py` resolves `r`/`e`/`c` only; `display s(nosuch)` is rc 111 here (Stata: missing value, rc 0) and `sreturn list` is rc 199.
- **result_listing** — prints a Python dict repr instead of Stata's aligned list; `sreturn list` unsupported.
- **eclass** — `return scalar` inside an `eclass` program still lands in `r()`; Stata routes it to `e()`.
- **cclass**, **macro_position** — the class table is exactly what Stata 19.5 defines, but `c(seed)`, `c(adopath)` and `c(mode)` diverge (see `AGENTS.md`), and an unset *local*/*global* expands to the literal `` `name' `` instead of the empty string.

## 9. Command prefixes

`capture`, `quietly`, `noisily`, `version`, `statsby`, `svy`, `mi`, `bayes`.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `prefix_capture` | prefix | partial | `capture: cmd`; swallows the error, leaves `_rc`. |
| `prefix_quietly` | prefix | partial | `quietly: cmd`; output suppressed, stored results kept. |
| `prefix_noisily` | prefix | implemented | `noisily: cmd`; re-enables output inside a quiet context. |
| `prefix_version` | prefix | implemented | `version #: cmd`; declares the interpretation version. |
| `prefix_statsby` | prefix | absent | `statsby [namelist] [, opts]: cmd`; per-group results into a dataset. |
| `prefix_svy` | prefix | absent | `svy [, opts]: cmd`; uses the `svyset` design. |
| `prefix_mi` | prefix | absent | `mi estimate [, opts]: cmd`; multiply-imputed results. |
| `prefix_bayes` | prefix | absent | `bayes [, opts]: cmd`; Bayesian re-estimation. |

Gaps:

- **prefix_capture**, **prefix_quietly** — the one-line forms work; the block forms `capture { ... }` and `quietly { ... }` are rc 199 (`command { not implemented`) where Stata returns rc 0.
- **prefix_statsby**, **prefix_svy**, **prefix_mi**, **prefix_bayes** — no branches; all rc 199. Stata recognises each prefix (rc 4 for `statsby` here, rc 119 `data not set up for svy` / `data not mi set`, rc 0 for `bayes`), so these are prefix-recognition gaps, not only estimator gaps.
- **prefix_version** — accepts any value up to 19.5 and rejects higher with rc 9, but does not switch per-version behaviour.

## 10. syntax grammar

The `syntax` command and its element and option descriptors.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `syntax_command` | command-group | implemented | `syntax spec [, options]`; binds each matched piece to a local. |
| `syntax_varlist_element` | option-group | implemented | `[varlist] \| varname \| newvarlist \| newvarname \| namelist`. |
| `syntax_anything_element` | option-group | implemented | `[anything [name=...]]`; verbatim remainder. |
| `syntax_ifin_elements` | option-group | implemented | `[if] [in]`, captured with the keyword. |
| `syntax_using_element` | option-group | partial | `[using filename] \| using/`. |
| `syntax_exp_element` | option-group | implemented | `[=exp]`, stored in `exp`. |
| `syntax_weight_element` | option-group | absent | `[aweight]`…`[weight]`; weight type and expression as locals. |
| `syntax_option_switch` | option-group | implemented | `[, NAME]` → 1 or empty. |
| `syntax_option_typed` | option-group | implemented | `[, NAME(integer #) \| NAME(real #) \| NAME(string) \| NAME(namelist) \| NAME(numlist)]`. |

Gaps:

- **syntax_using_element** — the `using/` spelling is not handled and the literal word `using` is required. `syntax using/` followed by a bare file is rc 101 `varlist not allowed` in Stata and rc 100 `using required` here.
- **syntax_weight_element** — no weight branch. `syntax varlist [weight]` is rc 197 in Stata and rc 199 `syntax descriptor not implemented: weight` here; `[aweight]` leaves `aweight`/`wexp` unset instead of binding them.

## 11. Program support — program / marksample / mark / markout

The commands a program uses to define itself and select observations.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `program_define` | command-group | partial | `program [define] name [, rclass \| eclass \| sclass \| sortpreserve \| byable(...) \| properties(...) \| plugin]` |
| `program_rclass_return` | command-group | partial | `return scalar name = exp \| return local name value`. |
| `marksample` | command-group | implemented | `marksample local [, novarlist strok zeroweight noby sysmisok]`. |
| `mark` | command-group | partial | `mark newmarkvar [if] [in] [weight] [, zeroweight noby]`. |
| `markout` | command-group | implemented | `markout markvar [varlist] [, strok sysmisok]`. |
| `tempvar_tempname_tempfile` | command-group | implemented | Hidden names from one counter; `tempfile` also yields a path. |
| `gettoken_args_tokenize` | command-group | implemented | `args`, `gettoken n1 n2 : string [, parse()]`, `tokenize`. |

Gaps:

- **program_define** — the option set matches, except `nclass` is rejected with rc 198 `option nclass not allowed` where Stata accepts it.
- **program_rclass_return** — `return` always writes `r()`; in an `eclass` program the value must go to `e()`.
- **mark** — the marker is created as a hidden `__NNNNNN` double and the local points at it. Stata creates the *named* variable as `byte %8.0g`, so `mark flag if g == 1` then `describe flag` is rc 0 in Stata and rc 111 here.
- **gettoken_args_tokenize** — `gettoken`'s `match()` option is refused (rc 199) rather than implemented.

## 12. Extended missing values

The `.` and `.a`–`.z` namespace.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `missing_dot` | type | implemented | `.`; larger than any real number, absent everywhere. |
| `missing_extended` | type | absent | `.a` … `.z`; 26 values between `.` and infinity, storable. |
| `missing_function` | operator | partial | `missing(x) \| mi(x)`; true for a missing argument. |
| `missing_ladder` | type | implemented | Overflow markers use the same `.a`–`.z` ladder. |

Gaps:

- **missing_extended** — the tokenizer has no `.a`–`.z` literal, so `display .a`, `missing(.a)` and `replace m = .a in 1` are rc 198 `unsupported expression: .a` (Stata: rc 0, prints `.a`). Datasets holding user missing values are refused on read and write, and `mvencode` is rc 199.
- **missing_function** — correct for `.` and `""`, but unreachable for the extended forms because their literals do not parse.

## 13. Mata subset

The Stata interface functions and matrix constructors.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `mata_st_data` | operator | partial | `st_data(obs, varlist)`; `obs` may be a range, vector or `.`. |
| `mata_st_store` | operator | implemented | `st_store(obs, varlist, value)`; shapes must conform. |
| `mata_st_matrix` | operator | implemented | `st_matrix(name [, value])`. |
| `mata_st_numscalar` | operator | implemented | `st_numscalar(name [, value])`. |
| `mata_st_local` | operator | implemented | `st_local(name [, value])`. |
| `mata_st_global` | operator | implemented | `st_global(name [, value])`. |
| `mata_J` | operator | implemented | `J(rows, cols, value)`. |
| `mata_I` | operator | implemented | `I(n)`. |
| `mata_builtins` | operator | partial | The `[M-5]` builtin functions. |

Gaps:

- **mata_st_data** — the frame is cast to float, so reading a *string* variable fails with rc 199 `invalid Mata statement: could not convert string to float`.
- **mata_builtins** — the matrix, maths, row/column-summary, `select`, missing and string families are present; anything absent raises `Mata function name() not implemented` (rc 199) instead of guessing.

## 14. Error codes

The `r(###)` contract and the fail-explicitly rule.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `rc_codes` | result-class | implemented | `r(###)` / `_rc`; every failure carries a numeric code. |
| `rc_fail_explicitly` | result-class | implemented | An unimplemented path stops with an explicit message, rc 199. |
| `rc_measured_divergences` | result-class | partial | The same command run in both runtimes, codes compared. |

Gaps:

- **rc_measured_divergences** — measured on this host: `count in 0/2` (0 vs 198), `by g: regress` (199 vs 0), `syntax varlist [weight]` (199 vs 197), `syntax using/` + bare file (100 vs 101), `display .a` (198 vs 0). `syntax varname` with a missing argument is rc 100 in both. The `mark` difference is recorded under `mark` in area 11.

## 15. Command groups

Data, estimation, postestimation, graphics and programming commands.

| Primitive | Kind | Status | Interface |
|---|---|---|---|
| `group_data` | command-group | partial | Read, write, reshape, describe. |
| `group_estimation` | command-group | partial | Fit a model, store the fit in `e()`. |
| `group_postestimation` | command-group | partial | Read `e()` and produce predictions, tests, tables. |
| `group_graphics` | command-group | partial | Draw a plot and export it. |
| `group_programming` | command-group | implemented | The do-file language: macros, control flow, programs. |

Gaps:

- **group_data** — implemented: `use save saveold import export append merge reshape collapse expand contract preserve restore keep drop rename order sort gsort duplicates generate replace egen recode encode decode destring tostring recast compress format label describe list tabulate summarize count correlate codebook inspect tabstat mean total centile`. Absent (rc 199): `table`, `collect`, `frame`, `mvencode`, the remaining `joinby` variants.
- **group_estimation** — implemented: `regress logit logistic probit poisson areg ivregress reghdfe xtreg ttest`. The wider catalogue (`glm`, `mixed`, `nbreg`, `tobit`, …) is absent.
- **group_postestimation** — implemented: `predict`, `test`, `estimates store`, `eststo`/`estadd`/`esttab`/`estout`. Absent: `margins` with options, `lincom`, `nlcom`, `estimates table`, `sreturn`.
- **group_graphics** — implemented: `histogram`/`hist`, `kdensity`, `scatter`, `line`, `bar`, `box`, `dot`, single-plot `twoway scatter|line`, and `graph export` to PNG/SVG/PDF. The rest of the graph grammar is rc 199 by design.

---

## How to use this document

If you are writing a conformance case, a feature-inventory entry or a review
comment, **cite the primitive name from this catalogue and stop there**:

- Say "`ts_S` diverges — `S2.y` returns 0 instead of 4" rather than
  re-explaining what `S.` does. The definition already exists here.
- Name the primitive in the case file's comment and in any report, e.g.
  `* primitives: ts_S, ts_requires_tsset`.
- Do not re-derive an interface from Stata help pages, from memory, or from
  Stata 15 output: `python qa/stata_primitives.py --primitive NAME` is the one
  place the recorded interface lives.
- When a case *settles* a `partial` or `unknown` record, update
  `openstat_status` and `openstat_note` in `qa/stata_primitives.json`, run
  `python qa/stata_primitives.py --check`, and adjust the matching row and gap
  note here. A status that no longer matches a measured case is a bug in this
  document, not a detail.
- When you find a *new* shared primitive, add a record (with a `sthlp:` or
  `targets:` source that `--check` can resolve) instead of describing it ad hoc
  in a case file.
