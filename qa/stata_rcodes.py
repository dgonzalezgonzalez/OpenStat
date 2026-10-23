"""Catalogue Stata's ``r(###)`` return codes and their canonical messages.

The catalogue is *measured*, not transcribed: it is built by asking the
locally installed Stata to raise every code and recording what Stata prints.

Two oracles are used, both inside a single batch run (Windows ``/e``, macOS
``-b``) so a full sweep costs seconds, not hours:

``error N``
    Sets ``_rc`` to ``N`` and prints the message Stata associates with the
    code.  This is the only way to reach *every* code -- 1 through 9999 in one
    do-file -- and it yields the generic table entry for each one.

``capture noisily <command>``
    Raises a code the way real commands do.  For parameterised codes this
    prints text that ``error N`` cannot reproduce: ``error 111`` prints
    ``invalid syntax`` but ``list nosuchvar`` prints ``variable nosuchvar not
    found``.  A curated trigger table therefore *overrides* the generic entry
    for the codes OpenStat actually raises.

``search rc N`` and ``help`` are ignored in batch mode ("request ignored
because of batch mode"), and the ``[R] Error messages`` table ships only as a
30 MB PDF, so neither is used.

Usage::

    python qa/stata_rcodes.py --refresh           # re-measure, rewrite JSON
    python qa/stata_rcodes.py --check             # re-measure, compare, assert
    python qa/stata_rcodes.py --json out.json     # write somewhere else
    python qa/stata_rcodes.py --show 111 601      # print stored entries

The stored JSON is ``qa/stata_rcodes.json``; the prose summary is
``qa/stata_rcodes.md``.  Messages are short interface facts (the text Stata
prints), never StataCorp documentation prose.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, NamedTuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import stata_run  # noqa: E402  (same directory; stands alone like the other qa scripts)

SCHEMA_VERSION = 1
SWEEP_LO = 1
SWEEP_HI = 9999
DEFAULT_JSON = HERE / "stata_rcodes.json"
DEFAULT_MD = HERE / "stata_rcodes.md"

#: The sentinel token every trigger substitutes into its command.  Any
#: occurrence of it in Stata's message is rewritten as ``<name>`` so the
#: stored text is the message *template*, not one observation of it.
TOKEN = "zzr"
PLACEHOLDER = "<name>"

#: Selected codes the ``--check`` pass re-measures through ``error N``.
CHECK_STRINGS = (2, 3, 4, 5, 18, 100, 102, 106, 120, 146, 199, 301, 401,
                 601, 610, 621, 691, 900, 1000, 2000, 3000, 4000)


class Trigger(NamedTuple):
    """One command that raises a specific code the way real commands do.

    ``reset`` re-loads ``sysuse auto`` (quietly) before the block so the
    trigger does not depend on a neighbour's leftovers; ``setup`` lines run
    verbatim (an expected-to-fail setup must carry its own ``capture``).
    """

    label: str
    code: int
    setup: tuple[str, ...]
    command: str
    reset: bool = True


#: Ordered so that state-destroying triggers (``clear``) come last: every
#: block runs in the same Stata session, mostly against ``sysuse auto``.
TRIGGERS: tuple[Trigger, ...] = (
    Trigger("assert-false", 9, (), "assert 1==2"),
    Trigger("type-mismatch", 109, (), f'generate {TOKEN} = price + "a"'),
    Trigger("variable-not-found", 111, (), f"list {TOKEN}"),
    Trigger("matrix-not-found", 111, (), f"matrix list {TOKEN}"),
    Trigger("name-not-found", 111, (), f"keep if {TOKEN} > 1"),
    Trigger("c-undefined", 133, (), f"display c({TOKEN})"),
    Trigger("invalid-format", 120, (), "format price %zz"),
    Trigger("invalid-syntax-if", 198, (), "summarize if"),
    Trigger("option-not-allowed", 198, (), "regress price mpg, zzrfoo"),
    Trigger("unrecognized-command", 199, (), TOKEN),
    Trigger("not-sorted", 5, (), "by make: summarize price"),
    Trigger("last-estimates-not-found", 301, (), "test price"),
    Trigger("last-test-not-found", 302, ("quietly regress price mpg",), "test"),
    Trigger("equation-not-found", 303, ("quietly regress price mpg",),
            f"test [{TOKEN}]price = 0"),
    Trigger("varlist-required", 100,
            (f"capture program drop {TOKEN}",
             f"program define {TOKEN}", "    syntax varlist", "end"), TOKEN),
    Trigger("varlist-not-allowed", 101,
            ("capture program drop zzrprog2",
             "program define zzrprog2", "    syntax, foo", "end"),
            "zzrprog2 price"),
    Trigger("no-observations", 2000, (), "regress price if price > 99999"),
    Trigger("insufficient-observations", 2001, (), "regress price if _n <= 1"),
    Trigger("file-not-found", 601, (), f"use {TOKEN}"),
    Trigger("file-already-exists", 602, (f"quietly save {TOKEN}, replace",),
            f"save {TOKEN}"),
    Trigger("file-cannot-open", 603, (),
            'save "C:/OpenStatNoSuchDir/' + TOKEN + '.dta"'),
    Trigger("file-not-stata-format", 610,
            ("capture file close zzrfh",
             f"file open zzrfh using {TOKEN}.dta, write text replace",
             'file write zzrfh "not a dataset" _n',
             "file close zzrfh"), f"use {TOKEN}"),
    Trigger("already-defined", 110, (f"quietly generate {TOKEN} = 1",),
            f"generate {TOKEN} = 1"),
    Trigger("dataset-changed", 4, ("quietly generate zzrflag = 1",), "sysuse auto"),
    Trigger("already-preserved", 621, ("preserve",), "preserve", reset=False),
    Trigger("nothing-to-restore", 622, ("capture quietly restore",), "restore",
            reset=False),
    Trigger("statement-out-of-context", 119, (), "continue", reset=False),
    Trigger("no-variables-defined", 111, ("clear",), f"list {TOKEN}"),
)

RC_SENTINEL = "__OPENSTAT_RETURN_CODE__="


# --------------------------------------------------------------------------
# running Stata
# --------------------------------------------------------------------------
def _wrapper(work: Path) -> Path:
    """The batch wrapper, identical in shape to ``qa/stata_run.py``'s."""
    wrapper = work / "runner.do"
    text = (
        "set more off\n"
        "set linesize 255\n"
        "capture log close _all\n"
        f'log using "{stata_run.stata_quote(work / "stata.log")}", text replace\n'
        f'cd "{stata_run.stata_quote(work)}"\n'
        'capture noisily do "case.do"\n'
        "local qa_result = _rc\n"
        "display \"" + RC_SENTINEL + "\" `qa_result'\n"
        "log close _all\n"
        "exit, clear\n"
    )
    with open(wrapper, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return wrapper


def run_stata(dofile: str, *, executable: Path, timeout: float = 600.0) -> str:
    """Run one do-file in batch Stata and return the log text."""
    with tempfile.TemporaryDirectory(prefix="openstat-rcodes-") as tmp:
        work = Path(tmp)
        (work / "case.do").write_text(dofile, encoding="utf-8", newline="\n")
        _wrapper(work)
        process = subprocess.Popen(
            [str(executable), stata_run.batch_flag(), "do", str(work / "runner.do")],
            cwd=str(stata_run.stata_cwd(work)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError(f"Stata exceeded the {timeout:g}s timeout")
        log = work / "stata.log"
        if not log.exists():
            raise RuntimeError("Stata produced no log; check the executable and cwd")
        return log.read_text(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------
# do-files
# --------------------------------------------------------------------------
def sweep_dofile(lo: int = SWEEP_LO, hi: int = SWEEP_HI) -> str:
    """One do-file that raises every code in ``lo..hi`` via ``error``."""
    return (
        "set more off\n"
        "set linesize 255\n"
        'display "__SWEEP_START__"\n'
        f"forvalues n = {lo}/{hi} {{\n"
        '    display "__BEGIN__`n\'"\n'
        "    capture noisily error `n'\n"
        '    display "__END__`n\'=" _rc\n'
        "}\n"
        'display "__SWEEP_DONE__"\n'
    )


def trigger_dofile(triggers: Iterable[Trigger] = TRIGGERS) -> str:
    """One do-file that raises each trigger's code the way commands do."""
    lines = ["set more off", "set linesize 255", "sysuse auto, clear"]
    for trigger in triggers:
        lines.append(f'display "__T_BEGIN__{trigger.label}"')
        if trigger.reset:
            lines.append("capture quietly sysuse auto, clear")
        lines.extend(trigger.setup)
        lines.append(f"capture noisily {trigger.command}")
        lines.append(f'display "__T_END__{trigger.label}=" _rc')
    lines.append('display "__T_DONE__"')
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def _clean(lines: list[str]) -> tuple[str, int]:
    """Collapse a message block to one line; report how many lines it had.

    Inside a do-file Stata echoes every command (``. capture noisily zzr``
    and ``  2.     syntax varlist``), so echo lines are dropped before the
    message itself is read.
    """
    import re

    echo = re.compile(r"^\s*(?:\.\s|\d+\.\s)")
    kept = [line.strip() for line in lines
            if line.strip() and not echo.match(line)]
    if not kept:
        return "", 0
    text = kept[0]
    if len(kept) > 1:
        # Long `error` blocks (900-916) continue with help pointers; only the
        # first line is the message itself.
        return text, len(kept)
    return text, 1


def parse_sweep(log: str) -> dict[int, dict[str, Any]]:
    """``{code: {"text": ..., "extra_lines": n}}`` from a sweep log."""
    import re

    begin = re.compile(r"__BEGIN__(\d+)\s*$")
    end = re.compile(r"__END__(\d+)=(-?\d+)\s*$")
    found: dict[int, dict[str, Any]] = {}
    current: int | None = None
    buffer: list[str] = []
    for line in log.splitlines():
        match = begin.match(line)
        if match:
            current, buffer = int(match.group(1)), []
            continue
        match = end.match(line)
        if match:
            announced, seen = int(match.group(1)), int(match.group(2))
            if current == announced and seen == announced:
                text, extra = _clean(buffer)
                found[announced] = {"text": text, "extra_lines": max(0, extra - 1)}
            current, buffer = None, []
            continue
        if current is not None:
            buffer.append(line)
    return found


def parse_triggers(log: str) -> dict[str, tuple[int, str, int]]:
    """``{label: (rc, message, extra_lines)}`` from a trigger log."""
    import re

    begin = re.compile(r"__T_BEGIN__(\S+)\s*$")
    end = re.compile(r"__T_END__(\S+)=(-?\d+)\s*$")
    found: dict[str, tuple[int, str, int]] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in log.splitlines():
        match = begin.match(line)
        if match:
            current, buffer = match.group(1), []
            continue
        match = end.match(line)
        if match:
            label, rc = match.group(1), int(match.group(2))
            if current == label:
                text, extra = _clean(buffer)
                found[label] = (rc, text, max(0, extra - 1))
            current, buffer = None, []
            continue
        if current is not None:
            buffer.append(line)
    return found


def templatize(text: str) -> str:
    """Rewrite the probe tokens in a message as placeholders."""
    for literal, placeholder in ((TOKEN, PLACEHOLDER),
                                 ("OpenStatNoSuchDir", "<dir>")):
        text = text.replace(literal, placeholder)
    return text


# --------------------------------------------------------------------------
# building the catalogue
# --------------------------------------------------------------------------
def measure(executable: Path, *, log=print) -> dict[str, Any]:
    """Run both oracles and return the JSON payload."""
    log(f"stata: {executable}")
    started = time.perf_counter()
    log(f"sweeping error {SWEEP_LO}..{SWEEP_HI} ...")
    sweep = parse_sweep(run_stata(sweep_dofile(), executable=executable, timeout=900.0))
    log(f"  {sum(1 for v in sweep.values() if v['text'])} codes carry a message "
        f"({time.perf_counter() - started:.1f}s)")
    log(f"running {len(TRIGGERS)} command triggers ...")
    raised = parse_triggers(run_stata(trigger_dofile(), executable=executable, timeout=600.0))
    mismatched = [(label, trigger.code, raised.get(label, (None, "", 0))[0])
                  for label, trigger in ((t.label, t) for t in TRIGGERS)
                  if raised.get(label, (None, "", 0))[0] != trigger.code]
    for label, expected, seen in mismatched:
        log(f"  ! trigger {label}: expected r({expected}), got r({seen})")

    codes: dict[str, dict[str, Any]] = {}
    for code, entry in sorted(sweep.items()):
        if entry["text"]:
            record: dict[str, Any] = {"text": entry["text"],
                                      "source": "stata-19.5:error-command"}
            if entry["extra_lines"]:
                record["extra_lines"] = entry["extra_lines"]
            codes[str(code)] = record

    variants: dict[int, list[str]] = {}
    for trigger in TRIGGERS:
        rc, text, extra = raised.get(trigger.label, (None, "", 0))
        if rc != trigger.code or not text:
            continue
        template = templatize(text)
        variants.setdefault(trigger.code, [])
        if template not in variants[trigger.code]:
            variants[trigger.code] = variants[trigger.code] + [template]

    for code, texts in variants.items():
        key = str(code)
        record = codes.get(key, {})
        generic = record.get("text")
        record = {"text": texts[0], "source": "stata-19.5:command-trigger"}
        if generic and generic != texts[0]:
            record["generic"] = generic
        if len(texts) > 1:
            record["variants"] = texts
        codes[key] = record

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oracle": {"executable": str(executable), "version": "19.5"},
        "method": (
            "batch Stata, one do-file per oracle: (a) capture noisily error N for "
            f"every N in {SWEEP_LO}..{SWEEP_HI}; (b) capture noisily <command> for a "
            "curated set of command triggers. search rc and help are ignored in "
            "batch mode. Values are the text Stata prints above r(N); no StataCorp "
            "documentation was copied."
        ),
        "codes": codes,
        "counts": {"codes": len(codes),
                   "probed": SWEEP_HI - SWEEP_LO + 1,
                   "with_generic_message": sum(1 for v in sweep.values() if v["text"]),
                   "command_triggered": len(variants)},
        "coverage": {
            "probed_range": [SWEEP_LO, SWEEP_HI],
            "notes": [
                "Codes with an empty message under `error N` are omitted, not guessed.",
                "For parameterised codes a command-trigger entry overrides the "
                "generic `error N` text; `generic` keeps the fallback for comparison.",
                "<name> stands for the variable/command/matrix/file name Stata "
                "substitutes at run time.",
                "Codes above 9999 were probed once and carry no message; the "
                "sweep therefore stops there.",
                "Long messages (r(900)-r(916)) are truncated to their first line; "
                "`extra_lines` records how many lines were dropped.",
            ],
        },
    }
    if mismatched:
        payload["coverage"]["trigger_mismatches"] = [
            {"label": label, "expected": expected, "observed": seen}
            for label, expected, seen in mismatched]
    return payload


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------
def check(payload: dict[str, Any], executable: Path, *, log=print) -> int:
    """Re-measure a sample of codes and every trigger; return a failure count."""
    failures = 0
    strings = [code for code in CHECK_STRINGS if str(code) in payload["codes"]]
    log(f"spot-checking {len(strings)} codes through `error N` ...")
    sweep = parse_sweep(run_stata(sweep_dofile(min(strings), max(strings)),
                                  executable=executable, timeout=900.0))
    for code in strings:
        stored = payload["codes"][str(code)]
        generic = stored.get("generic", stored["text"])
        seen = sweep.get(code, {}).get("text", "")
        if seen != generic:
            failures += 1
            log(f"  ! r({code}): stored {generic!r} / measured {seen!r}")
        else:
            log(f"  ok r({code})")

    log(f"re-running {len(TRIGGERS)} command triggers ...")
    raised = parse_triggers(run_stata(trigger_dofile(), executable=executable, timeout=600.0))
    for trigger in TRIGGERS:
        rc, text, _extra = raised.get(trigger.label, (None, "", 0))
        stored = payload["codes"].get(str(trigger.code), {})
        expected = templatize(text)
        known = stored.get("variants", [stored.get("text")])
        if rc != trigger.code:
            failures += 1
            log(f"  ! {trigger.label}: expected r({trigger.code}), got r({rc})")
        elif expected and expected not in known:
            failures += 1
            log(f"  ! {trigger.label}: stored {known!r} / measured {expected!r}")
        else:
            log(f"  ok {trigger.label} r({rc}) {expected!r}")
    return failures


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def lookup(code: int, path: Path = DEFAULT_JSON) -> str | None:
    """The catalogued message for ``code``, or ``None`` when it is unknown.

    ``<name>``/``<dir>`` are placeholders for what Stata substitutes at run
    time (a variable, command, matrix, file or directory name).
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    entry = payload["codes"].get(str(code))
    return entry["text"] if entry else None


def write_payload(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False, sort_keys=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true",
                        help="re-measure every code and rewrite the JSON")
    parser.add_argument("--check", action="store_true",
                        help="re-measure a sample and every trigger, compare with the JSON")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON,
                        help=f"catalogue path (default {DEFAULT_JSON.name})")
    parser.add_argument("--stata", type=Path, help="Stata executable (STATA_EXE also works)")
    parser.add_argument("--show", type=int, nargs="+", metavar="CODE",
                        help="print the stored entry for these codes")
    args = parser.parse_args(argv)

    executable = args.stata or stata_run.detect_stata()
    if (args.refresh or args.check) and (executable is None or not executable.is_file()):
        parser.error("Stata executable unavailable; pass --stata PATH or set STATA_EXE")

    if args.show:
        payload = json.loads(args.json.read_text(encoding="utf-8"))
        for code in args.show:
            entry = payload["codes"].get(str(code))
            print(f"r({code}): {entry}" if entry else f"r({code}): (not catalogued)")
        return 0

    if args.refresh:
        payload = measure(executable, log=lambda *a: print(*a, flush=True))
        write_payload(payload, args.json)
        counts = payload["counts"]
        print(f"wrote {args.json}: {counts['codes']} codes "
              f"({counts['command_triggered']} command-verified)")
        if payload.get("coverage", {}).get("trigger_mismatches"):
            print("trigger mismatches recorded; see the JSON coverage block")
            return 1
        return 0

    if args.check:
        payload = json.loads(args.json.read_text(encoding="utf-8"))
        failures = check(payload, executable, log=lambda *a: print(*a, flush=True))
        print(f"{failures} mismatch(es)" if failures else "all checks passed")
        return 1 if failures else 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
