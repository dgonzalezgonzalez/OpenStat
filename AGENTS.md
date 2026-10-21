# OpenStat — agent instructions

OpenStat is an independent, MIT-licensed Stata-compatible statistical workstation
(Python + PySide6). The goal is **behavioural parity with Stata**, verified by
differential testing against a real Stata installation — not by copying Stata.

## Use REA for this project — as much as you can and need

**REA (the `rea` MCP server) is a primary investigation tool for this repo, not
an optional extra.** Any time you need to know *how Stata actually behaves* for
something you cannot read in public docs — a numeric algorithm, a magic
constant, a table, a default, a code path, a UI resource — reach for REA
against the installed Stata binary before guessing.

Working REA calls on this project (all verified to work here):

| Goal | Tool |
|---|---|
| Open the Stata binary, get its SHA-256 | `rea.open_binary` |
| Mach-O segments/sections (VMA → file offset mapping) | `rea.inspect_macho({detail:"complete"})` |
| File/section headers, symbols, relocations, DWARF | `rea.inspect_with_objdump({path, operation})` |
| Section → file-backed ranges of any binary | `rea.inspect_binary_layout({path})` |
| Export a retained Evidence record to disk | `rea.export_evidence_bundle({path, overwrite})` |
| List/search retained records | `rea.get_evidence_bundle({detail:"summary"})` |
| Provider + capability status | `rea.binary_session({})` |
| Find a string and its cross-references | `rea.search_strings({pattern})` → `rea.xrefs({address})` |

REA limitations on this host (do not waste time rediscovering them):

- **Ghidra 12.1.4 is available** (`GHIDRA_INSTALL_DIR` →
  `~/.local/opt/ghidra_12.1.4_PUBLIC`, Temurin JDK 21), but its **native
  macOS decompiler has to be hand-built** from the shipped C++ source
  (`brew install binutils` for `bfd.h`, then `make` in
  `Ghidra/Features/Decompiler/src/decompile/cpp`, and copy the binaries to
  `Ghidra/Features/Decompiler/os/mac_x86_64/{decompile,sleigh}`). Without
  that step Ghidra opens the binary but cannot decompile anything.
  `REA_GHIDRA_STARTUP_TIMEOUT_MS=1500000` and
  `REA_GHIDRA_DISABLED_ANALYZERS=DecompilerSwitchAnalyzer,Stack,FunctionID`
  are set in `~/.config/opencode/opencode.json` under `mcp.environment`.
- Hopper and Kuna are available; **IDA is not** (and cannot be:
  `ida-pro-mcp` rejects IDA Free).
- The REA MCP server caches provider availability from startup — a newly
  installed provider binary needs a server restart.
- `inspect_binary_layout` is **Linux x64 only** (pwntools provider) and
  returns `capability_unavailable` for a Mach-O target. Use
  `inspect_macho({detail:"complete"})` for segment/section layout instead.
- `inspect_with_objdump` with `operation:"disassemble"` **exceeds the MCP
  stdio response budget** on the 20 MB `__text` section; the export is
  truncated at 16 MiB. See "Narrowing a Mach-O section window" below.
- `lldb`'s Python API returns empty frames/registers here, so live debugging of
  Stata is not usable. Static disassembly is the working approach.
- **Kuna's project export times out on the 70–135 MB Stata binaries**
  (`REA_KUNA_PROJECT_TIMEOUT_MS` is exceeded), so `address_name`,
  `procedure_info` and `resolve_containing_procedure` are unavailable for
  them. The *live* operations (`search_strings`, `xrefs`) do work.
- **The Stata 19 macOS binaries keep their local symbol names.** `nm -n` and
  `objdump` show Stata's own routine names (`_retval_get_as_string`,
  `_macsub`, `_macgen`, `_sreforms`, `_fmt_18p0g`, …), so a routine can be
  located by name and its xref site read directly. This is the single most
  useful fact for reverse engineering this target.

### Reading the disassembly without REA's transport limit

`objdump` is installed at `/usr/bin/objdump`. Because REA's MCP response
budget cannot carry a 20 MB `__text` disassembly, the practical workflow is
to run `objdump` locally, into the repo's ignored `qa/_scratch/` directory,
and grep it. The Stata dylib slice at `/tmp/reaslice/libstata-se-x86_64.dylib`
has **file offset == VM address** for `__text` and `__cstring`, so an
address found by REA can be read directly out of the file.

### Narrowing a Mach-O section window (the trick that makes REA disassembly work)


`qa/_scratch/make_window.py` writes a copy of the Stata binary whose `__text`
section header describes only a window around a target address. No
instruction byte is changed — only the extent objdump is asked to walk — so
the disassembly of the window is byte-for-byte the disassembly of the
original, and REA can return it in full:

