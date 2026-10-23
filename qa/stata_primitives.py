"""The shared-primitive contract for Stata commands.

Every Stata command reuses the same small set of interface primitives: the
``if``/``in`` qualifiers, the ``by`` prefix, the weight brackets, factor-variable
operators, time-series operators, storage types, display formats, the
``r()``/``e()``/``s()``/``c()`` result classes, command prefixes, the ``syntax``
grammar, the program-support commands and the numeric missing values.  Getting
one of them wrong breaks every command that relies on it, so the contract is
published once, here, instead of being re-derived by each conformance case.

``qa/stata_primitives.json`` is the catalogue: one record per primitive with an
own-words summary, the grammar, one to three examples, the *measured* status of
OpenStat's implementation, and the help files and target subjects the record is
based on.

Licensing
---------
The records quote interface facts only: primitive names, grammar shapes, option
and keyword names, and observed behaviour.  No help-file prose, table or example
is copied.  ``qa/stata_help.py`` (and therefore ``qa/stata_targets.json``) follows
the same rule.

Public API
----------
``primitives(area=None, kind=None, status=None)``
    Every primitive record, optionally filtered.
``primitive(name)``
    One record; raises ``KeyError`` for an unknown name.
``openstat_status(name)``
    ``implemented`` | ``partial`` | ``absent`` | ``unknown``.
``areas()``
    The fifteen areas, in order.
``resolve_source(source, ...)``
    Map a ``sthlp:``/``targets:`` reference to a real path.
``validate(document=None, ...)``
    Return the list of problems; an empty list means the catalogue is sound.
``summary(document=None)``
    Counts per area, per kind and per status.
``main(argv=None)``
    ``--summary``, ``--check``, ``--primitive NAME``, ``--area KEY``, ``--json``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

__all__ = [
    "SCHEMA_VERSION",
    "GENERATOR",
    "KINDS",
    "STATUSES",
    "CATALOGUE",
    "STATA_BASE",
    "load",
    "areas",
    "primitives",
    "primitive",
    "openstat_status",
    "resolve_source",
    "validate",
    "summary",
    "main",
]

SCHEMA_VERSION = 1
GENERATOR = "qa/stata_primitives.py"

REPO = Path(__file__).resolve().parent.parent
#: The catalogue document.
CATALOGUE = REPO / "qa" / "stata_primitives.json"
#: Subject index produced by ``qa/stata_help.py``.
TARGETS = REPO / "qa" / "stata_targets.json"
#: The licensed Stata help corpus on this host.  ``--stata-root`` and the
#: ``STATA_BASE`` environment variable override it.
STATA_BASE = Path(r"C:\Program Files\StataNow19\ado\base")

#: The primitive kinds the catalogue is allowed to use.
KINDS = (
    "qualifier", "weight", "operator", "type", "result-class", "prefix",
    "option-group", "command-group",
)

#: The implementation statuses a record may carry.
STATUSES = ("implemented", "partial", "absent", "unknown")

#: Fields every primitive record must carry.
REQUIRED_FIELDS = (
    "name", "area", "kind", "summary", "grammar", "examples",
    "openstat_status", "openstat_note", "sources",
)

#: Source prefixes understood by :func:`resolve_source`.
SOURCE_SCHEMES = ("sthlp:", "targets:")

#: Maximum number of examples a record may carry.
MAX_EXAMPLES = 3


class PrimitiveError(KeyError):
    """Raised when a primitive name is not in the catalogue."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load(path: Path | str | None = None) -> dict:
    """Read the catalogue document and check that it is well formed.

    The document is returned as a dictionary.  ``ValueError`` is raised when
    the file cannot be parsed or its ``schema_version`` is not the one this
    module understands.
    """
    source = Path(path) if path is not None else CATALOGUE
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read the primitive catalogue {source}: {error}") from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{source} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{source} must contain a JSON object")
    version = document.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"{source} has schema_version {version!r}; this module reads "
            f"{SCHEMA_VERSION}")
    if not isinstance(document.get("primitives"), list):
        raise ValueError(f"{source} has no 'primitives' list")
    return document


