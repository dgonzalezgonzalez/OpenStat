# Compatibility evidence

Only synthetic data and short scripts are used. No personal do-files, Stata ado
sources, licenses, or datasets are included. Fixtures use deterministic algebra,
so differences cannot be explained by different random-number generators.

On Windows the interpreter is the project virtual environment; every command
below is written for PowerShell:

```powershell
$py = ".\.venv\Scripts\python.exe"
```

## Fast checks (no Stata needed)

```powershell
& $py -m pytest -q
```

## Differential corpus against licensed Stata

`qa/run_differential.py` discovers the corpus from `qa/cases/*.do` — adding a
fixture needs no edit to the harness — and runs each fixture in OpenStat and in
licensed Stata, in its own temporary directory.

```powershell
& $py qa/run_differential.py --list                    # the corpus, with features
& $py qa/run_differential.py --all                     # every case (the default)
& $py qa/run_differential.py --case data --case panel  # one or more cases
& $py qa/run_differential.py --all --jobs 2            # two cases at a time
& $py qa/run_differential.py --all --report qa/report.json   # --json is an alias
```

* `--case NAME` is repeatable; `--all` is the default when no `--case` is given.
  An unknown name is a usage error, not a silent skip.
* `--jobs N` runs N cases concurrently, `1`–`4`. Four is the hard ceiling: each
  concurrent case owns a licensed-Stata process.
* `--stata PATH` or `STATA_EXE` selects the executable; otherwise the usual
  Windows install directories are probed.
* `--timeout` is per case and per engine (default 30 s, maximum 180 s).
* `--online-ssc` opts in to the one network check (`ssc`); without it that case
  is reported as `skipped`, never as passing.

Each result has `status` ∈ `passed`, `failed`, `error`, `skipped`. `error` means
the harness could not get a comparison at all (engine crash, timeout, no
completion sentinel) and `failed` means the two engines disagree. Every result
also carries `first_divergence`, the first thing that differed, tagged by kind:

| kind | meaning |
|---|---|
| `engine` | one engine did not complete |
| `return_code` | `r()`/rc differ |
| `scalar` | an `__OPENSTAT__<name>=<value>` sentinel differs or is missing |
| `csv` / `dta` / `png` / `ssc` | the produced artifact differs |

The report records the reference version, return codes, numerical sentinels,
script hashes, execution time, and checks for CSV contents, DTA 118 labels, and
valid PNG export. PNG checks verify format and requested width; they do not
establish visual equivalence. Negative fixtures require explicit errors and
accurate source lines. `stata_reference.json` contains the licensed-Stata results
used by the fast tests; changing a fixture invalidates its stored hash.

Additional fixtures check recursive `replace`, numeric logical variables,
missing bounds in `inrange()`, first-observation programming `if`, repeated macro
expansion in `while`, and returning from a program with `exit 0`. A negative
fixture records that Stata rejects a self-reference to an undefined variable in
`generate` with r(111). Estimation checks include HC1 and clustered OLS standard
errors, clustered inference degrees of freedom, logistic stored log-odds
parameters, factor variables (`i.g##c.x`), all four weight types, `ivregress 2sls`
(robust and `small vce(cluster)`), the panel fixtures (`xtset`/`areg`/`xtreg fe`,
lag operators), and `reghdfe` two-way absorption when the package is installed on
the reference ado path. The fast test separately checks OpenStat's default
odds-ratio display header.

Network package installation is a separate, opt-in check:

```powershell
& $py qa/run_differential.py --case ssc --online-ssc
```

This installs `distinct` only into temporary directories. SSC acquisition uses
the canonical HTTP repository at `fmwww.bc.edu/repec/bocode`; the report compares
downloaded ado SHA-256 hashes with Stata's download. Explicit HTTPS sources retain
certificate validation. Downloaded package code
is never copied into this repository and is not relicensed under MIT. A skipped
network check is not evidence of package compatibility.

## The conformance harness (cross-platform)

`qa/conformance_run.py` runs the same `qa/conformance/cNN_*.do` in Stata and in
OpenStat, each in its own temporary directory, and compares the
`__OPENSTAT__<name>=<value>` sentinels plus `__OPENSTAT_RETURN_CODE__=<rc>`.

The licensed Stata is found per platform: the `STATA_EXE` environment variable
first, then `%ProgramFiles%\StataNow19\StataMP-64.exe` and
`%ProgramFiles%\Stata19\StataMP-64.exe` on Windows, and
`/Applications/StataNow/StataSE.app/Contents/MacOS/stata-se` on macOS. It is
launched in batch mode with the flag that platform expects: `/e` on Windows,
`-b` on macOS. The wrapper do-file is always written UTF-8 without a BOM and
with forward-slash paths. On Windows Stata is started from the case's own
scratch directory, because the install root under `Program Files` is not
writable.

