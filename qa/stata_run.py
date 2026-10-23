"""Run a do-file in the locally installed, licensed Stata (batch mode).

This is the cross-platform replacement for the old ``qa/stata_run.sh``:

    python qa/stata_run.py path/to/case.do
    python qa/stata_run.py --dir path/to/probes
    python qa/stata_run.py --stata "C:/Program Files/StataNow19/StataMP-64.exe" case.do

The do-file is copied into a scratch directory, executed by Stata in batch
mode, and the resulting log is printed to stdout.  The Stata executable is
detected per platform (``STATA_EXE`` first, then the usual install
locations) and the platform's batch flag is used: ``/e`` on Windows, ``-b``
on macOS.  The wrapper do-file is written UTF-8 without a BOM and uses
forward-slash paths.

Only synthetic do-files are run; the tool never mutates user data.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

IS_WINDOWS = sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"

MACOS_STATA_ROOT = Path("/Applications/StataNow")
MACOS_STATA_EXE = MACOS_STATA_ROOT / "StataSE.app/Contents/MacOS/stata-se"

RC_SENTINEL = re.compile(r"^\s*__OPENSTAT_RETURN_CODE__\s*=\s*(\d+)\s*$")


def stata_candidates() -> list[Path]:
    """Every place a licensed Stata executable may live, most specific first."""
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
    else:
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


def stata_cwd(work: Path) -> Path:
    """The directory Stata is started from.

    macOS wants the application root (``/Applications/StataNow``).  On
    Windows the install root under ``Program Files`` is not writable by a
    normal user and Stata then exits without running anything, so the
    scratch directory is used instead.
    """
    return MACOS_STATA_ROOT if IS_MACOS else work


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


def strip_banner(text: str) -> str:
    """Drop the ``log using`` banner so only the transcript is shown."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("."):
            return "\n".join(lines[index:])
    return text


def run_dofile(source: Path, executable: Path, work: Path, timeout: float) -> dict[str, Any]:
    """Run one do-file in Stata and return the log and the return code."""
    shutil.copyfile(source, work / "case.do")
    wrapper = write_wrapper(work)
    started = time.perf_counter()
    try:
        process = subprocess.Popen(
            [str(executable), batch_flag(), "do", str(wrapper)],
            cwd=str(stata_cwd(work)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        return {"log": "", "rc": None, "status": "error",
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
        status, error = "timeout", f"Stata exceeded the {timeout:g}s timeout and was stopped."
    elif not codes:
        status, error = "missing_result", "Stata produced no completion sentinel."
    else:
        status, error = "completed", None
    return {"log": output, "rc": codes[-1] if codes else None, "status": status,
            "seconds": elapsed, "error": error}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dofile", nargs="?", help="the do-file to run")
    parser.add_argument("--dir", dest="directory", type=Path,
                        help="run every .do file in this directory")
    parser.add_argument("--stata", type=Path,
                        help="licensed Stata executable; STATA_EXE is also supported")
    parser.add_argument("--workdir", type=Path,
                        help="keep the scratch directory here instead of a temporary one")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="per-do-file timeout in seconds (default 120)")
    parser.add_argument("--banner", action="store_true",
                        help="keep the Stata log banner instead of only the transcript")
    args = parser.parse_args(argv)

    if not args.dofile and not args.directory:
        parser.error("give a do-file or --dir DIR")

    executable = Path(args.stata) if args.stata else detect_stata()
    if executable is None or not executable.is_file():
        parser.error("Stata executable unavailable; pass --stata PATH or set STATA_EXE")

    sources: list[Path] = []
    if args.directory:
        if not args.directory.is_dir():
            parser.error(f"{args.directory} is not a directory")
        sources = sorted(args.directory.glob("*.do"))
        if not sources:
            print(f"no .do files in {args.directory}")
            return 0
    if args.dofile:
        source = Path(args.dofile)
        if not source.is_file():
            parser.error(f"{source} does not exist")
        sources.append(source)

    failures = 0
    for source in sources:
        if args.workdir:
            # A user-supplied directory is never wiped; only the files this
            # run creates are replaced.
            work = Path(args.workdir)
            work.mkdir(parents=True, exist_ok=True)
            temporary = None
        else:
            temporary = tempfile.TemporaryDirectory(prefix="openstat-stata-")
            work = Path(temporary.name)
        result = run_dofile(source, executable, work, args.timeout)
        body = result["log"] if args.banner else strip_banner(result["log"])
        if args.directory:
            print(f"########## {source.stem} ##########")
            for line in body.splitlines():
                print(f"  {line}")
            print(f"  --- rc: {result['rc']}")
        else:
            print(body)
            print(f"___RETURN_CODE___={result['rc']}")
            print(f"___WORKDIR___={work}")
        if result["status"] != "completed":
            failures += 1
            print(f"  --- {result['status']}: {result['error']}", file=sys.stderr)
        if temporary is not None:
            temporary.cleanup()
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())