_CACHE: dict = {}


def _document(document: dict | None = None) -> dict:
    if document is not None:
        return document
    if "doc" not in _CACHE:
        _CACHE["doc"] = load()
    return _CACHE["doc"]


# ---------------------------------------------------------------------------
# Query API
# ---------------------------------------------------------------------------
def areas(document: dict | None = None) -> list[dict]:
    """The catalogue's areas, in numbered order."""
    return list(_document(document).get("areas", []))


def primitives(document: dict | None = None, *, area: str | None = None,
               kind: str | None = None,
               status: str | None = None) -> list[dict]:
    """Every primitive record, optionally filtered.

    ``area`` matches the area key, ``kind`` one of :data:`KINDS` and ``status``
    one of :data:`STATUSES`.  The order is the order the catalogue stores, which
    is the order of the fifteen areas.
    """
    records = list(_document(document)["primitives"])
    if area is not None:
        records = [record for record in records if record["area"] == area]
    if kind is not None:
        records = [record for record in records if record["kind"] == kind]
    if status is not None:
        records = [record for record in records if record["openstat_status"] == status]
    return records


def primitive(name: str, document: dict | None = None) -> dict:
    """Return one primitive record.

    ``KeyError`` is raised for a name the catalogue does not carry, so a case
    that cites a primitive cannot silently cite nothing.
    """
    for record in _document(document)["primitives"]:
        if record["name"] == name:
            return record
    known = ", ".join(sorted(record["name"] for record in _document(document)["primitives"]))
    raise PrimitiveError(f"unknown primitive {name!r}; known primitives: {known}")


def openstat_status(name: str, document: dict | None = None) -> str:
    """Return OpenStat's status for one primitive."""
    return primitive(name, document)["openstat_status"]


def names(document: dict | None = None) -> list[str]:
    """Every primitive name, in catalogue order."""
    return [record["name"] for record in _document(document)["primitives"]]


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def _targets(stata_root: Path | None = None) -> dict:
    key = "targets:" + str(stata_root)
    if key not in _CACHE:
        if not TARGETS.is_file():
            _CACHE[key] = {}
        else:
            with TARGETS.open(encoding="utf-8") as handle:
                _CACHE[key] = json.load(handle).get("commands", {})
    return _CACHE[key]


def resolve_source(source: str, *, stata_root: Path | str | None = None,
                   repo_root: Path | str | None = None) -> Path | None:
    """Map a catalogue source reference to a path, or ``None``.

    ``sthlp:`` references name a file under the Stata help corpus; ``targets:``
    references name a subject key in ``qa/stata_targets.json`` and resolve to
    that file.  An unrecognised scheme resolves to ``None``.
    """
    root = Path(stata_root) if stata_root is not None else STATA_BASE
    repo = Path(repo_root) if repo_root is not None else REPO
    if source.startswith("sthlp:"):
        return root / source[len("sthlp:"):].replace("\\", "/")
    if source.startswith("targets:"):
        entry = _targets(root).get(source[len("targets:"):])
        if not entry:
            return None
        return root / entry["help"].replace("\\", "/")
    return None


