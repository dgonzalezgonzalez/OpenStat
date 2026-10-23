"""Build one ranked, deduplicated gap queue for OpenStat.

Three independent sources are merged:

* ``pytest``       -- ``python -m pytest -q --tb=no`` (no Stata needed),
* ``conformance``  -- ``qa/conformance_run.py --all --json qa/latest-report.json``,
* ``differential`` -- ``qa/run_differential.py --all --report qa/report.json``.

The two harnesses compare OpenStat with the licensed Stata.  ``--no-stata``
skips both oracle runs and reads the reports that are already on disk, which is
what CI-without-a-licence and offline triage need.

    python qa/gap_report.py                 # run everything, write both reports
    python qa/gap_report.py --no-stata      # reuse qa/latest-report.json + qa/report.json
    python qa/gap_report.py --json out.json --md out.md
    python qa/gap_report.py --jobs 2        # cap concurrent licensed-Stata processes

Output is ``qa/gap-report.json`` plus ``qa/gap-report.md``, and a Markdown
summary on stdout.  Every gap names a best-guess ``owning_module``; that is a
routing hint, not a claim about where the bug is.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
CONFORMANCE_REPORT = QA / "latest-report.json"
DIFFERENTIAL_REPORT = QA / "report.json"
GAP_JSON = QA / "gap-report.json"
GAP_MD = QA / "gap-report.md"

SCHEMA_VERSION = 1

#: The recorded starting point of the resumed session (``.local/swarm/PLAN.md``).
BASELINE = {
    "pytest": {"failed": 26, "passed": 226, "skipped": 4, "subtests_passed": 90},
    "conformance": {"passed": 10, "failed": 0, "error": 0},
    "differential": {"passed": 17, "failed": 0, "error": 0},
}

#: Most licensed-Stata processes this tool will ever ask for, in total.
MAX_STATA_PROCESSES = 4


# --------------------------------------------------------------------------
# owning-module routing
# --------------------------------------------------------------------------

#: pytest file -> the module that most often owns its failures.
TEST_FILE_MODULE = {
    "test_audit.py": "openstat/audit.py",
    "test_compatibility.py": "openstat/engine.py",
    "test_expanded_models.py": "openstat/linear.py",
    "test_extended_data.py": "openstat/extended_data.py",
    "test_gui.py": "openstat/gui.py",
    "test_mata_language.py": "openstat/mata.py",
    "test_metadata.py": "openstat/metadata.py",
    "test_output.py": "openstat/output.py",
    "test_packages.py": "openstat/packages.py",
    "test_programming.py": "openstat/programming.py",
    "test_returns.py": "openstat/statistics.py",
    "test_rng.py": "openstat/rng.py",
    "test_rng_fastpath.py": "openstat/rng.py",
    "test_runtime.py": "openstat/engine.py",
    "test_strings.py": "openstat/expression.py",
}

#: Unicode word -> module.  First match wins; order matters.
KEYWORD_MODULE = (
    (r"ssc|net install|adopath|\bwhich\b", "openstat/packages.py"),
    (r"reghdfe|ivregress|\biv\b|\b2sls\b", "openstat/instrumental.py"),
    (r"reghdfe|absorb|hdfe|df_a\b", "openstat/linear.py"),
    (r"xtreg|xtset|areg|\bpanel\b", "openstat/panel.py"),
    (r"factor variable|\bi\.\w|##|margins|contrast", "openstat/design.py"),
    (r"\[aw=|\[fw=|\[pw=|weight|aweight|fweight|pweight", "openstat/linear.py"),
    (r"regress|logit|probit|robust|cluster", "openstat/statistics.py"),
    (r"egen|\bgenerate\b|\breplace\b|\bnobs\b|recode", "openstat/generation.py"),
    (r"macro|local |global |`|expand", "openstat/macros.py"),
    (r"format|%[0-9]|display|list\b", "openstat/formats.py"),
    (r"\blabel\b|describe|notes|rename|order", "openstat/metadata.py"),
    (r"merge|append|reshape|preserve|restore|xmerge", "openstat/data_commands.py"),
    (r"\bdta\b|saveold|storage|compress", "openstat/storage.py"),
    (r"mata|st_matrix", "openstat/mata.py"),
    (r"putexcel|esttab|outreg|export|\blog\b", "openstat/output.py"),
    (r"program|marksample|tempvar|_rc|foreach|forvalues|prefix", "openstat/program_support.py"),
    (r"scatter|graph|png", "openstat/graphics.py"),
    (r"\brng\b|seed|runiform|rnormal", "openstat/rng.py"),
    (r"expression|operator|missing value", "openstat/expression.py"),
    (r"syntax|option|r\(198\)|r\(199\)", "openstat/parser.py"),
)

#: conformance case -> module the case mostly exercises.
CONFORMANCE_MODULE = {
    "c01_language": "openstat/engine.py",
    "c02_expressions": "openstat/expression.py",
    "c03_control": "openstat/engine.py",
    "c04_describe": "openstat/metadata.py",
    "c05_labels": "openstat/metadata.py",
    "c06_regress": "openstat/statistics.py",
    "c07_reshape": "openstat/data_commands.py",
    "c08_errors": "openstat/engine.py",
    "c09_random": "openstat/rng.py",
    "c10_macros": "openstat/macros.py",
}

#: differential case -> module the fixture mostly exercises.
DIFFERENTIAL_MODULE = {
    "data": "openstat/data_commands.py",
    "macros": "openstat/macros.py",
    "regression": "openstat/statistics.py",
    "robust": "openstat/statistics.py",
    "logistic": "openstat/statistics.py",
    "semantics": "openstat/engine.py",
    "self_reference": "openstat/generation.py",
    "dta": "openstat/storage.py",
    "preserve": "openstat/data_commands.py",
    "merge": "openstat/data_commands.py",
    "append": "openstat/data_commands.py",
    "reshape": "openstat/data_commands.py",
    "mata": "openstat/mata.py",
    "graph": "openstat/graphics.py",
    "unsupported": "openstat/engine.py",
    "invalid_option": "openstat/parser.py",
    "ssc": "openstat/packages.py",
    "factors_weights": "openstat/design.py",
    "instrumental": "openstat/instrumental.py",
    "panel": "openstat/panel.py",
    "hdfe": "openstat/linear.py",
}

CORE_MODULES = {
    "openstat/engine.py", "openstat/parser.py", "openstat/parser_expr.py",
    "openstat/expression.py", "openstat/macros.py", "openstat/formats.py",
    "openstat/storage.py", "openstat/syntax.py", "openstat/generation.py",
}

#: Feature keys used to merge the same underlying gap reported by >1 source.
FEATURES = (
    (r"reghdfe|df_a\b|absorb", "reghdfe"),
    (r"ivregress|2sls", "ivregress"),
    (r"xtreg|xtset|areg", "panel"),
    (r"factor variable|i\.g|##|c\.x", "factor-variables"),
    (r"\[aw=|\[fw=|\[pw=|weight", "weights"),
    (r"macro|local|global|expand", "macros"),
    (r"format|%[0-9]|display", "formats"),
    (r"reshape|merge|append|preserve", "data-shape"),
    (r"\bdta\b|saveold|storage", "dta"),
    (r"\blabel\b|describe", "metadata"),
    (r"mata|st_matrix", "mata"),
    (r"ssc|net install|adopath", "packages"),
    (r"seed|runiform|rnormal|\brng\b", "rng"),
    (r"logit|probit|logistic", "logistic"),
    (r"robust|cluster|hc1", "robust"),
    (r"putexcel|esttab|outreg", "output"),
)

SEVERITY_WEIGHT = {"high": 100, "medium": 50, "low": 10}


def _search(patterns: Iterable[tuple[str, str]], text: str) -> str | None:
    lowered = text.lower()
    for pattern, value in patterns:
        if re.search(pattern, lowered):
            return value
    return None


def module_for_test(node_id: str, symptom: str = "") -> str:
    """Best-guess owning module for a pytest node id (plus its message)."""
    file_part = node_id.split("::")[0].replace("\\", "/")
    test_name = file_part.rsplit("/", 1)[-1]
    by_keyword = _search(KEYWORD_MODULE, f"{node_id} {symptom}")
    if by_keyword:
        return by_keyword
    return TEST_FILE_MODULE.get(test_name, "openstat/engine.py")


def feature_key(*texts: str) -> str | None:
    return _search(FEATURES, " ".join(texts))


def slug(text: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:limit] or "gap"


#: Absolute local paths (Windows drive letters, POSIX temp/home install roots).
#: Deliberately narrow: a bare ``/`` in an expression must survive.
ABSOLUTE_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'`|]*"
                           r"|/(?:tmp|private|var|home|Users|Applications)/[^\s\"'`|]*")


def scrub(text: str) -> str:
    """Keep local absolute paths out of a shareable report."""
    return ABSOLUTE_PATH.sub("<path>", str(text))


def short(text: str, limit: int = 300) -> str:
    collapsed = " ".join(scrub(text).split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 3] + "..."


RC_LINE = re.compile(r"(r\(\d+\); \[line \d+\]|r\(\d+\))")


def first_of(values: list[str], limit: int = 300) -> str:
    """The first problem, plus a count of the rest."""
    if not values:
        return "unknown"
    head = short(values[0], limit)
    return f"{head} (+{len(values) - 1} more)" if len(values) > 1 else head


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def run_command(command: list[str], timeout: float) -> tuple[int, str]:
    # Force UTF-8 on the child's stdout so a cp1252 console cannot mojibake the
    # transcript (PYTHONUTF8 is deliberately *not* set: it would change how the
    # runtime under test opens files).
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(command, cwd=str(ROOT), capture_output=True,
                                   timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT after {timeout:g}s: {' '.join(command)}"
    except OSError as error:
        return 127, f"could not launch {' '.join(command)}: {error}"
    output = (completed.stdout or b"") + (completed.stderr or b"")
    return completed.returncode, output.decode("utf-8", errors="replace")


SUMMARY_LINE = re.compile(
    r"(?:(?P<failed>\d+) failed)?[,\s]*"
    r"(?:(?P<passed>\d+) passed)?[,\s]*"
    r"(?:(?P<skipped>\d+) skipped)?[,\s]*"
    r"(?:(?P<error>\d+) (?:error|errors))?[,\s]*"
    r"(?:(?P<subtests>\d+) subtests? passed)?")


def parse_pytest_summary(text: str) -> dict[str, int]:
    """Counts from the final pytest summary line (defaults to zero)."""
    counts = {"passed": 0, "failed": 0, "skipped": 0, "error": 0, "subtests_passed": 0}
    for line in reversed(text.splitlines()):
        stripped = line.strip().strip("=").strip()
        if not stripped or " in " not in stripped:
            continue
        if not re.search(r"\b(passed|failed|skipped|error|no tests ran)\b", stripped):
            continue
        match = SUMMARY_LINE.search(stripped)
        if not match:
            continue
        for key in ("failed", "passed", "skipped", "error", "subtests"):
            value = match.group(key)
            if value:
                counts["subtests_passed" if key == "subtests" else key] = int(value)
        return counts
    return counts


FAILED_LINE = re.compile(r"^(FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$")


def junit_messages(path: Path) -> dict[str, str]:
    """``test name -> failure message`` from a JUnit XML report.

    ``pytest -q --tb=no`` keeps the output small but drops the assertion text
    from the ``FAILED`` lines; the JUnit report keeps it, so the two are joined
    on the test name.
    """
    messages: dict[str, str] = {}
    if not path.is_file():
        return messages
    try:
        import xml.etree.ElementTree as ElementTree
        root = ElementTree.parse(path).getroot()
    except Exception:
        return messages
    for testcase in root.iter("testcase"):
        for child in testcase:
            if child.tag not in ("failure", "error"):
                continue
            message = (child.get("message") or "").strip() or (child.text or "").strip()
            name = testcase.get("name") or ""
            messages.setdefault(name, short(message, 240) if message else "")
    return messages


def pytest_gaps(text: str, messages: dict[str, str] | None = None) -> list[dict[str, Any]]:
    messages = messages or {}
    gaps: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = FAILED_LINE.match(line.strip())
        if not match:
            continue
        kind, node_id, message = match.groups()
        name = node_id.rsplit("::", 1)[-1]
        detail = message or messages.get(name) or ""
        symptom = short(detail) if detail else f"pytest {kind.lower()} (no message recorded)"
        # The first runtime error the assertion hit is the actionable anchor.
        rc_match = RC_LINE.search(symptom)
        first = f"{rc_match.group(1)} -- {short(symptom, 200)}" if rc_match else symptom
        module = module_for_test(node_id, symptom)
        feature = feature_key(node_id, symptom)
        severity = "high" if module in CORE_MODULES else "medium"
        gaps.append({
            "source": "pytest",
            "case": node_id,
            "symptom": symptom,
            "first_divergence": short(first),
            "owning_module": module,
            "severity": severity,
            "kind": "pytest",
            "dedup": f"{module}|{feature or node_id}|pytest",
        })
    return gaps


def conformance_gaps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for case in payload.get("cases", []):
        if case.get("status") == "passed":
            continue
        name = case.get("name") or case.get("case") or "?"
        rc = case.get("rc") or {}
        mismatches = case.get("scalar_mismatches") or []
        stata_status = (case.get("stata") or {}).get("status")
        openstat_status = (case.get("openstat") or {}).get("status")
        engine_error = ([f"stata {stata_status}"] if stata_status not in (None, "completed") else []) + \
                       ([f"openstat {openstat_status}"] if openstat_status not in (None, "completed") else [])
        if engine_error:
            divergence = "; ".join(engine_error)
            kind = "engine"
        elif mismatches:
            first = mismatches[0]
            divergence = (f"scalar {first.get('name')}: "
                          f"stata={first.get('stata')} openstat={first.get('openstat')}")
            kind = "scalar"
        elif rc.get("stata") != rc.get("openstat"):
            divergence = f"return code: stata r({rc.get('stata')}) openstat r({rc.get('openstat')})"
            kind = "return_code"
        else:
            diff = [line for line in (case.get("diff") or []) if line.strip()]
            divergence = diff[0] if diff else (case.get("problems") or ["unknown"])[0]
            kind = "transcript"
        problems = case.get("problems") or []
        module = CONFORMANCE_MODULE.get(name) or _search(KEYWORD_MODULE, " ".join(problems)) or "openstat/engine.py"
        gaps.append({
            "source": "conformance",
            "case": case.get("script") or name,
            "symptom": first_of([str(problem) for problem in problems]) if problems else f"{name}: {case.get('status')}",
            "first_divergence": short(divergence),
            "owning_module": module,
            "severity": "high",
            "kind": kind,
            "dedup": f"{module}|{feature_key(name, ' '.join(problems)) or name}|{kind}",
            "status": case.get("status"),
        })
    return gaps


def differential_gaps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for case in payload.get("cases", []):
        if case.get("status") in ("passed", "skipped"):
            continue
        name = case.get("case") or "?"
        divergence = case.get("first_divergence") or {}
        if isinstance(divergence, dict):
            kind = divergence.get("kind") or "unknown"
            detail = divergence.get("detail") or ""
        else:
            kind, detail = "unknown", str(divergence)
        errors = (case.get("comparison") or {}).get("errors") or []
        module = DIFFERENTIAL_MODULE.get(name) or _search(KEYWORD_MODULE, " ".join(errors) + " " + detail) or "openstat/engine.py"
        if case.get("status") == "error" or kind in ("engine", "return_code"):
            severity = "high"
        elif module in CORE_MODULES:
            severity = "high"
        else:
            severity = "medium"
        gaps.append({
            "source": "differential",
            "case": case.get("script") or f"qa/cases/{name}.do",
            "symptom": first_of([str(error) for error in errors]) if errors else f"{name}: {case.get('status')}",
            "first_divergence": short(detail or kind),
            "owning_module": module,
            "severity": severity,
            "kind": kind,
            "dedup": f"{module}|{feature_key(name, ' '.join(errors), detail) or name}|{kind}",
            "status": case.get("status"),
        })
    return gaps


# --------------------------------------------------------------------------
# merge + rank
# --------------------------------------------------------------------------

def merge(gaps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for gap in gaps:
        key = gap.pop("dedup")
        existing = merged.get(key)
        if existing is None:
            merged[key] = {**gap, "dedup_key": key, "sources": [gap["source"]],
                           "evidence": [{"source": gap["source"], "case": gap["case"],
                                         "symptom": gap["symptom"]}]}
            continue
        if gap["source"] not in existing["sources"]:
            existing["sources"].append(gap["source"])
        existing["evidence"].append({"source": gap["source"], "case": gap["case"],
                                     "symptom": gap["symptom"]})
        if SEVERITY_WEIGHT[gap["severity"]] > SEVERITY_WEIGHT[existing["severity"]]:
            existing["severity"] = gap["severity"]
        # The first symptom seen stays the headline, so the title always matches
        # `evidence[0]`; the other symptoms are still listed in `evidence`.
    ordered = list(merged.values())
    for gap in ordered:
        gap["score"] = (SEVERITY_WEIGHT[gap["severity"]]
                        + 15 * (len(gap["sources"]) - 1)
                        + 10 * (len(gap["evidence"]) - 1))
    ordered.sort(key=lambda gap: (-gap["score"], gap["owning_module"], gap["evidence"][0]["case"]))
    for rank, gap in enumerate(ordered, 1):
        gap["rank"] = rank
        gap["id"] = f"{rank:03d}-{gap['source']}-{slug(gap['evidence'][0]['case'].rsplit('::', 1)[-1])}"
    return ordered


def counts_for(gaps: list[dict[str, Any]], source: str) -> dict[str, int]:
    total = sum(1 for gap in gaps if gap["source"] == source)
    high = sum(1 for gap in gaps if gap["source"] == source and gap["severity"] == "high")
    return {"gaps": total, "high": high}


def newest_source_mtime() -> float:
    """The mtime of the newest runtime/case/test file."""
    newest = 0.0
    for pattern in ("openstat/**/*.py", "qa/cases/*.do", "qa/conformance/*.do", "tests/**/*.py"):
        for path in ROOT.glob(pattern):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    return newest


def report_is_stale(generated_utc: str | None) -> bool:
    """True when the report predates the code it is being compared against."""
    if not generated_utc:
        return True
    try:
        stamped = datetime.fromisoformat(generated_utc).timestamp()
    except (TypeError, ValueError):
        return True
    return stamped < newest_source_mtime()


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def markdown(payload: dict[str, Any], limit: int = 25) -> str:
    lines = ["# OpenStat gap queue", "",
             f"Generated {payload['generated_utc']} by `qa/gap_report.py`.", ""]
    baseline = payload["baseline"]
    lines += ["## Baseline vs measured", "",
              "| source | baseline | measured | verdict |", "|---|---|---|---|"]
    for name in ("pytest", "conformance", "differential"):
        entry = baseline[name]
        lines.append(f"| {name} | {entry['baseline_text']} | {entry['measured_text']} | "
                     f"{entry['verdict']} |")
    lines += ["", f"**{len(payload['gaps'])} ranked gaps** "
                  f"({sum(g['severity'] == 'high' for g in payload['gaps'])} high, "
                  f"{sum(g['severity'] == 'medium' for g in payload['gaps'])} medium, "
                  f"{sum(g['severity'] == 'low' for g in payload['gaps'])} low).", ""]
    if not payload["gaps"]:
        lines += ["No gaps: every source is clean.", ""]
        return "\n".join(lines) + "\n"
    lines += ["## Ranked queue", "",
              "| # | sev | module | source | case | symptom |",
              "|---|---|---|---|---|---|"]
    for gap in payload["gaps"][:limit]:
        case = gap["evidence"][0]["case"]
        extra = f" (+{len(gap['evidence']) - 1})" if len(gap["evidence"]) > 1 else ""
        lines.append(f"| {gap['rank']} | {gap['severity']} | `{gap['owning_module']}` | "
                     f"{', '.join(gap['sources'])} | `{case}`{extra} | {gap['symptom'][:110]} |")
    lines += ["", "## Detail", ""]
    for gap in payload["gaps"][:limit]:
        lines.append(f"### {gap['rank']}. {gap['symptom'][:100]}")
        lines.append("")
        lines.append(f"- module: `{gap['owning_module']}` | severity: **{gap['severity']}** | "
                     f"score: {gap['score']}")
        lines.append(f"- first divergence: `{gap['first_divergence'][:200]}`")
        for evidence in gap["evidence"][:6]:
            lines.append(f"- {evidence['source']}: `{evidence['case']}` -- {evidence['symptom'][:140]}")
        if len(gap["evidence"]) > 6:
            lines.append(f"- ... {len(gap['evidence']) - 6} more evidence items (see the JSON)")
        lines.append("")
    if len(payload["gaps"]) > limit:
        lines += [f"... {len(payload['gaps']) - limit} further gaps are in `qa/gap-report.json`.", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true",
                        help="run every source (the default; kept for symmetry with the harnesses)")
    parser.add_argument("--no-stata", action="store_true",
                        help="skip the licensed-Stata harnesses and reuse the existing reports")
    parser.add_argument("--json", type=Path, default=GAP_JSON, help="gap JSON path")
    parser.add_argument("--md", type=Path, default=GAP_MD, help="gap Markdown path")
    parser.add_argument("--jobs", type=int, default=2,
                        help=f"concurrency passed to the harnesses (default 2, max {MAX_STATA_PROCESSES})")
    parser.add_argument("--pytest-timeout", type=float, default=900.0)
    parser.add_argument("--harness-timeout", type=float, default=1800.0)
    parser.add_argument("--top", type=int, default=25, help="how many gaps the Markdown shows")
    args = parser.parse_args(argv)

    if not 1 <= args.jobs <= MAX_STATA_PROCESSES:
        parser.error(f"--jobs must be between 1 and {MAX_STATA_PROCESSES}")

    sources: dict[str, Any] = {}
    gaps: list[dict[str, Any]] = []

    # ---- pytest ---------------------------------------------------------
    junit = Path(tempfile.mkdtemp(prefix="openstat-gap-")) / "pytest.xml"
    rc, output = run_command([sys.executable, "-m", "pytest", "-q", "--tb=no", "-rf",
                              "-p", "no:cacheprovider", f"--junit-xml={junit}"],
                             args.pytest_timeout)
    pytest_counts = parse_pytest_summary(output)
    gaps += pytest_gaps(output, junit_messages(junit))
    sources["pytest"] = {
        "command": "python -m pytest -q --tb=no -rf --junit-xml=<temp>/pytest.xml",
        "returncode": rc,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "summary": pytest_counts,
        "assertion_messages": "from the JUnit XML report",
    }

    # ---- conformance ----------------------------------------------------
    if args.no_stata:
        if CONFORMANCE_REPORT.is_file():
            payload = json.loads(CONFORMANCE_REPORT.read_text(encoding="utf-8"))
            sources["conformance"] = {
                "command": "reused (--no-stata)",
                "path": CONFORMANCE_REPORT.relative_to(ROOT).as_posix(),
                "generated_utc": payload.get("generated_utc"),
                "summary": payload.get("summary", {}),
                "reused": True,
                "stale": report_is_stale(payload.get("generated_utc")),
            }
            gaps += conformance_gaps(payload)
        else:
            sources["conformance"] = {"command": "skipped (--no-stata, no report on disk)",
                                      "missing": True, "summary": {}}
    else:
        rc, output = run_command([sys.executable, str(QA / "conformance_run.py"), "--all",
                                  "--jobs", str(args.jobs), "--json", str(CONFORMANCE_REPORT)],
                                 args.harness_timeout)
        if CONFORMANCE_REPORT.is_file():
            payload = json.loads(CONFORMANCE_REPORT.read_text(encoding="utf-8"))
            sources["conformance"] = {
                "command": "python qa/conformance_run.py --all --jobs "
                           f"{args.jobs} --json qa/latest-report.json",
                "path": CONFORMANCE_REPORT.relative_to(ROOT).as_posix(),
                "generated_utc": payload.get("generated_utc"),
                "summary": payload.get("summary", {}),
                "returncode": rc,
            }
            gaps += conformance_gaps(payload)
        else:
            sources["conformance"] = {"command": "failed", "returncode": rc,
                                      "error": short(output, 400), "summary": {}}

    # ---- differential ---------------------------------------------------
    if args.no_stata:
        if DIFFERENTIAL_REPORT.is_file():
            payload = json.loads(DIFFERENTIAL_REPORT.read_text(encoding="utf-8"))
            sources["differential"] = {
                "command": "reused (--no-stata)",
                "path": DIFFERENTIAL_REPORT.relative_to(ROOT).as_posix(),
                "generated_utc": payload.get("generated_utc"),
                "summary": payload.get("summary", {}),
                "reused": True,
                "stale": report_is_stale(payload.get("generated_utc")),
            }
            gaps += differential_gaps(payload)
        else:
            sources["differential"] = {"command": "skipped (--no-stata, no report on disk)",
                                       "missing": True, "summary": {}}
    else:
        rc, output = run_command([sys.executable, str(QA / "run_differential.py"), "--all",
                                  "--jobs", str(args.jobs), "--report", str(DIFFERENTIAL_REPORT)],
                                 args.harness_timeout)
        if DIFFERENTIAL_REPORT.is_file():
            payload = json.loads(DIFFERENTIAL_REPORT.read_text(encoding="utf-8"))
            sources["differential"] = {
                "command": "python qa/run_differential.py --all --jobs "
                           f"{args.jobs} --report qa/report.json",
                "path": DIFFERENTIAL_REPORT.relative_to(ROOT).as_posix(),
                "generated_utc": payload.get("generated_utc"),
                "summary": payload.get("summary", {}),
                "returncode": rc,
            }
            gaps += differential_gaps(payload)
        else:
            sources["differential"] = {"command": "failed", "returncode": rc,
                                       "error": short(output, 400), "summary": {}}

    ranked = merge(gaps)

    def baseline_block(name: str, measured: dict[str, int], text: str) -> dict[str, Any]:
        expected = BASELINE[name]
        worse = any(measured.get(key, 0) > value for key, value in expected.items()
                    if key in ("failed", "error") and key in measured)
        better_lost = any(measured.get(key, 0) < value for key, value in expected.items()
                          if key in ("passed", "subtests_passed") and key in measured)
        return {"baseline": expected, "measured": measured,
                "baseline_text": ", ".join(f"{k}={v}" for k, v in expected.items()),
                "measured_text": text,
                "verdict": "regressed" if (worse or better_lost) else "at-or-better"}

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "tool": "qa/gap_report.py",
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "stata_runs": not args.no_stata,
        "sources": sources,
        "baseline": {
            "pytest": baseline_block("pytest", pytest_counts,
                                     f"failed={pytest_counts['failed']}, passed={pytest_counts['passed']}, "
                                     f"skipped={pytest_counts['skipped']}, "
                                     f"subtests_passed={pytest_counts['subtests_passed']}"),
            "conformance": baseline_block(
                "conformance",
                {"passed": sources["conformance"]["summary"].get("passed", 0),
                 "failed": sources["conformance"]["summary"].get("failed", 0),
                 "error": sources["conformance"]["summary"].get("error", 0)},
                f"passed={sources['conformance']['summary'].get('passed', 0)}, "
                f"failed={sources['conformance']['summary'].get('failed', 0)}, "
                f"error={sources['conformance']['summary'].get('error', 0)}"),
            "differential": baseline_block(
                "differential",
                {"passed": sources["differential"]["summary"].get("passed", 0),
                 "failed": sources["differential"]["summary"].get("failed", 0),
                 "error": sources["differential"]["summary"].get("error", 0)},
                f"passed={sources['differential']['summary'].get('passed', 0)}, "
                f"failed={sources['differential']['summary'].get('failed', 0)}, "
                f"error={sources['differential']['summary'].get('error', 0)}, "
                f"skipped={sources['differential']['summary'].get('skipped', 0)}"),
        },
        "totals": {
            "gaps": len(ranked),
            "high": sum(1 for gap in ranked if gap["severity"] == "high"),
            "medium": sum(1 for gap in ranked if gap["severity"] == "medium"),
            "low": sum(1 for gap in ranked if gap["severity"] == "low"),
            "raw_findings": len(gaps),
            "by_source": {name: counts_for(gaps, name)["gaps"]
                          for name in ("pytest", "conformance", "differential")},
        },
        "gaps": ranked,
    }

    args_json = Path(args.json)
    args_md = Path(args.md)
    args_json.parent.mkdir(parents=True, exist_ok=True)
    args_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    body = markdown(payload, args.top)
    args_md.write_text(body, encoding="utf-8")
    try:  # a cp1252 console cannot print every symptom; never crash on it
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