```sh
.venv/bin/python qa/_scratch/make_window.py 0x10013b000 0x6000 qa/_scratch/stata-rng-window
# then: rea.inspect_with_objdump({path: ".../stata-rng-window", operation: "disassemble"})
```

Mach-O `section_64` layout: `addr` at +32, `size` at +40, `offset` at +48.

### What REA already settled for this repo (don't re-derive it)

See [`docs/random-numbers.md`](docs/random-numbers.md) for the full record.
Short version, all confirmed by REA disassembly of the shipped binary:

- MT64 uniform transform is `((w >> 12) + 0.5) * 2**-52` — five instructions
  at `0x1005b6450`, constants `0.5` @ `0x10143f5d0` and `2**-52` @ `0x1014767a0`.
- `rnormal()` is a ziggurat, not an inverse-normal function. Tables live in
  `__data`: `A`@`0x101d6f0c0`, `B`@`0x101d6f2c0`, `C`@`0x101d6f4c0`,
  `D`@`0x101d6f6c0`, `E`@`0x101d6f7c0`, `F`@`0x101d6f8e0`, and the two
  acceptance tables `T`@`0x101d6efc0` / `T'`@`0x101d6eec0`.
- The selector flag @`0x102781dc8` is in `__common` (BSS ⇒ zero) and the code
  at `0x10013b863` picks `T'` only when `(flag & ~2) == 1` ⇒ **`T` is the
  table Stata uses**. T vs T' first diverge at draw 433,727; Stata follows `T`.
- Tail constants are *derived at runtime* @`0x10013bdf1`:
  `TAIL_LIMIT = 0.125 + 3.75 = 3.875`, `TAIL_SQUARE = 3.875**2`.
  `openstat/rng_tables.py` derives them the same way.

### What REA settled about the display formats and the macros

See [`docs/formats.md`](docs/formats.md) for the full record. Short version:

- Stata's `%w.dg` model is implemented exactly in `openstat/formats.py`
  (integer-decimal arithmetic, no floats in the digit path). It reproduces
  Stata 19.5 byte-for-byte on 385 815 measured renderings plus 7 160 fresh
  probes. The full rule set is in `docs/formats.md` — read it before
  touching `formats.py`.
- **Macro expansion uses `%18.0g` and strips the padding.** The shipped
  binary names the format string `_fmt_18p0g` (the literal `"%18.0g"` at
  `0x4062e34` in `libstata-se-x86_64.dylib`) and uses it in exactly the
  routines that do the expansion:
  `_retval_get_as_string` (r/e/c → text), `_macsub` (macro substitution),
  `_macgen` (`local`/`global x = expr`) and `_macincr` (`local x = x + 1`).
  Each of them formats with `_sreforms` and then trims the padding
  (`_trimblnk` / `_skipblnk`). `_macgen` picks the format conditionally on
  `_vernum >= 220` (Stata 14+).
- Stata's overflow markers are the extended-missing ladder
  `L(n) = 0x7fe0010000000000 + n * 2**40` (`n = 0…25`); the dta writer
  builds exactly those bit patterns.
- `generate` creates a **float** variable unless a type is named;
  `set type float|double` changes the default. An out-of-range integer
  becomes *missing*, not a clamped value.
- Stata's `describe` layout is reproduced byte-for-byte: 255-character
  rules, the `Variable / Storage / Display / Value` header, variable rows
  at column offsets 0 / 16 / 24 / 35 / 46, and the `Observations:` /
  `Variables:` count lines right-justified to end at column 27.

**Never copy StataCorp code, tables, or assets into this repo.** Recovering
*observable behaviour* (numeric constants of a published method, observed
output) and reimplementing it independently is the project's rule — see
`CONTRIBUTING.md`.

## The oracle: Stata 19.5 at `/Applications/StataNow`

```sh
STATA_ROOT="/Applications/StataNow"
STATA_EXE="$STATA_ROOT/StataSE.app/Contents/MacOS/stata-se"
```

**Stata 19.5 (SE) is the oracle.** It must be run with **CWD =
`/Applications/StataNow`**; the harnesses already do this.

Stata 15.1 (`/Applications/Stata 15`, `StataMP.app/Contents/MacOS/stata-mp`) is
still installed and was the oracle until 21 Oct 2026. It is **no longer the
reference**: the two versions differ in observable behaviour, so any rule
fitted on Stata 15 must be re-measured on Stata 19 before it is trusted.

Known Stata 15 → Stata 19 difference (measured, 350,865 renderings compared,
108 differ — all at decimal exponent 19):

- Stata 15 renders `1e19` at `%22.0g` as `1000000000000000000` (fixed, 20
  integer digits); Stata 19 renders it as `1.000000000000000e+19` (scientific).