def _source_problem(source: str, *, stata_root: Path, repo_root: Path) -> str | None:
    if not isinstance(source, str) or not source:
        return "source must be a non-empty string"
    if not source.startswith(SOURCE_SCHEMES):
        return f"source {source!r} must start with one of {SOURCE_SCHEMES}"
    if source.startswith("targets:"):
        subject = source[len("targets:"):]
        entry = _targets(stata_root).get(subject)
        if entry is None:
            return f"source {source!r}: subject {subject!r} is not in {TARGETS.name}"
        path = stata_root / entry["help"].replace("\\", "/")
        if not path.is_file():
            return f"source {source!r}: help file {path} does not exist"
        return None
    path = resolve_source(source, stata_root=stata_root, repo_root=repo_root)
    if path is None or not path.is_file():
        return f"source {source!r}: {path} does not exist"
    return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate(document: dict | None = None, *, stata_root: Path | str | None = None,
             repo_root: Path | str | None = None) -> list[str]:
    """Return every problem found in the catalogue; empty means it is sound.

    The checks are: the document's shape, unique primitive names, a known area
    and kind and status, the required fields, one to three examples, and a
    source list that resolves on this host.  A missing Stata installation is
    reported as a warning, not as a per-record failure.
    """
    problems: list[str] = []
    root = Path(stata_root) if stata_root is not None else STATA_BASE
    repo = Path(repo_root) if repo_root is not None else REPO
    try:
        doc = _document(document)
    except ValueError as error:
        return [str(error)]

    if doc.get("generator") != GENERATOR:
        problems.append(f"generator should be {GENERATOR!r}, found {doc.get('generator')!r}")
    for key in ("source_version", "measured", "notes", "areas"):
        if key not in doc:
            problems.append(f"the document is missing the {key!r} field")

    area_keys = []
    for entry in doc.get("areas", []):
        for field in ("id", "key", "title", "summary"):
            if field not in entry:
                problems.append(f"area {entry!r} is missing {field!r}")
        area_keys.append(entry.get("key"))
    if len(area_keys) != len(set(area_keys)):
        problems.append("the area keys are not unique")

    targets_available = bool(_targets(root))
    if not targets_available:
        problems.append(
            f"warning: {TARGETS} is unavailable, so 'targets:' sources were not "
            "checked against subject keys")
    if not root.is_dir():
        problems.append(f"warning: the Stata help corpus {root} is not present; "
                        "'sthlp:' sources were only checked for a known path shape")

    seen: set[str] = set()
    for index, record in enumerate(doc.get("primitives", [])):
        label = record.get("name") or f"#{index}"
        for field in REQUIRED_FIELDS:
            if field not in record:
                problems.append(f"{label}: missing required field {field!r}")
        if label in seen:
            problems.append(f"{label}: duplicate primitive name")
        seen.add(label)
        if record.get("area") not in area_keys:
            problems.append(f"{label}: unknown area {record.get('area')!r}")
        if record.get("kind") not in KINDS:
            problems.append(f"{label}: unknown kind {record.get('kind')!r}")
        if record.get("openstat_status") not in STATUSES:
            problems.append(f"{label}: unknown status {record.get('openstat_status')!r}")
        for field in ("summary", "grammar", "openstat_note"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}: {field} must be a non-empty string")
        examples = record.get("examples")
        if not isinstance(examples, list) or not examples:
            problems.append(f"{label}: needs at least one example")
        elif len(examples) > MAX_EXAMPLES:
            problems.append(f"{label}: carries {len(examples)} examples; the "
                            f"maximum is {MAX_EXAMPLES}")
        sources = record.get("sources")
        if not isinstance(sources, list) or not sources:
            problems.append(f"{label}: needs at least one source")
            continue
        for source in sources:
            if not isinstance(source, str) or not source:
                problems.append(f"{label}: {source!r} is not a source reference")
                continue
            if source.startswith("targets:") and not targets_available:
                continue
            if source.startswith("sthlp:") and not root.is_dir():
                continue
            problem = _source_problem(source, stata_root=root, repo_root=repo)
            if problem:
                problems.append(f"{label}: {problem}")
    return problems


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _counts(values: Iterable[str]) -> dict:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def summary(document: dict | None = None) -> str:
    """Return a human readable summary of the catalogue."""
    doc = _document(document)
    records = doc["primitives"]
    statuses = _counts(record["openstat_status"] for record in records)
    kinds = _counts(record["kind"] for record in records)
    lines = [
        f"Stata primitives ({doc.get('source_version', '?')}), measured "
        f"{doc.get('measured', '?')}",
        f"{len(records)} primitives in {len(doc.get('areas', []))} areas",
        "status: " + ", ".join(f"{key} {statuses.get(key, 0)}" for key in STATUSES),
        "kinds:  " + ", ".join(f"{key} {kinds.get(key, 0)}" for key in KINDS
                               if kinds.get(key)),
        "",
        f"{'area':<24}{'n':>4}  {'implemented':>11}{'partial':>9}{'absent':>8}{'unknown':>9}",
    ]
    for entry in doc.get("areas", []):
        subset = [record for record in records if record["area"] == entry["key"]]
        bucket = _counts(record["openstat_status"] for record in subset)
        lines.append(
            f"{entry['key']:<24}{len(subset):>4}  {bucket.get('implemented', 0):>11}"
            f"{bucket.get('partial', 0):>9}{bucket.get('absent', 0):>8}"
            f"{bucket.get('unknown', 0):>9}")
    return "\n".join(lines)


