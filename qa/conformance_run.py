"""Conformance harness: run the same do-file in OpenStat and (optionally) Stata.

Unlike ``qa/run_differential.py`` this tool is developer-facing: it can diff
the two log transcripts line by line, which is what makes it useful while
closing a gap. It never mutates user data and each engine gets its own
temporary working directory.

The harness is cross-platform.  The licensed Stata executable is detected per
platform (``STATA_EXE`` first, then the usual install locations) and launched
with the batch flag that platform expects: ``/e`` on Windows, ``-b`` on
macOS.  The wrapper do-file is always written UTF-8 *without* a BOM and with
forward-slash paths, which is what ``log using`` expects everywhere.

    python qa/conformance_run.py --list
    python qa/conformance_run.py --case c01_language
    python qa/conformance_run.py --case c01_language --json out.json
    python qa/conformance_run.py --all --jobs 4
    python qa/conformance_run.py --all --diff
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = Path(__file__).resolve().parent / "conformance"

#: Bumped whenever the ``--json`` payload changes shape.
SCHEMA_VERSION = 1

#: How many lines of the transcript diff are kept in the ``--json`` payload.
DIFF_LINE_LIMIT = 200

#: Relative tolerance used when comparing the ``__OPENSTAT__`` sentinels.
REL_TOLERANCE = 1e-8
ABS_TOLERANCE = 1e-9

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

#: macOS keeps the application bundle here; the harness must also *start*
#: Stata with this directory as the working directory on macOS.
MACOS_STATA_ROOT = Path("/Applications/StataNow")
MACOS_STATA_EXE = MACOS_STATA_ROOT / "StataSE.app/Contents/MacOS/stata-se"

SENTINEL = re.compile(r"^\s*__OPENSTAT__(\w+)\s*=\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?|\.)\s*$")
RC_SENTINEL = re.compile(r"^\s*__OPENSTAT_RETURN_CODE__\s*=\s*(\d+)\s*$")


def cases() -> list[str]:
    """The available conformance case names (``qa/conformance/*.do``)."""
    return sorted(path.stem for path in CASE_DIR.glob("*.do"))


def parse_scalars(output: str) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for line in output.splitlines():
        match = SENTINEL.match(line)
        if match:
            values[match[1]] = None if match[2] == "." else float(match[2])
    return values


def stata_candidates() -> list[Path]:
    """Every place a licensed Stata executable may live, most specific first.

    ``STATA_EXE`` always wins; the rest are the platform defaults.
    """
    candidates: list[Path] = []
    env = os.environ.get("STATA_EXE")
    if env:
        candidates.append(Path(env))
    if IS_WINDOWS:
        bases = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for base in bases:
            candidates.append(Path(base) / "StataNow19" / "StataMP-64.exe")
            candidates.append(Path(base) / "Stata19" / "StataMP-64.exe")
    elif IS_MACOS:
        candidates.append(MACOS_STATA_EXE)
        candidates.append(Path("/Applications/StataNow/StataMP.app/Contents/MacOS/stata-mp"))
    else:  # Linux and friends: the usual manual install locations.
        candidates.append(Path("/usr/local/stata/stata-mp"))
        candidates.append(Path("/usr/local/stata/stata-se"))
        candidates.append(Path("/usr/local/stata/stata-ic"))
    return candidates


def detect_stata() -> Path | None:
    """The first existing Stata executable, or ``None``."""
    for candidate in stata_candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def batch_flag() -> str:
    """The batch flag the installed Stata understands on this platform."""
    return "/e" if IS_WINDOWS else "-b"


def stata_cwd(executable: Path, work: Path) -> Path:
    """The directory Stata is started from.

    macOS wants the application root (``/Applications/StataNow``), which is
    what Stata 19.5 SE expects.  On Windows the install root under
    ``Program Files`` is not writable by a normal user and Stata then exits
    without running anything, so the case's own scratch directory is used
    instead (the wrapper ``cd``s there anyway).
    """
    if IS_MACOS:
        return MACOS_STATA_ROOT
    return work


def stata_quote(path: Path | str) -> str:
    """Stata wants forward slashes and no embedded double quotes."""
    return str(path).replace("\\", "/").replace('"', "")


def write_wrapper(work: Path) -> Path:
    """Write the wrapper do-file: UTF-8, no BOM, forward-slash paths."""
    wrapper = work / "runner.do"
    text = (
        "set more off\n"
        "set linesize 255\n"
        "capture log close _all\n"
        f'log using "{stata_quote(work / "stata.log")}", text replace\n'
        f'cd "{stata_quote(work)}"\n'
        'capture noisily do "case.do"\n'
        "local qa_result = _rc\n"
        "display \"__OPENSTAT_RETURN_CODE__=\" `qa_result'\n"
        "log close _all\n"
        "exit, clear\n"
    )
    with open(wrapper, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return wrapper

def stata_run(source: Path, work: Path, executable: Path, timeout: float = 60.0) -> dict[str, Any]:
    """Run one do-file in licensed Stata and return the log plus sentinels."""
    shutil.copyfile(source, work / "case.do")
    wrapper = write_wrapper(work)
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            [str(executable), batch_flag(), "do", str(wrapper)],
            cwd=str(stata_cwd(executable, work)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        return {"status": "error", "rc": None, "output": "", "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "error": f"could not launch Stata: {error}"}
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    elapsed = round(time.perf_counter() - started, 4)
    log = work / "stata.log"
    output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    codes = [int(match[1]) for match in (RC_SENTINEL.match(line) for line in output.splitlines()) if match]
    if timed_out:
        status = "timeout"
        error = f"Stata exceeded the {timeout:g}s timeout and was stopped."
    elif not codes:
        status = "missing_result"
        error = "Stata produced no completion sentinel; check the installation and licence."
    else:
        status = "completed"
        error = None
    return {"status": status, "rc": codes[-1] if codes else None, "seconds": elapsed,
            "output": output, "scalars": parse_scalars(output), "error": error}


def openstat_run(source: Path, work: Path, timeout: float = 60.0) -> dict[str, Any]:
    """Run one do-file in OpenStat and return the log plus sentinels."""
    report = work / "result.json"
    started = time.perf_counter()
    try:
        subprocess.run(
            [sys.executable, "-m", "openstat", "--batch", str(source), "--cwd", str(work),
             "--json", str(report)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "rc": None, "output": "", "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "error": f"OpenStat exceeded the {timeout:g}s timeout and was stopped."}
    except OSError as error:
        return {"status": "error", "rc": None, "output": "", "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "error": f"could not launch OpenStat: {error}"}
    if not report.is_file():
        return {"status": "missing_result", "rc": None, "output": "", "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "error": "OpenStat wrote no report file."}
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return {"status": "error", "rc": None, "output": "", "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "error": f"unreadable OpenStat report: {error}"}
    output = payload.get("output", "")
    return {"status": "completed", "rc": int(payload["rc"]),
            "seconds": round(time.perf_counter() - started, 4),
            "output": output, "scalars": parse_scalars(output), "error": None}


def normalize(text: str) -> list[str]:
    """Reduce a Stata/OpenStat transcript to comparable content lines."""
    keep = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("."):
            # Echoed command; keep only the command word so harmless formatting
            # differences do not dominate the diff.
            body = stripped[1:].strip()
            if body.startswith("*") or body.startswith("/*") or body.startswith(">"):
                continue
            keep.append("$ " + body.split()[0] if body else "$")
            continue
        keep.append(stripped)
    return keep


def diff_texts(left: str, right: str) -> str:
    a = normalize(left)
    b = normalize(right)
    return "\n".join(difflib.unified_diff(a, b, "stata", "openstat", lineterm="", n=1))


def close_number(left: float | None, right: float | None) -> bool:
    """Do two sentinel values agree within the harness tolerance?"""
    if left is None or right is None:
        return left is right
    return abs(left - right) <= max(REL_TOLERANCE * max(1.0, abs(left)), ABS_TOLERANCE)

def _blank_result(name: str, status: str, problems: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "case": name,
        "script": f"qa/conformance/{name}.do",
        "status": status,
        "rc": {"stata": None, "openstat": None},
        "rc_differ": False,
        "scalars": {"stata": {}, "openstat": {}},
        "scalar_mismatches": [],
        "diff": [],
        "diff_line_count": 0,
        "duration_seconds": 0.0,
        "problems": problems,
        "stata": {"status": status, "seconds": 0.0, "error": "; ".join(problems) or None},
        "openstat": {"status": status, "seconds": 0.0, "error": "; ".join(problems) or None},
    }


def run_case(name: str, executable: Path, timeout: float = 60.0,
              diff_lines: int = DIFF_LINE_LIMIT) -> dict[str, Any]:
    """Run one conformance case in both engines and compare the sentinels."""
    source = CASE_DIR / f"{name}.do"
    if not source.is_file():
        return _blank_result(name, "missing", [f"case file {source} does not exist"])

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="openstat-conf-") as temporary:
        work = Path(temporary)
        stata_dir, openstat_dir = work / "stata", work / "openstat"
        stata_dir.mkdir()
        openstat_dir.mkdir()
        reference = stata_run(source, stata_dir, executable, timeout)
        actual = openstat_run(source, openstat_dir, timeout)
    duration = round(time.perf_counter() - started, 4)

    problems: list[str] = []
    if reference["status"] != "completed":
        problems.append(f"stata {reference['status']}: {reference['error']}")
    if actual["status"] != "completed":
        problems.append(f"openstat {actual['status']}: {actual['error']}")

    rc_differ = reference["rc"] != actual["rc"]
    if reference["status"] == "completed" and actual["status"] == "completed" and rc_differ:
        problems.append(f"rc stata={reference['rc']} openstat={actual['rc']}")

    mismatches: list[dict[str, Any]] = []
    for key in sorted(set(reference["scalars"]) | set(actual["scalars"])):
        expected, observed = reference["scalars"].get(key), actual["scalars"].get(key)
        if not close_number(expected, observed):
            mismatches.append({"name": key, "stata": expected, "openstat": observed})
            problems.append(f"scalar {key}: stata={expected} openstat={observed}")

    if problems and (reference["status"] != "completed" or actual["status"] != "completed"):
        status = "error"
    else:
        status = "failed" if problems else "passed"

    diff = diff_texts(reference["output"], actual["output"]).splitlines()
    kept = diff[:max(0, diff_lines)]

    return {
        "name": name,
        "case": name,
        "script": f"qa/conformance/{name}.do",
        "status": status,
        "rc": {"stata": reference["rc"], "openstat": actual["rc"]},
        "rc_differ": bool(rc_differ),
        "scalars": {"stata": reference["scalars"], "openstat": actual["scalars"]},
        "scalar_mismatches": mismatches,
        "diff": kept,
        "diff_line_count": len(kept),
        "duration_seconds": duration,
        "problems": problems,
        "stata": {"status": reference["status"], "seconds": reference["seconds"],
                  "error": reference["error"]},
        "openstat": {"status": actual["status"], "seconds": actual["seconds"],
                     "error": actual["error"]},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", dest="names",
                        help="conformance case name (repeatable)")
    parser.add_argument("--all", action="store_true", help="run every case in qa/conformance")
    parser.add_argument("--list", action="store_true",
                        help="print the available case names and exit")
    parser.add_argument("--diff", action="store_true", help="print a transcript diff")
    parser.add_argument("--always", action="store_true",
                        help="print the transcript diff even when the case passes")
    parser.add_argument("--json", type=Path, help="write a machine-readable result file")
    parser.add_argument("--jobs", type=int, default=1,
                        help="run up to N cases concurrently (default 1)")
    parser.add_argument("--diff-lines", type=int, default=DIFF_LINE_LIMIT,
                        help=f"how many diff lines to keep in the JSON payload (default {DIFF_LINE_LIMIT})")
    parser.add_argument("--timeout", type=float, default=60.0,
                        help="per-engine timeout in seconds (default 60)")
    parser.add_argument("--stata", type=Path,
                        help="licensed Stata executable; STATA_EXE is also supported")
    args = parser.parse_args(argv)

    if args.list:
        for name in cases():
            print(name)
        return 0

    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.diff_lines < 0:
        parser.error("--diff-lines must not be negative")

    selected = cases() if args.all else list(args.names or [])
    if not selected:
        parser.error("use --case NAME or --all (or --list)")

    executable = Path(args.stata) if args.stata else detect_stata()
    if executable is None or not executable.is_file():
        parser.error("Stata executable unavailable; pass --stata PATH or set STATA_EXE")

    workers = max(1, args.jobs)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_case, name, executable, args.timeout, args.diff_lines): name
            for name in selected
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as error:  # a harness bug must not lose the other cases
                results[name] = _blank_result(name, "error", [f"{type(error).__name__}: {error}"])

    failures = 0
    for name in selected:
        result = results.get(name) or _blank_result(name, "error", ["case did not run"])
        if result["status"] != "passed":
            failures += 1
        print(f"{name}: {result['status']}  rc={result['rc']['openstat']}  "
              + "; ".join(result["problems"]))
        if args.always or (args.diff and result["problems"]):
            print("\n".join(result["diff"]))

    if args.json:
        summary = {
            state: sum(1 for name in selected
                       if (results.get(name) or {}).get("status") == state)
            for state in ("passed", "failed", "error", "missing")
        }
        summary["total"] = len(selected)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "tool": "qa/conformance_run.py",
            "platform": sys.platform,
            "platform_name": platform.platform(),
            "python": platform.python_version(),
            "stata_executable": stata_quote(executable),
            "stata_batch_flag": batch_flag(),
            "timeout_seconds": args.timeout,
            "jobs": workers,
            "numeric_tolerance": {"relative": REL_TOLERANCE, "absolute": ABS_TOLERANCE},
            "summary": summary,
            "cases": [results.get(name) or _blank_result(name, "error", ["case did not run"])
                      for name in selected],
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())