```powershell
& $py qa/conformance_run.py --list                          # the available case names
& $py qa/conformance_run.py --case c01_language             # one case
& $py qa/conformance_run.py --case c01_language --json conf.json
& $py qa/conformance_run.py --all --jobs 4                  # every case, four at a time
& $py qa/conformance_run.py --all --diff                    # unified transcript diff
```

* `--stata PATH` selects the executable explicitly.
* `--jobs N` runs up to N cases concurrently. Each case already has its own
  temporary directory, so the cases are independent; `--jobs 1` is the serial
  behaviour. Keep the total (this harness plus any other Stata run) at four
  processes.
* `--json PATH` writes the machine-readable result file (`schema_version` 1).
  It records `generated_utc`, `platform`, `stata_executable`, `stata_batch_flag`,
  and a `summary` of `passed`/`failed`/`error`/`missing`/`total`. Per case it
  records `name`, `script`, `status`, `rc` (`stata` and `openstat`), `rc_differ`,
  `scalars` (`stata` and `openstat` maps), `scalar_mismatches` (name, Stata
  value, OpenStat value), the first `--diff-lines` lines of the transcript
  `diff`, `diff_line_count`, `duration_seconds`, and `problems`.
* The exit code is non-zero when any case fails.
* `qa/latest-report.json` is the conventional `--json` target and is gitignored,
  so a local run never dirties the tree.

## Running a do-file through Stata

`qa/stata_run.py` runs any do-file in the licensed Stata in batch mode and
prints the log (the `log using` banner is dropped unless `--banner` is given):

```powershell
& $py qa/stata_run.py qa/conformance/c01_language.do
& $py qa/stata_run.py --dir qa/_scratch/probes               # every .do in a directory
& $py qa/stata_run.py --stata "C:/Program Files/StataNow19/StataMP-64.exe" case.do
& $py qa/stata_run.py --workdir qa/_scratch/stata-keep case.do   # keep the scratch files
```

This is the tool to use when a rule has to be *measured* on Stata rather than
reasoned about. `qa/stata_run.sh` and `qa/probe_stata.sh` remain as POSIX-shell
convenience wrappers; they are not used on Windows and need no changes there.

## One ranked gap queue

`qa/gap_report.py` merges three sources into a single ranked, deduplicated
queue — pytest failures, conformance mismatches, and differential failures:

```powershell
& $py qa/gap_report.py                         # run everything, write both reports
& $py qa/gap_report.py --no-stata              # reuse qa/latest-report.json + qa/report.json
& $py qa/gap_report.py --jobs 2                # cap concurrent licensed-Stata processes
& $py qa/gap_report.py --json out.json --md out.md
& $py qa/gap_report.py --top 50                # how many gaps the Markdown shows
```

It writes `qa/gap-report.json` and `qa/gap-report.md` and prints the Markdown
summary on stdout. The JSON has `schema_version`, `generated_utc`, `sources`
(per source: command, path, timestamp, summary), `baseline` (the recorded
starting point plus what this run measured and a `regressed`/`at-or-better`
verdict), `totals`, and `gaps`. Each gap is
`{id, rank, score, source, sources, case, symptom, first_divergence,
owning_module, severity, kind, evidence}`:

* `owning_module` is a best guess from the fixture name, the failing test file,
  and the command named in the symptom. It is a routing hint for the swarm, not
  a claim about where the defect lives.
* gaps are deduplicated on `(owning_module, feature, kind)`; a merged gap keeps
  one `evidence` item per contributing finding.
* ranking is severity (`high`/`medium`/`low`) plus a bonus for every additional
  source or finding that corroborates the same gap.

`--no-stata` still runs pytest (it needs no oracle) and reads the two harness
reports that are already on disk, so an offline triage keeps working. Each
reused source is marked `"reused": true` and `"stale": true` only when the
report predates the newest file under `openstat/`, `qa/cases/`, `qa/conformance/`
or `tests/` — a reused report is only evidence for the code it was run against.

## Current state of this corpus

See `report.json` for the cases that were actually run and their timestamps;
`qa/gap-report.md` for the ranked queue produced from the same run. A stale
report is not evidence.

Passing these examples does not establish compatibility with arbitrary do-files,
all estimation commands, all versions of DTA, all Mata programs, binary plugins,
all community packages, graph appearance, or Stata's GUI. `report.json` records
only the cases actually run.
