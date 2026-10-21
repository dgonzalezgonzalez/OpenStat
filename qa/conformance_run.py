"""Conformance harness: run the same do-file in OpenStat and (optionally) Stata.

Unlike ``qa/run_differential.py`` this tool is developer-facing: it can diff
the two log transcripts line by line, which is what makes it useful while
closing a gap. It never mutates user data and each engine gets its own
temporary working directory.

    python qa/conformance_run.py --case c01_language
    python qa/conformance_run.py --all --diff
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = Path(__file__).resolve().parent / "conformance"
STATA_ROOT = Path("/Applications/StataNow")
STATA_EXE = STATA_ROOT / "StataSE.app/Contents/MacOS/stata-se"

SENTINEL = re.compile(r"^\s*__OPENSTAT__(\w+)\s*=\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?|\.)\s*$")
RC_SENTINEL = re.compile(r"^\s*__OPENSTAT_RETURN_CODE__\s*=\s*(\d+)\s*$")


def cases() -> list[str]:
    return sorted(path.stem for path in CASE_DIR.glob("*.do"))


def parse_scalars(output: str) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for line in output.splitlines():
        match = SENTINEL.match(line)
        if match:
            values[match[1]] = None if match[2] == "." else float(match[2])
    return values


def stata_run(source: Path, work: Path, timeout: float = 60.0) -> dict:
    shutil.copyfile(source, work / "case.do")
    wrapper = work / "runner.do"
    wrapper.write_text(
        "set more off\nset linesize 255\n"
        "capture log close _all\n"
        f'log using "{work / "stata.log"}", text replace\n'
        f'cd "{work}"\n'
        'capture noisily do "case.do"\n'
        "local qa_result = _rc\n"
        "display \"__OPENSTAT_RETURN_CODE__=\" `qa_result'\n"
        "log close _all\n"
        "exit, clear\n", encoding="utf-8")
    started = time.perf_counter()
    process = subprocess.Popen([str(STATA_EXE), "-b", "do", str(wrapper)], cwd=STATA_ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    log = work / "stata.log"
    output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    codes = [int(m[1]) for m in (RC_SENTINEL.match(l) for l in output.splitlines()) if m]
    return {
        "rc": codes[-1] if codes else None,
        "seconds": round(time.perf_counter() - started, 2),
        "output": output,
        "scalars": parse_scalars(output),
    }


def openstat_run(source: Path, work: Path, timeout: float = 60.0) -> dict:
    report = work / "result.json"
    started = time.perf_counter()
    try:
        subprocess.run(
            [sys.executable, "-m", "openstat", "--batch", str(source), "--cwd", str(work),
             "--json", str(report)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": None, "output": "", "scalars": {}, "error": "timeout"}
    if not report.is_file():
        return {"rc": None, "output": "", "scalars": {}, "error": "no report"}
    import json
    payload = json.loads(report.read_text(encoding="utf-8"))
    return {
        "rc": int(payload["rc"]),
        "seconds": round(time.perf_counter() - started, 2),
        "output": payload["output"],
        "scalars": parse_scalars(payload["output"]),
        "error": None,
    }


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="names")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--diff", action="store_true", help="print a transcript diff")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args(argv)
    selected = cases() if args.all else (args.names or [])
    if not selected:
        parser.error("use --case NAME or --all")
    failures = 0
    for name in selected:
        source = CASE_DIR / f"{name}.do"
        if not source.is_file():
            print(f"{name}: MISSING")
            failures += 1
            continue
        with tempfile.TemporaryDirectory(prefix="openstat-conf-") as temporary:
            work = Path(temporary)
            stata_dir, openstat_dir = work / "stata", work / "openstat"
            stata_dir.mkdir()
            openstat_dir.mkdir()
            reference = stata_run(source, stata_dir, args.timeout)
            actual = openstat_run(source, openstat_dir, args.timeout)
        problems = []
        if reference["rc"] != actual["rc"]:
            problems.append(f"rc stata={reference['rc']} openstat={actual['rc']}")
        for key in sorted(set(reference["scalars"]) | set(actual["scalars"])):
            expected, observed = reference["scalars"].get(key), actual["scalars"].get(key)
            if expected != observed:
                if expected is None or observed is None or abs(expected - observed) > 1e-8 * max(1.0, abs(expected)):
                    problems.append(f"scalar {key}: stata={expected} openstat={observed}")
        status = "PASS" if not problems else "FAIL"
        if problems:
            failures += 1
        print(f"{name}: {status}  rc={actual['rc']}  " + ("; ".join(problems)))
        if args.diff and (problems or "--always" in sys.argv):
            print(diff_texts(reference["output"], actual["output"]))
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