- The switch is at the *integer-digit count*: Stata 19 keeps a value in fixed
  notation only while it needs at most 19 integer digits, so `1e19` (20
  digits) is scientific from the first width, while `1e15` (16 digits) stays
  fixed from `%18.0g` onwards.
- Everything else measured so far (the `%g` digit rules, the fixed/scientific
  switch for exponents -6..18) is identical between the two versions.

When a rule is uncertain, **measure it on Stata 19** — do not reason from
Stata 15 output.

## Commands

```sh
# setup (macOS)
python3 -m venv .venv && .venv/bin/python -m pip install -e ".[desktop,test]"

# unit tests
.venv/bin/python -m pytest -q

# differential vs real Stata, one conformance case
.venv/bin/python qa/conformance_run.py --case c09_random
.venv/bin/python qa/conformance_run.py --all          # all 10 cases
.venv/bin/python qa/conformance_run.py --all --diff   # transcript diff

# replication packages (AEA / JPE)
.venv/bin/python qa/replication_run.py --list
.venv/bin/python qa/replication_run.py --case bdmmm_jpe_randomdata

# one-off: run any do-file through Stata 19 and print the log
qa/stata_run.sh path/to/case.do

# headless OpenStat
.venv/bin/python -m openstat --batch examples/quickstart.do --json out.json
```

`--batch` requires an **absolute** do-file path. The GUI is
`.venv/bin/python -m openstat` (needs `.[desktop]`; use `QT_QPA_PLATFORM=offscreen`
for headless Qt).

## Conformance harness contract (don't break it)

`qa/conformance_run.py` runs the same `qa/conformance/cNN_*.do` in Stata and in
OpenStat, each in its own temp dir, and compares the `__OPENSTAT__<name>=<value>`
sentinels plus `__OPENSTAT_RETURN_CODE__=<rc>`.

- A case file emits sentinels like
  `display "__OPENSTAT__x=" %21.14g \`value'`.
- **Sentinel precision matters.** `%21.14g` truncates small values and creates
  false mismatches. For full-precision reference values use
  `format z %30.17g` + `list z, noobs`, and parse the value column with
  `line.split('|')[1]` (Stata's log rows are `  |value`; the value is the
  *first* field, not the second).
- Stata's `display` is progressive: an error after earlier output still leaves
  the earlier output in the log. Stata's unresolvable name is `r(111)` "X not found".
- Stata's `%g` drops the leading zero of a fraction: `.5`, never `0.5`.
- `r()`/`e()`/`c()` expand in **macro position** (`` `r(mean)' ``) to *text*:
  the value rendered with `%18.0g` and the padding removed. In **expression
  position** (`r(mean)`) the value is a number and is shown with `%10.0g`.
  An unknown name expands to nothing in macro position; `r()`/`e()` are the
  missing value in expression position, and an unknown `c()` is `r(133)`.

## Replication harness contract

`qa/replication_run.py` + `qa/replication/manifest.json` run real published
replication packages (AEA, JPE) through OpenStat.

- Third-party package data lives in gitignored `qa/replication/_packages/` and
  `qa/replication/_artifacts/`. **Never commit package data.**
- Each case is a repo-local `qa/replication/cases/<name>/case.do` that emits
  `__OPENSTAT__` sentinels; a `prepare.do` (run by Stata) may build reduced
  datasets first.
- Packages that need restricted PII or raw data that is not in the package
  cannot be run — record that, don't fake it.

## SSC packages

`ssc install` writes to `~/.openstat/ado`; override with the `OPENSTAT_ADO`
env var (e.g. `OPENSTAT_ADO=/tmp/ado1`). The endpoint is
`http://fmwww.bc.edu/repec/bocode/<letter>/<name>.pkg` (HTTP, because HTTPS did
not negotiate on this host). Downloading a package is not evidence it runs —
`distinct` is the one package verified to install *and* execute.

## Known state (as of 22 Oct 2026)

- All 10 `qa/conformance` cases PASS differentially; `bdmmm_jpe_randomdata` and
  `dropout_jpe_descriptives` pass; `ssc install distinct` + `distinct x` works.
- `pytest -q` → **89 passed, 90 subtests passed**.
- The `%w.dg` model in `openstat/formats.py` is **complete**: it reproduces
  Stata 19.5 byte-for-byte on 385 815 measured renderings plus 7 160 fresh
  probes. The rule set is documented in [`docs/formats.md`](docs/formats.md).
  Do not "fix" `formats.py` from intuition — measure on Stata 19 first.
