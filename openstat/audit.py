"""Read-only local script inventory; no script is executed or modified.

Reports omit paths and script text by default. Command recognition is a static
inventory of dispatcher branches, not a claim that a file can execute correctly.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable

from .parser import lex, split_outside


SCRIPT_EXTENSIONS = frozenset({".do", ".ado", ".mata"})
EXCLUDED_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", "site-packages", "__pycache__",
    ".local", ".cache", "cache", "__macosx", "build", "dist", ".tox",
})
CONTROL_COMMANDS = frozenset({"foreach", "forvalues", "while", "if", "else", "end"})
PREFIXES = frozenset({"capture", "cap", "quietly", "qui", "noisily", "noi"})


def _runtime_inventory() -> tuple[set[str], dict[str, str]]:
    """Inspect dispatcher source without importing or running the interpreter."""
    commands: set[str] = set(CONTROL_COMMANDS)
    aliases: dict[str, str] = {}
    modules = ("engine", "programming", "extended_data", "output", "panel")
    nodes = (node for module in modules for node in ast.walk(ast.parse(
        Path(__file__).with_name(module + ".py").read_text(encoding="utf-8"))))
    for node in nodes:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "ALIASES" for target in node.targets
        ):
            aliases = ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(
            isinstance(target, ast.Name) and target.id == "handlers" for target in node.targets
        ):
            commands.update(key.value for key in node.value.keys
                            if isinstance(key, ast.Constant) and isinstance(key.value, str))
        if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
            continue
        if node.left.id != "command":
            continue
        for operator, value in zip(node.ops, node.comparators):
            if isinstance(operator, ast.Eq) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                commands.add(value.value)
            elif isinstance(operator, (ast.In, ast.NotIn)) and isinstance(value, (ast.Set, ast.Tuple, ast.List)):
                commands.update(item.value for item in value.elts if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return commands, aliases


def discover_scripts(paths: Iterable[Path | str], exclude: Iterable[str] = ()) -> tuple[list[Path], list[dict]]:
    """Enumerate only script extensions; skip links, metadata and build/cache trees."""
    requested_exclusions = {part.casefold() for part in exclude}
    excluded = EXCLUDED_DIRECTORIES | requested_exclusions
    found: dict[str, Path] = {}
    errors: list[dict] = []

    def add(path: Path):
        if path.suffix.casefold() in SCRIPT_EXTENSIONS and not path.name.startswith("._") and not path.is_symlink():
            found[os.path.normcase(str(path.absolute()))] = path

    for item in paths:
        root = Path(item).absolute()
        if any(part.casefold() in requested_exclusions for part in root.parts):
            continue
        if root.is_file():
            add(root)
        elif root.is_dir():
            def onerror(error):
                errors.append({"path": str(getattr(error, "filename", root)), "reason": type(error).__name__})
            for current, directories, filenames in os.walk(root, followlinks=False, onerror=onerror):
                directories[:] = sorted(name for name in directories if name.casefold() not in excluded and not Path(current, name).is_symlink())
                for filename in sorted(filenames):
                    add(Path(current, filename))
        else:
            errors.append({"path": str(root), "reason": "missing_or_inaccessible"})
    return sorted(found.values(), key=lambda value: str(value).casefold()), errors


def _without_strings(text: str) -> str:
    return re.sub(r'"(?:[^"\n]|"")*"', '""', text)


def _commands(text: str, aliases: dict[str, str]) -> list[tuple[str, str]]:
    """Extract visible prefix chains and commands, retaining inline arguments locally."""
    result = []
    rest = text.lstrip("} ").strip()
    while rest:
        match = re.match(r"([A-Za-z_][\w]*|#\w+)(?=\s|:|$)", rest)
        if not match:
            break
        command = aliases.get(match[1].casefold(), match[1].casefold())
        argument = rest[match.end():].strip()
        if command == "mata" and argument.startswith(":"):
            command = "mata:"
        result.append((command, argument))
        if command in PREFIXES or command in {"capture", "quietly", "noisily"}:
            rest = argument.lstrip(": ")
        elif command in {"by", "bysort", "bootstrap", "jackknife", "permute", "statsby", "svy", "mi"}:
            pieces = split_outside(argument, ":")
            if len(pieces) < 2:
                break
            rest = ":".join(pieces[1:]).strip()
        elif command == "version" and ":" in argument:
            rest = argument.split(":", 1)[1].strip()
        else:
            break
    return result


def analyze_source(source: str, extension: str = ".do", known_commands: set[str] | None = None,
                   aliases: dict[str, str] | None = None) -> dict:
    """Return counts only. Macro expansion and dynamic control flow remain unresolved."""
    if known_commands is None or aliases is None:
        commands, runtime_aliases = _runtime_inventory()
        known_commands = commands if known_commands is None else known_commands
        aliases = runtime_aliases if aliases is None else aliases
    counts: Counter = Counter()
    prefixes: Counter = Counter()
    options: Counter = Counter()
    features: Counter = Counter()
    packages: Counter = Counter()
    dependency_kinds: Counter = Counter()
    definitions: set[str] = set()
    statements = lex(source)
    in_mata = extension.casefold() == ".mata"
    if in_mata:
        features["mata_source"] += 1
    for statement in statements:
        text = statement.text
        visible = _without_strings(text)
        if text.strip().casefold() in {"mata", "mata:"}:
            in_mata = True
            features["mata_block"] += 1
            continue
        if in_mata:
            if text.strip().casefold() == "end":
                in_mata = False
            else:
                features["mata_statement"] += 1
            continue
        if re.search(r"`[^\n]*'|\$\{?[A-Za-z_]", text):
            features["macro_reference"] += 1
        if re.search(r"\b(?:i|ib\d+|ibn|b\d*|c|o)\.[A-Za-z_]|##?", visible):
            features["factor_variables_or_interactions"] += 1
        if re.search(r"\b(?:[LFSD]\d*|[LFSD]\.\([^)]*\))\.[A-Za-z_]", visible, re.IGNORECASE):
            features["time_series_operators"] += 1
        if re.search(r"\[\s*(?:fw|fweight|aw|aweight|pw|pweight|iw|iweight)\s*=", visible, re.IGNORECASE):
            features["weights"] += 1
        if re.search(r"\b(?:r|e|s)\([^)]*\)|\b_[bs]e?\[", visible):
            features["stored_results_or_coefficients"] += 1
        if re.search(r"\b(?:local|loc)\s+\w+\s*:", visible, re.IGNORECASE):
            features["extended_macro_functions"] += 1
        for command, argument in _commands(text, aliases):
            counts[command] += 1
            if command in {"by", "bysort", "capture", "quietly", "noisily", "bootstrap", "jackknife", "permute", "statsby", "svy", "mi"}:
                prefixes[command] += 1
            if command == "program":
                match = re.match(r"(?:define\s+)?([A-Za-z_]\w*)", argument, re.IGNORECASE)
                if match and match[1].casefold() not in {"drop", "list", "dir"}:
                    definitions.add(match[1].casefold())
                    features["program_definition"] += 1
            if command in {"do", "run", "include", "use", "merge", "append", "import", "export", "save", "saveold"}:
                dependency_kinds[command] += 1
                if re.search(r"`[^\n]*'|\$\{?[A-Za-z_]", argument):
                    features["dynamic_dependency_path"] += 1
            if command in {"ssc", "net"}:
                match = re.match(r"inst(?:all)?\s+([A-Za-z_]\w*)", argument, re.IGNORECASE)
                if match:
                    packages[match[1].casefold()] += 1
            parts = split_outside(argument)
            if len(parts) > 1:
                tail = _without_strings(",".join(parts[1:]))
                # Option names only; nested expression names/values are not retained.
                depth = 0
                i = 0
                while i < len(tail):
                    char = tail[i]
                    if char == "(":
                        depth += 1
                    elif char == ")":
                        depth = max(0, depth - 1)
                    elif depth == 0 and (char.isalpha() or char == "_"):
                        match = re.match(r"[A-Za-z_]\w*", tail[i:])
                        options[match[0].casefold()] += 1
                        i += len(match[0])
                        continue
                    i += 1
    unknown = {key: value for key, value in counts.items() if key not in known_commands and key not in definitions}
    return {
        "physical_lines": len(source.splitlines()), "statements": len(statements),
        "commands": dict(counts.most_common()), "unrecognized_commands": dict(Counter(unknown).most_common()),
        "prefixes": dict(prefixes.most_common()), "options": dict(options.most_common()),
        "features": dict(features.most_common()), "packages_requested": dict(packages.most_common()),
        "dependency_kinds": dict(dependency_kinds.most_common()),
        "locally_defined_program_count": len(definitions),
    }


def audit_paths(paths: Iterable[Path | str], *, exclude: Iterable[str] = (),
                include_paths: bool = False, max_bytes: int = 4 * 1024 * 1024) -> dict:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    scripts, discovery_errors = discover_scripts(paths, exclude)
    known_commands, aliases = _runtime_inventory()
    files = []
    errors = []
    totals = {name: Counter() for name in ("commands", "unrecognized_commands", "prefixes", "options", "features", "packages_requested", "dependency_kinds")}
    unique_totals = {name: Counter() for name in totals}
    seen: set[str] = set()
    line_total = statement_total = 0
    for path in scripts:
        record = {}
        if include_paths:
            record["path"] = str(path)
        try:
            if path.stat().st_size > max_bytes:
                raise ValueError("file_exceeds_byte_limit")
            with path.open("rb") as stream:
                data = stream.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("file_exceeds_byte_limit")
            if b"\x00" in data:
                raise ValueError("binary_or_unsupported_encoding")
            digest = hashlib.sha256(data).hexdigest()
            record.update({"id": digest, "bytes": len(data), "extension": path.suffix.casefold(), "duplicate_content": digest in seen})
            encoding = "utf-8-sig"
            try:
                source = data.decode(encoding)
            except UnicodeDecodeError:
                encoding = "cp1252"
                source = data.decode(encoding)
            analysis = analyze_source(source, path.suffix, known_commands, aliases)
            record.update({"encoding": encoding, **analysis})
            for name, total in totals.items():
                total.update(analysis[name])
                if digest not in seen:
                    unique_totals[name].update(analysis[name])
            line_total += analysis["physical_lines"]
            statement_total += analysis["statements"]
            seen.add(digest)
            files.append(record)
        except (OSError, UnicodeError, ValueError) as error:
            # OSError text can contain private source paths; do not include it.
            record["reason"] = str(error) if isinstance(error, ValueError) else type(error).__name__
            errors.append(record)
    for error in discovery_errors:
        errors.append(error if include_paths else {"reason": error["reason"]})
    return {
        "schema_version": 1, "execution_performed": False, "paths_included": include_paths,
        "scope": "Static syntax inventory only; recognition does not establish command, option, numerical, or full-file compatibility. Dynamic macros, dependencies, user programs and package behavior remain unresolved.",
        "files_discovered": len(scripts), "files_analyzed": len(files), "unique_contents": len(seen),
        "duplicate_files": len(files) - len(seen), "physical_lines": line_total,
        "statements": statement_total, "runtime_command_families": sorted(known_commands),
        "totals": {name: dict(value.most_common()) for name, value in totals.items()},
        "unique_content_totals": {name: dict(value.most_common()) for name, value in unique_totals.items()},
        "files": files, "errors": errors,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inventory local do/ado/Mata scripts without executing them. Reports omit paths/source text by default.")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="Write JSON report locally; otherwise print JSON")
    parser.add_argument("--exclude", action="append", default=[], metavar="DIRECTORY_NAME", help="Additional directory basename to skip; repeat as needed")
    parser.add_argument("--include-paths", action="store_true", help="Include private local file paths; keep resulting report private")
    parser.add_argument("--max-bytes", type=int, default=4 * 1024 * 1024, help="Maximum bytes read from each script (default: 4194304)")
    args = parser.parse_args(argv)
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    report = audit_paths(args.paths, exclude=args.exclude, include_paths=args.include_paths, max_bytes=args.max_bytes)
    output = json.dumps(report, indent=2, ensure_ascii=True) + "\n"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(output, encoding="utf-8")
        print(f"Analyzed {report['files_analyzed']} scripts ({report['unique_contents']} unique); {len(report['errors'])} errors. No scripts executed.")
    else:
        print(output, end="")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
