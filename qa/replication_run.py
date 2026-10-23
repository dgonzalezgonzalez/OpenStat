"""Replication-package validation harness.

Runs the Stata code shipped in published replication packages through both
Stata (the oracle) and OpenStat, and reports where the two disagree.

Published packages are the harshest test available: their code was written
by economists against real Stata, and it uses command combinations nobody
writes by hand.  Packages from the AEA and the Journal of Political Economy
are the focus, because those are the venues the corpus was requested from.

The harness never mutates a package.  It clones the package into a cache
directory, copies what a case needs into a fresh working directory for each
engine, and compares the return codes and the ``__OPENSTAT__`` sentinels.

    python qa/replication_run.py --list
    python qa/replication_run.py --case dropout_jpe_descriptives --keep

A case that needs data the package does not ship can declare a ``prepare``
step: a do-file that runs in Stata once and writes the reduced dataset that
the case then uses.  That keeps third-party data out of this repository
while still exercising the package's own code.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# The oracle launcher is shared with ``qa/stata_run.py`` so that Windows and
# macOS find their own installation instead of a hard-coded path.
from stata_run import batch_flag, detect_stata, stata_cwd, stata_quote  # noqa: E402

ROOT = HERE.parent
CACHE = ROOT / "qa" / "replication" / "_packages"
STATA_EXE = detect_stata()

SENTINEL = re.compile(
    r"^\s*__OPENSTAT__(\w+)\s*=\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?|\.)\s*$")
RC_SENTINEL = re.compile(r"^\s*__OPENSTAT_RETURN_CODE__\s*=\s*(\d+)\s*$")
ERROR = re.compile(r"^r\((\d+)\);", re.MULTILINE)


def parse_scalars(output: str) -> dict[str, float | None]:
    """Collect the ``__OPENSTAT__`` sentinel values from a transcript."""
    values: dict[str, float | None] = {}
    for line in output.splitlines():
        match = SENTINEL.match(line)
        if match:
            values[match[1]] = None if match[2] == "." else float(match[2])
    return values


# ----------------------------------------------------------------------
# Package management
# ----------------------------------------------------------------------
def fetch_package(spec: dict) -> Path:
    """Clone or update a replication package in the local cache."""
    url = spec["url"]
    directory = CACHE / spec.get("name", Path(url).stem)
    if not (directory / ".git").is_dir():
        directory.parent.mkdir(parents=True, exist_ok=True)
        if directory.exists():
            shutil.rmtree(directory)
        subprocess.run(["git", "clone", "--depth", "1", url, str(directory)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return directory


# ----------------------------------------------------------------------
# Engine drivers
# ----------------------------------------------------------------------
def _stata_wrapper(work: Path, do_file: Path, env: dict | None = None) -> Path:
    """Write the wrapper that runs a do-file and records its return code.

    The return code goes to a separate file as well as to the log, because a
    do-file is free to close the log (the published packages do) and that
    must not hide the result.
    """
    wrapper = work / "runner.do"
    prologue = ""
    for key, value in (env or {}).items():
        prologue += f'global {key} "{value}"\n'
    log_path = stata_quote(work / "stata.log")
    work_path = stata_quote(work)
    result_path = stata_quote(work / "stata_result.txt")
    wrapper.write_text(
        "set more off\nset linesize 255\n"
        "capture log close _all\n"
        f'log using "{log_path}", text replace name(harness)\n'
        f'cd "{work_path}"\n'
        f"{prologue}"
        f'capture noisily do "{stata_quote(do_file)}"\n'
        "local qa_result = _rc\n"
        "display \"__OPENSTAT_RETURN_CODE__=\" `qa_result'\n"
        f'file open _qa using "{result_path}", write replace text\n'
        "file write _qa \"__OPENSTAT_RETURN_CODE__=\" `qa_result'\n"
        "file close _qa\n"
        "capture log close _all\n"
        "exit, clear\n", encoding="utf-8", newline="\n")
    return wrapper


def stata_run(do_file: Path, work: Path, timeout: float = 1800.0,
              env: dict | None = None) -> dict:
    """Run a do-file in the licensed Stata 19.5 installation."""
    if STATA_EXE is None:
        raise RuntimeError("no Stata installation found; set STATA_EXE")
    wrapper = _stata_wrapper(work, do_file, env)
    started = time.perf_counter()
    process = subprocess.Popen([str(STATA_EXE), batch_flag(), "do", str(wrapper)],
                               cwd=str(stata_cwd(work)),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    log = work / "stata.log"
    output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    codes = [int(m[1]) for m in (RC_SENTINEL.match(l) for l in output.splitlines()) if m]
    marker = work / "stata_result.txt"
    if marker.is_file():
        recorded = marker.read_text(encoding="utf-8", errors="replace")
        codes += [int(m[1]) for m in (RC_SENTINEL.match(l) for l in recorded.splitlines()) if m]
    return {
        "rc": codes[-1] if codes else None,
        "seconds": round(time.perf_counter() - started, 2),
        "output": output,
        "scalars": parse_scalars(output),
    }


def openstat_run(do_file: Path, work: Path, timeout: float = 1800.0) -> dict:
    """Run a do-file in OpenStat and collect the same information."""
    report = work / "result.json"
    started = time.perf_counter()
    try:
        subprocess.run(
            [sys.executable, "-m", "openstat", "--batch", str(do_file), "--cwd", str(work),
             "--json", str(report)],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"rc": None, "output": "", "scalars": {}, "error": "timeout"}
    if not report.is_file():
        return {"rc": None, "output": "", "scalars": {}, "error": "no report"}
    payload = json.loads(report.read_text(encoding="utf-8"))
    return {
        "rc": int(payload["rc"]),
        "seconds": round(time.perf_counter() - started, 2),
        "output": payload["output"],
        "scalars": parse_scalars(payload["output"]),
        "error": None,
    }


def first_error(output: str) -> str | None:
    """Return the first ``r(nnn);`` error in a transcript, if any."""
    match = ERROR.search(output)
    return match[0] if match else None


def compare(reference: dict, actual: dict) -> list[str]:
    """Compare two engine transcripts and return the differences."""
    problems = []
    if reference["rc"] != actual["rc"]:
        problems.append(f"rc stata={reference['rc']} openstat={actual['rc']}")
    for key in sorted(set(reference["scalars"]) | set(actual["scalars"])):
        expected = reference["scalars"].get(key)
        observed = actual["scalars"].get(key)
        if expected != observed:
            if (expected is None or observed is None
                    or abs(expected - observed) > 1e-8 * max(1.0, abs(expected))):
                problems.append(f"scalar {key}: stata={expected} openstat={observed}")
    return problems


# ----------------------------------------------------------------------
# Case execution
# ----------------------------------------------------------------------
def _stage(case: dict, package: Path, directory: Path) -> None:
    """Copy the case's files and the package's inputs into a work dir."""
    for pattern in case.get("copy", []):
        for item in package.glob(pattern):
            if item.is_dir():
                shutil.copytree(item, directory / item.name, dirs_exist_ok=True)
            else:
                shutil.copy2(item, directory / item.name)
    for name in case.get("files", []):
        source = ROOT / "qa" / "replication" / "cases" / case["name"] / name
        if source.is_file():
            shutil.copy2(source, directory / name)
    for name in case.get("outputs", []):
        source = package / name
        if source.is_file():
            shutil.copy2(source, directory / Path(name).name)


def _prepare(case: dict, package: Path, directory: Path) -> None:
    """Build a reduced dataset for a case that needs one."""
    prepare = case.get("prepare")
    if not prepare:
        return
    script = ROOT / "qa" / "replication" / "cases" / case["name"] / prepare
    if not script.is_file():
        return
    shutil.copy2(script, directory / Path(prepare).name)
    # The prepare script may need the package's own files, so it runs in a
    # directory that can reach them.
    result = stata_run(directory / Path(prepare).name, directory,
                       env={"pkg_root": str(package)})
    if result["rc"] not in (0, None):
        raise RuntimeError(f"prepare step failed with r({result['rc']})")


def run_case(case: dict, keep: bool = False) -> dict:
    """Run one replication case in both engines and compare the results."""
    package = fetch_package(case["package"])
    results: dict = {"name": case["name"], "package": str(package)}
    with tempfile.TemporaryDirectory(prefix="openstat-repl-") as temporary:
        work = Path(temporary)
        stata_dir, openstat_dir = work / "stata", work / "openstat"
        for directory in (stata_dir, openstat_dir):
            directory.mkdir()
            _stage(case, package, directory)
        if case.get("prepare"):
            _prepare(case, package, stata_dir)
            shutil.copytree(stata_dir, openstat_dir, dirs_exist_ok=True)
        do_name = case.get("do_file", "case.do")
        reference = stata_run(stata_dir / do_name, stata_dir)
        actual = openstat_run(openstat_dir / do_name, openstat_dir)
        problems = compare(reference, actual)
        results.update({
            "problems": problems,
            "stata_rc": reference["rc"],
            "openstat_rc": actual["rc"],
            "stata_seconds": reference["seconds"],
            "openstat_seconds": actual["seconds"],
            "stata_error": first_error(reference["output"]),
            "openstat_error": first_error(actual["output"]),
            "status": "PASS" if not problems else "FAIL",
        })
        if keep:
            target = ROOT / "qa" / "replication" / "_artifacts" / case["name"]
            target.mkdir(parents=True, exist_ok=True)
            (target / "stata.log").write_text(reference["output"], encoding="utf-8")
            (target / "openstat.log").write_text(actual["output"], encoding="utf-8")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path,
                        help="JSON file describing the cases to run")
    parser.add_argument("--case", action="append", dest="names",
                        help="run only the named case")
    parser.add_argument("--list", action="store_true", help="list the known cases")
    parser.add_argument("--keep", action="store_true", help="save both transcripts")
    parser.add_argument("--json", type=Path, help="write the results as JSON")
    args = parser.parse_args(argv)

    manifest_path = args.manifest or (ROOT / "qa" / "replication" / "manifest.json")
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    if args.list:
        for case in cases:
            print(f"{case['name']}: {case.get('description', '')}")
        return 0
    if args.names:
        cases = [case for case in cases if case["name"] in args.names]
        if not cases:
            print("no matching case", file=sys.stderr)
            return 2
    if STATA_EXE is None:
        print("no Stata installation found; set STATA_EXE to the oracle executable",
              file=sys.stderr)
        return 2
    print(f"oracle: {STATA_EXE}")
    failures = 0
    report = []
    for case in cases:
        try:
            result = run_case(case, keep=args.keep)
        except Exception as error:  # pragma: no cover - developer feedback
            print(f"{case['name']}: ERROR  {error}")
            failures += 1
            continue
        report.append(result)
        problems = result["problems"]
        status = result["status"]
        if status == "FAIL":
            failures += 1
        print(f"{result['name']}: {status}  stata_rc={result['stata_rc']} "
              f"openstat_rc={result['openstat_rc']}  "
              f"({result['stata_seconds']}s / {result['openstat_seconds']}s)")
        for problem in problems:
            print(f"    {problem}")
        if result["openstat_error"] and not result["stata_error"]:
            print(f"    openstat stopped with {result['openstat_error']}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())