def _format(record: dict) -> str:
    lines = [
        f"{record['name']}  [{record['kind']}, area {record['area']}]",
        f"  status: {record['openstat_status']}",
        f"  grammar: {record['grammar']}",
        f"  summary: {record['summary']}",
        f"  examples: " + "; ".join(record["examples"]),
        f"  openstat: {record['openstat_note']}",
        f"  sources: " + ", ".join(record["sources"]),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="The shared Stata primitive contract: query and validate "
                    "qa/stata_primitives.json.")
    parser.add_argument("--summary", action="store_true",
                        help="print the per-area and per-status counts")
    parser.add_argument("--check", action="store_true",
                        help="validate the schema and every source path")
    parser.add_argument("--primitive", metavar="NAME",
                        help="print one primitive record")
    parser.add_argument("--area", metavar="KEY",
                        help="list the primitive names of one area")
    parser.add_argument("--status", choices=STATUSES,
                        help="list the primitive names with one status")
    parser.add_argument("--json", action="store_true",
                        help="print the selected records as JSON")
    parser.add_argument("--catalogue", type=Path, default=None,
                        help="the catalogue file (default qa/stata_primitives.json)")
    parser.add_argument("--stata-root", type=Path, default=None,
                        help="the Stata help corpus root used for source checks")
    args = parser.parse_args(argv)

    try:
        document = load(args.catalogue)
    except ValueError as error:
        print(f"error: {error}")
        return 2

    if args.check:
        problems = validate(document, stata_root=args.stata_root)
        warnings = [problem for problem in problems if problem.startswith("warning:")]
        failures = [problem for problem in problems if not problem.startswith("warning:")]
        for problem in problems:
            print(problem)
        root = args.stata_root or STATA_BASE
        if failures:
            print(f"FAILED: {len(failures)} problem(s), {len(warnings)} warning(s)")
            return 1
        print(f"OK: {len(document['primitives'])} primitives, every source verified "
              f"against {root} (+{len(warnings)} warning(s))")
        return 0

    if args.primitive:
        try:
            record = primitive(args.primitive, document)
        except PrimitiveError as error:
            print(f"error: {error}")
            return 2
        if args.json:
            print(json.dumps(record, indent=1, ensure_ascii=False))
        else:
            print(_format(record))
        return 0

    if args.summary:
        print(summary(document))
        return 0

    area_keys = [entry["key"] for entry in document.get("areas", [])]
    if args.area is not None and args.area not in area_keys:
        print(f"error: unknown area {args.area!r}; areas are: " + ", ".join(area_keys))
        return 2

    selected = primitives(document, area=args.area, status=args.status)
    if args.json:
        print(json.dumps(selected, indent=1, ensure_ascii=False))
        return 0
    if not selected:
        query = args.area or args.status
        print(f"no primitive matches {query!r}; try --summary")
        return 1
    for record in selected:
        print(f"{record['name']:<28}{record['area']:<20}"
              f"{record['kind']:<16}{record['openstat_status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
