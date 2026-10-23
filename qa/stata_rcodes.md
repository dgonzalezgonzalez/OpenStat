# Stata return codes (`r(###)`) — measured catalogue

`qa/stata_rcodes.json` is a machine-readable catalogue of Stata 19.5 (MP) return
codes and the short message Stata prints above `r(N)`. It exists so OpenStat's
error paths can quote the text Stata itself prints, instead of inventing it.

## Method (measured, never transcribed)

Oracle: `C:\Program Files\StataNow19\StataMP-64.exe`, started in batch
(`/e do <wrapper>`) from a writable temporary directory, with a UTF-8 no-BOM LF
wrapper, `set linesize 255` and `log using`. Each oracle is **one** Stata launch
(~3 s for the whole sweep), not one launch per code.

1. **`error N` sweep, N = 1…9999.** `capture noisily error N` sets `_rc` to `N`
   and prints Stata's table entry for that code. The do-file brackets every probe
   with `__BEGIN__N` / `__END__N=_rc` sentinels, so the message is exactly what
   sits between them (echoed command lines are stripped). This is the only way to
   reach every code.
2. **Command triggers (24 codes).** `capture noisily <command>` raises a code the
   way real commands do. For parameterised codes this prints text that `error N`
   cannot reproduce — `error 111` prints `invalid syntax`, while `list zzr` prints
   `variable zzr not found` — so a trigger overrides the generic entry and the
   `generic` field keeps the fallback for comparison.

Dead ends, recorded so nobody retries them: `search rc N` and `help` print
`request ignored because of batch mode` (r(8002)) and set `_rc` 0; the `[R] Error
messages` table ships only as a 30 MB PDF; a `numlist`-based sweep dies with
`invalid numlist has too many elements` (r(123)) — `forvalues` has no such limit.

## Coverage

- **1 243 codes**, probed range 1…9999; nothing above 9999 carries a message.
- 1 242 entries come from `error N` (`"source": "stata-19.5:error-command"`).
- 24 codes carry command-verified text (`"source": "stata-19.5:command-trigger"`):
  4, 5, 9, 100, 101, 109, 110, 111, 119, 120, 133, 198, 199, 301, 302, 303, 601,
  602, 603, 610, 621, 622, 2000, 2001. 111 and 198 also carry `variants`.
- 238 distinct message strings. 999 of the 1 000 codes in 3000–3999 are the Mata
  family `Mata run-time error`, so the *informative* catalogue is ~244 entries.
- Per-thousand blocks: 1–999 → 175, 1000–1999 → 6, 2000–2999 → 3, 3000–3999 →
  1000, 4000–4999 → 33, 5000–5999 → 13, 6000–6999 → 6, 7000–7999 → 3,
  8000–8999 → 3, 9000–9999 → 1 (plus 4 000 / 4 099 class-system entries).

## Gaps (honest)

- Codes whose `error N` entry is empty are **omitted, not guessed**. Within 1–199
  that is 1, 6–17, 19–99, 105, 112–118, 128–129, 136–139, 150, 154–160, 163–169,
  177–179, 183, 185–189, 192–195 (reserved, or raised only by specific commands).
  r(1) in particular prints no text at all, although OpenStat uses it for
  "execution interrupted".
- Parameterisation: where Stata substitutes a name the catalogue stores the
  *template* (`variable <name> not found`). `<name>` and `<dir>` are placeholders,
  not literal text.
- r(900)–r(916) are truncated to their first line (`extra_lines` records how many
  follow — usually Stata's help pointers).
- A code reachable only through one command keeps its generic wording until a
  trigger is added. Extend `TRIGGERS` in `qa/stata_rcodes.py`; never hand-edit the
  JSON.

## Refresh / check

```sh
.venv\Scripts\python.exe qa/stata_rcodes.py --refresh   # re-measure, rewrite JSON
.venv\Scripts\python.exe qa/stata_rcodes.py --check     # re-measure 22 codes + all triggers, assert
.venv\Scripts\python.exe qa/stata_rcodes.py --show 111 601
.venv\Scripts\python.exe qa/stata_rcodes.py --json C:/tmp/rc.json --refresh
```

`--check` re-runs the oracle and exits non-zero on any drift (it currently passes:
22 `error N` spot-checks and 28 trigger checks). `STATA_EXE` or `--stata PATH`
selects a different install. Other lanes can skip the CLI: `from stata_rcodes
import lookup; lookup(111)` → `'variable <name> not found'` (or `None`).

## Licensing

The JSON stores short interface facts only: the code number and the message string
Stata prints at run time, measured against a licensed local installation. No
StataCorp documentation prose, tables or assets are copied, and long multi-line
messages are truncated to their first line. This is QA evidence, not redistributed
Stata content.