- Macro expansion, `local`/`global` assignment, the `c()` table and
  `generate`'s float default have all been re-measured on Stata 19.5 and
  now match. In particular:
  - `` `r(x)' ``/`` `e(x)' ``/`` `c(x)' `` expand to the **unpadded `%18.0g`**
    rendering (confirmed in the binary, see above). In *expression* position
    the same values are re-rendered with `%10.0g`.
  - `local x = <expr>` stores the same `%18.0g` text, so a `local` keeps
    full precision even though the display rounds.
  - An **unknown** `r()`/`e()`/`c()` name expands to the empty string in
    macro position; `r()`/`e()` in expression position is the *missing
    value*, and an unknown `c()` in expression position is the error
    `c(name) undefined` (rc 133).
  - Only the `c()` names Stata 19.5 actually defines are in the table.
    `c(SCS)`, `c(n_processors)`, `c(endian)`, `c(release)`, `c(cmd)`,
    `c(S_level)`, `c(nobs)`, `c(node)`, `c(ado_version)` and friends are
    **undefined** in Stata 19 and must stay undefined here.

### Known divergences (measured, not yet closed)

| Divergence | Stata 19.5 | OpenStat |
|---|---|---|
| `c(seed)` | the RNG state as a hex string, `"XAA"` + 312 MT64 state words + 8 further bytes | the numeric seed |
| `c(adopath)` | `BASE;SITE;.;PERSONAL;PLUS;OLDPLACE` | OpenStat's own ado directory |
| `c(mode)` | `batch` in batch mode, `interactive` in the GUI | always `batch` |
| `c(filename)` | the path of the last dataset loaded | the path of the last `.dta` loaded |
| `c(max_memory)` | `.` (missing) | `.` (missing) — matches |

## Repo layout

```
openstat/         the runtime (all Python)
  engine.py         core interpreter, command dispatch, dataset state
  parser.py         statement lexer/parser
  parser_expr.py    expression recursive-descent parser
  expression.py     expression evaluator + function table
  macros.py         macro expansion, r()/e()/c() resolution
  formats.py        Stata display formats (%f %g %e %x ...)
  storage.py        Stata's storage types and their default formats
  syntax.py         ado `syntax' parsing
  rng.py / rng_tables.py   MT64 + ziggurat normal
  cclass.py          Stata's c() system values
  duplicates.py panel.py program_support.py generation.py statistics.py
  data_commands.py metadata.py mata.py graphics.py packages.py gui.py
qa/                verification harnesses
  conformance_run.py  differential vs Stata (the main tool)
  replication_run.py  AEA/JPE replication packages
  run_differential.py older checked-in fixture suite (qa/cases/*.do)
  conformance/        cNN_*.do conformance cases
  replication/        manifest + cases
  _scratch/           throwaway probe scripts (gitignored — see below)
docs/              compatibility.md, feature-inventory.md, formats.md,
                   inspection.md, random-numbers.md
tests/             pytest suite
```

`openstat/engine.py` is the largest file (~1700 lines). Keep new behaviour in
focused modules and have `engine.py` delegate — the repo already follows that
pattern (`rng.py`, `cclass.py`, `duplicates.py`, `panel.py`).

## Gotchas

- **`qa/_scratch/` is gitignored and must stay that way.** It holds probe
  scripts and large binary artifacts (`full_disasm.txt` is 357 MB). It is
  listed in `.gitignore` — do not commit it.
- `openstat/syntax.py` and `openstat/engine.py` wrap their comment blocks
  differently from what you expect; the `edit` tool's exact-match often fails.
  **Use a Python patch script instead** when a pattern won't match.
- Debug/probe scripts must live **inside the repo directory** (or `/tmp`), not
  in the user's home directory.
- `openstat/__main__.py` exits with the runtime return code; `--json` writes
  the full status including `r` and `e`.
- `docs/compatibility.md`, `docs/feature-inventory.md`, `docs/formats.md`,
  `docs/inspection.md` and `README.md` must be kept in sync with what
  actually works. They make explicit, bounded claims — keep them honest
  rather than aspirational.
- `docs/inspection.md` records the **REA inspection record**. Extend it with
  new REA findings (evidence type, limits, hashes) rather than replacing it.
- `openstat/_rngfast.c` is an optional C accelerator for the MT64 core. The
  pure-Python path in `openstat/rng.py` is the reference; the compiled
  extension is a build-time speedup only and must produce identical draws
  (`tests/test_rng_fastpath.py` asserts this).

## Working style

- Implement from public documentation plus *observed* behaviour; never from
  StataCorp source.
- Missing primitives should raise an explicit error, not guess. Stata's
  `r(###)` codes matter — the conformance cases assert them.
- Before claiming a semantic is Stata-compatible, prove it differentially:
  add a `qa/conformance/cNN_*.do` case and run `qa/conformance_run.py --case`.
- Commit in conventional-commit form (`feat(core): ...`), keeping the RNG,
  duplicates, macros and cclass work in reviewable commits.
