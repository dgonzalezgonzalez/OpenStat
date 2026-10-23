"""Run short synthetic do-files in OpenStat and, optionally, licensed Stata.

No personal data or installed ado files are read. Each engine receives its own
temporary directory. Stata is a separately launched process; a timeout stops only
that child process. Network access is disabled unless --online-ssc is supplied.

The corpus is discovered from ``qa/cases/*.do``; ``CASE_METADATA`` only adds the
human-readable feature list and the artifacts a case is expected to produce.
A case that exists on disk but has no metadata still runs (its features default
to its own name), so adding a fixture needs no edit here.

    python qa/run_differential.py --list
    python qa/run_differential.py --all
    python qa/run_differential.py --case data --case regression
    python qa/run_differential.py --all --jobs 2 --json qa/report.json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = Path(__file__).resolve().parent / "cases"
REPORT_PATH = ROOT / "qa" / "report.json"
SENTINEL = re.compile(r"^\s*__OPENSTAT__(\w+)\s*=\s*([+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?|\.)\s*$")
RC_SENTINEL = re.compile(r"^\s*__OPENSTAT_RETURN_CODE__\s*=\s*(\d+)\s*$")
#: Any absolute Windows/POSIX path, used to keep local paths out of the report.
ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)[^\s\"'|]*")

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
MACOS_STATA_ROOT = Path("/Applications/StataNow")

#: Never run more than this many licensed-Stata processes at once.
MAX_STATA_PROCESSES = 4


@dataclass(frozen=True)
class Case:
    name: str
    features: tuple[str, ...]
    expected_rc: int = 0
    csv_file: str | None = None
    png_file: str | None = None
    dta_file: str | None = None
    online: bool = False


#: Explicit metadata per case. Discovery supplies everything else.
CASE_METADATA: dict[str, Case] = {
    case.name: case
    for case in (
        Case("data", ("generate", "replace", "missing values", "bysort", "egen mean", "sort", "summarize", "count", "keep/drop", "CSV export"), csv_file="data.csv"),
        Case("macros", ("locals", "globals", "forvalues", "foreach", "program args", "rclass", "capture", "_rc")),
        Case("regression", ("OLS coefficients", "OLS standard errors", "e(N)", "e(df_r)", "e(r2)")),
        Case("robust", ("OLS HC1 robust standard errors", "OLS clustered standard errors", "cluster inference degrees of freedom", "OLS coefficients", "stored results")),
        Case("logistic", ("logistic log-odds coefficients", "logistic standard errors", "log likelihood")),
        Case("semantics", ("recursive replace", "numeric logical values", "inrange missing bounds", "qualified cumulative sum", "scalar cumulative sum", "programming if first observation", "while macro expansion", "program exit 0")),
        Case("self_reference", ("generate rejects reference to nonexistent variable",), expected_rc=111),
        Case("dta", ("DTA 118 write/read", "variable labels", "value labels"), csv_file="roundtrip.csv", dta_file="roundtrip.dta"),
        Case("preserve", ("preserve", "restore")),
        Case("merge", ("merge 1:1", "merge result indicator"), csv_file="merged.csv"),
        Case("append", ("append",), csv_file="appended.csv"),
        Case("reshape", ("reshape long", "reshape wide"), csv_file="reshaped.csv"),
        Case("mata", ("Mata matrix literals", "Mata transpose", "Mata product", "st_matrix")),
        Case("graph", ("scatter", "PNG export"), png_file="scatter.png"),
        Case("unsupported", ("unknown command fails explicitly",), expected_rc=199),
        Case("invalid_option", ("unknown option fails explicitly",), expected_rc=198),
        Case("ssc", ("online SSC install", "which"), online=True),
        Case("factors_weights", ("factor variables i.g##c.x", "analytic weights [aw]", "frequency weights [fw]", "sampling weights [pw]", "robust standard errors with fweights")),
        Case("instrumental", ("ivregress 2sls", "robust IV standard errors", "small vce(cluster) IV", "predict residuals")),
        Case("panel", ("xtset", "lag operator L.", "areg absorb", "areg vce(cluster)", "xtreg fe", "xtreg fe vce(cluster)", "within r2")),
        Case("hdfe", ("reghdfe two-way absorb", "reghdfe weights", "reghdfe vce(robust)", "reghdfe vce(cluster)"), expected_rc=0),
    )
}


def discovered_names() -> list[str]:
    """Every case file on disk, sorted."""
    return sorted(path.stem for path in CASE_DIR.glob("*.do"))


def cases() -> list[Case]:
    """The corpus: discovered files merged with the explicit metadata."""
    return [CASE_METADATA.get(name) or Case(name, (name,)) for name in discovered_names()]


def unregistered_names() -> list[str]:
    """Case files that exist but carry no explicit metadata."""
    return [name for name in discovered_names() if name not in CASE_METADATA]


#: Backwards-compatible name: the corpus as discovered at import time.
#: ``qa/cases/*.do`` is the source of truth, not this tuple.
CASES: tuple[Case, ...] = tuple(cases())


def parse_scalars(output: str) -> dict[str, float | None]:
    scalars: dict[str, float | None] = {}
    for line in output.splitlines():
        match = SENTINEL.match(line)
        if match:
            scalars[match[1]] = None if match[2] == "." else float(match[2])
    return scalars


def default_stata() -> Path | None:
    if os.environ.get("STATA_EXE"):
        return Path(os.environ["STATA_EXE"])
    candidates = (
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "StataNow19" / "StataMP-64.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Stata19" / "StataMP-64.exe",
    )
    return next((path for path in candidates if path.is_file()), None)


def batch_flag() -> str:
    """The batch flag the installed Stata expects on this platform."""
    return "/e" if IS_WINDOWS else "-b"


def stata_cwd(directory: Path) -> Path:
    """Where Stata is started from.

    macOS wants the application root; Windows must not be started inside the
    read-only ``Program Files`` install root, so the case's own scratch
    directory is used (the wrapper ``cd``s there anyway).
    """
    return MACOS_STATA_ROOT if IS_MACOS else directory


def _stata_quote(path: Path) -> str:
    return str(path).replace("\\", "/").replace('"', "")


def sanitize(text: str, limit: int = 240) -> str:
    """Drop absolute local paths and cap the length before publishing text."""
    collapsed = " ".join(str(text).replace("\\", "/").split())
    collapsed = ABSOLUTE_PATH.sub("<path>", collapsed)
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def run_stata(executable: Path, case: Case, directory: Path, timeout: float) -> dict[str, Any]:
    source = CASE_DIR / f"{case.name}.do"
    shutil.copyfile(source, directory / "case.do")
    if case.online:
        (directory / "plus").mkdir()
    wrapper = directory / "runner.do"
    with open(wrapper, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(
            'set more off\nset linesize 255\n'
            'capture log close _all\n'
            f'log using "{_stata_quote(directory / "stata.log")}", text replace\n'
            'about\n'
            f'cd "{_stata_quote(directory)}"\n'
            'capture noisily do "case.do"\n'
            'local qa_result = _rc\n'
            'display "__OPENSTAT_RETURN_CODE__=" `qa_result\'\n'
            'log close _all\n'
            'exit, clear\n'
        )
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(executable), batch_flag(), "do", str(wrapper)], cwd=str(stata_cwd(directory)),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    elapsed = time.perf_counter() - started
    log = directory / "stata.log"
    output = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    matches = [RC_SENTINEL.match(line) for line in output.splitlines()]
    codes = [int(match[1]) for match in matches if match]
    version_match = re.search(r"Stata(?:Now)?/(?:MP|SE|BE|IC)\s+(\d+(?:\.\d+)?)|Stata(?:Now)?\s+(\d+(?:\.\d+)?)", output)
    version = next((group for group in version_match.groups() if group), None) if version_match else None
    result = {
        "status": "timeout" if timed_out else ("completed" if codes else "missing_result"),
        "rc": codes[-1] if codes else None,
        "seconds": round(elapsed, 4),
        "scalars": parse_scalars(output),
        "version": version,
        # Kept in memory for the divergence report; never written verbatim.
        "_output": output,
    }
    if timed_out:
        result["error"] = f"Own Stata subprocess exceeded {timeout:g}s and was stopped."
    elif not codes:
        result["error"] = "Stata did not produce a completion sentinel. Check installation/license locally."
    return result


def run_openstat(case: Case, directory: Path, timeout: float = 30) -> dict[str, Any]:
    if case.online:
        (directory / "plus").mkdir()
    status_file = directory / "openstat-result.json"
    started = time.perf_counter()
    try:
        process = subprocess.run(
            [sys.executable, "-m", "openstat", "--batch", str(CASE_DIR / f"{case.name}.do"),
             "--cwd", str(directory), "--json", str(status_file)],
            cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "rc": None, "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "error": f"Own OpenStat subprocess exceeded {timeout:g}s and was stopped."}
    if not status_file.is_file():
        return {"status": "missing_result", "rc": None, "scalars": {},
                "seconds": round(time.perf_counter() - started, 4),
                "process_returncode": process.returncode}
    result = json.loads(status_file.read_text(encoding="utf-8"))
    return {
        "status": "completed", "rc": int(result["rc"]),
        "line": result.get("line"), "version": result.get("version"),
        "seconds": round(time.perf_counter() - started, 4),
        "scalars": parse_scalars(result["output"]),
        "_output": result["output"],
    }


def close_number(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return math.isclose(float(left), float(right), rel_tol=1e-8, abs_tol=1e-9)


def compare_csv(left: Path, right: Path) -> dict[str, Any]:
    if not left.exists() or not right.exists():
        return {"pass": False, "reason": "CSV missing", "stata_exists": left.exists(), "openstat_exists": right.exists()}
    with left.open(newline="", encoding="utf-8-sig") as handle:
        reference = list(csv.reader(handle))
    with right.open(newline="", encoding="utf-8-sig") as handle:
        actual = list(csv.reader(handle))
    if not reference or not actual or reference[0] != actual[0]:
        return {"pass": False, "reason": "CSV column mismatch"}
    if len(reference) != len(actual):
        return {"pass": False, "reason": "CSV row count mismatch"}
    differences = []
    for row_index, (expected_row, actual_row) in enumerate(zip(reference[1:], actual[1:]), 1):
        if len(expected_row) != len(actual_row):
            differences.append({"row": row_index, "reason": "field count"})
            continue
        for column, expected, observed in zip(reference[0], expected_row, actual_row):
            if expected == observed or expected in ("", ".") and observed in ("", "."):
                continue
            try:
                same = close_number(float(expected), float(observed))
            except ValueError:
                same = False
            if not same:
                differences.append({"row": row_index, "column": column, "expected": expected, "actual": observed})
    return {"pass": not differences, "rows": len(reference) - 1, "columns": reference[0], "differences": differences[:10]}


def inspect_dta(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    try:
        from pandas.io.stata import StataReader
        import pandas as pd
        with StataReader(path, convert_categoricals=False) as reader:
            labels = reader.variable_labels()
            value_labels = reader.value_labels()
            frame = reader.read()
        labeled = pd.read_stata(path, convert_categoricals=True)
        labeled_values = {
            column: labeled[column].astype(object).tolist()
            for column in labeled.columns
            if isinstance(labeled[column].dtype, pd.CategoricalDtype)
        }
        header = path.read_bytes()[:512]
        release_match = re.search(rb"<release>(\d+)</release>", header)
        return {
            "exists": True, "release": int(release_match[1]) if release_match else None,
            "rows": len(frame), "columns": list(frame.columns),
            "variable_labels": labels,
            "value_labels": {key: {str(number): label for number, label in values.items()} for key, values in value_labels.items()},
            "labeled_values": labeled_values,
        }
    except Exception as error:
        return {"exists": True, "error_type": type(error).__name__}


def inspect_png(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    data = path.read_bytes()
    valid = data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24
    return {
        "exists": True, "valid_png": valid, "bytes": len(data),
        "width": int.from_bytes(data[16:20], "big") if valid else None,
        "height": int.from_bytes(data[20:24], "big") if valid else None,
    }


def transcript_lines(output: str) -> list[str]:
    """Only the *value* lines: no log banner, no echoed commands, no blanks.

    Stata's log opens with a banner, then echoes ``. command`` and ``>
    continuation`` lines.  OpenStat's transcript has neither, so the banner and
    the echoes are dropped only on the side that has them; what remains on both
    sides is the output the engine produced.
    """
    raw_lines = output.splitlines()
    echoes = any(line.lstrip().startswith((".", ">")) for line in raw_lines)
    start = 0
    if echoes:
        # The banner is everything before the first echoed command.
        start = next((index for index, line in enumerate(raw_lines)
                      if line.lstrip().startswith((".", ">"))), 0)
    lines: list[str] = []
    skip_about = False
    for raw in raw_lines[start:]:
        stripped = raw.strip()
        if not stripped:
            continue
        if echoes and stripped.startswith((".", ">")):
            # `. about` prints the version/licence banner; it is not evidence.
            skip_about = stripped.startswith(". about")
            continue
        if skip_about:
            continue
        lines.append(sanitize(stripped, 200))
    return lines


def first_transcript_divergence(reference: dict[str, Any], actual: dict[str, Any]) -> str | None:
    left = transcript_lines(reference.get("_output", ""))
    right = transcript_lines(actual.get("_output", ""))
    for index in range(max(len(left), len(right))):
        expected = left[index] if index < len(left) else "<no line>"
        observed = right[index] if index < len(right) else "<no line>"
        if expected != observed:
            return f"line {index + 1}: stata `{expected}` vs openstat `{observed}`"
    return None


def compare_case(case: Case, reference: dict[str, Any], actual: dict[str, Any], stata_dir: Path, openstat_dir: Path) -> dict[str, Any]:
    errors = []
    divergence: list[tuple[str, str]] = []
    if reference.get("status") != "completed":
        errors.append("Stata reference did not complete")
        divergence.append(("engine", f"stata {reference.get('status')}: {reference.get('error') or ''}"))
    if actual.get("status") != "completed":
        errors.append("OpenStat did not complete")
        divergence.append(("engine", f"openstat {actual.get('status')}: {actual.get('error') or ''}"))
    if reference.get("rc") != case.expected_rc:
        errors.append(f"Reference expected r({case.expected_rc}), got r({reference.get('rc')})")
    if actual.get("rc") != reference.get("rc"):
        errors.append("Return codes differ")
        divergence.append(("return_code", f"stata r({reference.get('rc')}) vs openstat r({actual.get('rc')})"))
    ref_scalars, actual_scalars = reference.get("scalars", {}), actual.get("scalars", {})
    scalar_checks = {}
    for key in sorted(set(ref_scalars) | set(actual_scalars)):
        passed = key in ref_scalars and key in actual_scalars and close_number(ref_scalars.get(key), actual_scalars.get(key))
        scalar_checks[key] = {"pass": passed, "stata": ref_scalars.get(key), "openstat": actual_scalars.get(key)}
        if not passed:
            errors.append(f"Scalar {key} differs or missing")
            divergence.append(("scalar", f"{key}: stata={ref_scalars.get(key)} openstat={actual_scalars.get(key)}"))
    if case.expected_rc == 0 and not ref_scalars:
        errors.append("Reference produced no scalar evidence")
        divergence.append(("scalar", "reference produced no __OPENSTAT__ sentinel"))
    artifacts: dict[str, Any] = {}
    if case.csv_file:
        artifacts["csv"] = compare_csv(stata_dir / case.csv_file, openstat_dir / case.csv_file)
        if not artifacts["csv"]["pass"]:
            errors.append("CSV differs")
            detail = artifacts["csv"].get("reason") or (artifacts["csv"].get("differences") or [{}])[0]
            divergence.append(("csv", f"{case.csv_file}: {detail}"))
    if case.dta_file:
        expected, observed = inspect_dta(stata_dir / case.dta_file), inspect_dta(openstat_dir / case.dta_file)
        # Table names may differ between writers; values, assignments, and labels must agree.
        expected_labels = sorted(expected.get("value_labels", {}).values(), key=str)
        observed_labels = sorted(observed.get("value_labels", {}).values(), key=str)
        differing = [key for key in ("release", "rows", "columns", "variable_labels", "labeled_values")
                     if expected.get(key) != observed.get(key)]
        passed = not differing and expected_labels == observed_labels and expected.get("release") == 118
        artifacts["dta"] = {"pass": passed, "stata": expected, "openstat": observed}
        if not passed:
            errors.append("DTA format/labels differ")
            divergence.append(("dta", f"{case.dta_file}: differing field(s) {', '.join(differing) or 'value labels'}"
                                      f" (release stata={expected.get('release')} openstat={observed.get('release')})"))
    if case.png_file:
        expected, observed = inspect_png(stata_dir / case.png_file), inspect_png(openstat_dir / case.png_file)
        passed = bool(expected.get("valid_png") and observed.get("valid_png") and expected.get("width") == observed.get("width") == 640)
        artifacts["png"] = {"pass": passed, "comparison": "PNG validity and requested width only; no visual equivalence claim", "stata": expected, "openstat": observed}
        if not passed:
            errors.append("PNG export missing/invalid/wrong width")
            divergence.append(("png", f"{case.png_file}: stata={expected.get('valid_png')}/{expected.get('width')}px"
                                     f" openstat={observed.get('valid_png')}/{observed.get('width')}px"))
    if case.online:
        def package_evidence(directory: Path) -> dict[str, Any]:
            files = sorted((directory / "plus").rglob("distinct.ado"))
            data = files[0].read_bytes() if files else b""
            return {"ado_file_exists": bool(files), "ado_bytes": len(data), "ado_sha256": hashlib.sha256(data).hexdigest() if data else None}
        expected, observed = package_evidence(stata_dir), package_evidence(openstat_dir)
        passed = bool(expected["ado_file_exists"] and observed["ado_file_exists"] and expected["ado_sha256"] == observed["ado_sha256"])
        artifacts["ssc_download"] = {"pass": passed, "stata": expected, "openstat": observed}
        if not passed:
            errors.append("Downloaded SSC ado source missing or differs")
            divergence.append(("ssc", "downloaded ado source missing or hash differs"))

    if not errors:
        # Everything the case *asserts* agrees; look at the transcript for
        # unasserted output differences (informational, not a failure).
        note = first_transcript_divergence(reference, actual)
        first = {"kind": "transcript", "detail": note} if note else None
        return {"pass": True, "errors": [], "scalar_checks": scalar_checks, "artifacts": artifacts,
                "first_divergence": first}

    kind, detail = divergence[0]
    return {"pass": False, "errors": errors, "scalar_checks": scalar_checks,
            "artifacts": artifacts, "first_divergence": {"kind": kind, "detail": sanitize(detail)}}


def run_one(case: Case, executable: Path, timeout: float, online_ssc: bool) -> dict[str, Any]:
    if case.online and not online_ssc:
        return {"case": case.name, "status": "skipped",
                "reason": "Network test opt-in: --online-ssc", "features": case.features,
                "script": f"qa/cases/{case.name}.do", "expected_rc": case.expected_rc,
                "first_divergence": None,
                "sha256": hashlib.sha256((CASE_DIR / f"{case.name}.do").read_bytes()).hexdigest()}
    with tempfile.TemporaryDirectory(prefix="openstat-qa-") as temporary:
        work = Path(temporary)
        stata_dir, openstat_dir = work / "stata", work / "openstat"
        stata_dir.mkdir()
        openstat_dir.mkdir()
        try:
            reference = run_stata(executable, case, stata_dir, timeout)
        except Exception as error:
            reference = {"status": "exception", "rc": None, "scalars": {},
                         "error": f"{type(error).__name__}: {error}"}
        try:
            actual = run_openstat(case, openstat_dir, timeout)
        except Exception as error:
            actual = {"status": "exception", "rc": None, "scalars": {},
                      "error_type": type(error).__name__}
        try:
            comparison = compare_case(case, reference, actual, stata_dir, openstat_dir)
        except Exception as error:
            comparison = {"pass": False, "errors": [f"harness error: {type(error).__name__}: {error}"],
                          "scalar_checks": {}, "artifacts": {},
                          "first_divergence": {"kind": "harness", "detail": str(error)}}
    reference.pop("_output", None)
    actual.pop("_output", None)
    if reference.get("status") not in ("completed", None) or actual.get("status") not in ("completed", None):
        status = "error"
    else:
        status = "passed" if comparison["pass"] else "failed"
    return {
        "case": case.name, "status": status,
        "features": case.features,
        "script": f"qa/cases/{case.name}.do",
        "sha256": hashlib.sha256((CASE_DIR / f"{case.name}.do").read_bytes()).hexdigest(),
        "expected_rc": case.expected_rc,
        "first_divergence": comparison.get("first_divergence"),
        "stata": reference, "openstat": actual, "comparison": comparison,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stata", type=Path, default=default_stata(),
                        help="Licensed Stata executable; STATA_EXE also supported")
    parser.add_argument("--report", "--json", dest="report", type=Path, default=REPORT_PATH,
                        help="machine-readable report path (default qa/report.json)")
    parser.add_argument("--timeout", type=float, default=30,
                        help="Per-case, per-engine timeout in seconds (default 30, max 180)")
    parser.add_argument("--case", action="append", dest="names", metavar="NAME",
                        help="run one case (repeatable); see --list")
    parser.add_argument("--all", action="store_true", help="run every qa/cases/*.do (the default)")
    parser.add_argument("--list", action="store_true", help="print the corpus and exit")
    parser.add_argument("--jobs", type=int, default=1,
                        help=f"run up to N cases concurrently (default 1, max {MAX_STATA_PROCESSES})")
    parser.add_argument("--online-ssc", action="store_true",
                        help="Allow temporary online download from SSC; package is never copied into this repo")
    args = parser.parse_args(argv)

    corpus = cases()
    if args.list:
        print(f"{len(corpus)} cases in qa/cases")
        for case in corpus:
            flag = " (network, opt-in)" if case.online else (f" (expects r({case.expected_rc}))" if case.expected_rc else "")
            print(f"  {case.name:<18}{flag:<22}{', '.join(case.features)}")
        if unregistered_names():
            print("no explicit metadata (still run): " + ", ".join(unregistered_names()))
        return 0

    if not 1 <= args.timeout <= 180:
        parser.error("--timeout must be between 1 and 180 seconds")
    if not 1 <= args.jobs <= MAX_STATA_PROCESSES:
        parser.error(f"--jobs must be between 1 and {MAX_STATA_PROCESSES} (licensed Stata process limit)")
    known = {case.name for case in corpus}
    unknown = [name for name in (args.names or []) if name not in known]
    if unknown:
        parser.error(f"unknown case(s): {', '.join(unknown)}; see --list")
    if args.stata is None or not args.stata.is_file():
        parser.error("Stata executable unavailable; specify --stata or STATA_EXE")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    selected = [case for case in corpus if not args.names or case.name in args.names]
    if not selected:
        parser.error("no cases selected")

    results: dict[str, dict[str, Any]] = {}
    if args.jobs == 1:
        for case in selected:
            results[case.name] = run_one(case, args.stata, args.timeout, args.online_ssc)
            print(f"{case.name}: {results[case.name]['status']}", flush=True)
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {pool.submit(run_one, case, args.stata, args.timeout, args.online_ssc): case.name
                       for case in selected}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                except Exception as error:  # a harness bug must not lose the other cases
                    results[name] = {"case": name, "status": "error",
                                     "first_divergence": {"kind": "harness", "detail": str(error)},
                                     "features": [], "comparison": {"errors": [str(error)]}}
                print(f"{name}: {results[name]['status']}", flush=True)

    ordered = [results[case.name] for case in selected if case.name in results]
    summary = {state: sum(result["status"] == state for result in ordered)
               for state in ("passed", "failed", "error", "skipped")}
    summary["total"] = len(ordered)
    report = {
        "schema_version": 2, "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Bounded deterministic examples only. Passing does not establish arbitrary do-file, full Mata, package, or full Stata compatibility.",
        "reference_executable": args.stata.name,
        "stata_timeout_seconds": args.timeout,
        "openstat_timeout_seconds": args.timeout,
        "timing_method": "Wall-clock subprocess execution, including engine startup",
        "numeric_tolerance": {"relative": 1e-8, "absolute": 1e-9},
        "online_ssc_enabled": args.online_ssc,
        "jobs": args.jobs,
        "corpus": {"directory": "qa/cases", "discovered": len(corpus), "selected": len(ordered),
                   "without_metadata": unregistered_names()},
        "summary": summary,
        "cases": ordered,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    return int(summary["failed"] + summary["error"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
