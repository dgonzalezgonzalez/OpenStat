"""Machine-readable manifest of Stata's user-interface surface.

The manifest is built from the declarative UI files of an installed Stata:

* ``ado/base/s/*.mnu``            the menu tree (labels, actions, separators)
* ``ado/base/**/*.dlg``           one file per dialog set (tabs + controls)
* ``ado/base/**/*.idlg``          dialog fragments pulled in by ``INCLUDE``
* ``ado/base/resource/*.resource``id -> UI string table (menus, windows, labels)

Only interface facts are recorded: menu routes, the command an item runs, dialog
tab names, control identifiers and the command option a control edits.  Help
prose is never read.  Label *identifiers* are canonical; the English text is
resolved from the installed resource table for review and can be switched off
with ``--labels none``.

The second half of the manifest is the OpenStat side, derived mechanically from
``openstat/gui.py`` (its menu tree, its command dialogs and which Qt features it
uses), so the gap table cannot drift from the code.

Usage::

    python qa/ui_manifest.py --build                 # write qa/ui_manifest.json
    python qa/ui_manifest.py --check                 # rebuild and diff
    python qa/ui_manifest.py --build --json out.json --root "C:/Program Files/StataNow19"
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import os
import platform
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_JSON = Path(__file__).with_name("ui_manifest.json")

ORACLE_ROOTS = (
    Path(r"C:\Program Files\StataNow19"),
    Path(r"C:\Program Files\Stata19"),
    Path("/Applications/StataNow"),
)
STATA_ENV = "STATA_UI_ROOT"

# Kinds of statement that create a control inside a DIALOG block.  Stata writes
# these keywords in upper case, but lower-case spellings occur, so matching is
# case-insensitive and the recorded type is normalised to upper case.
CONTROL_TYPES = (
    "TEXT", "TEXTBOX", "CHECKBOX", "RADIO", "EDIT", "GROUPBOX", "COMBOBOX",
    "BUTTON", "VARNAME", "VARLIST", "SPINNER", "LISTBOX", "COLOR",
    "FILE", "FRAME", "EXP", "HLINK", "HYPERLINK", "TREEVIEW", "BROWSE",
    "SLIDER", "DATETIME",
)
# Statements that carry the geometry of a dialog and are not interface facts.
IGNORED_STATEMENTS = {
    "VERSION", "DEFINE", "SETSCALE_HADJ", "SETSCALE_VADJ", "REQUIRE", "CLASS",
    "MAIN", "REQUIREVERSION", "DIALOGSCALE", "SCALEFONT", "KEYWORD",
    "POSITION", "COPY",
}
# Statements that own a BEGIN ... END body we do not need to read.  ``LIST``
# declares a named contents/values list; its body is data, not a control.
SKIPPED_BLOCKS = {"SCRIPT", "PROGRAM", "SCRIPTS", "PROGRAMS", "CALL", "LIST"}
# Button presets that name a dialog action rather than a field.
BUTTON_STATEMENTS = {"OK", "CANCEL", "SUBMIT", "HELP", "RESET", "STOPBOX"}
# Attributes that continue the previous control when they start a new line.
ATTRIBUTE_WORDS = (
    "label", "option", "optionarg", "tooltip", "values", "contents", "numeric",
    "string", "fv", "ts", "allowcat", "numonly", "notex", "varlist", "radio",
    "dropdown", "dropdownlist", "min", "max", "default", "format", "readonly",
    "enable", "disable", "varname", "exp", "hidden", "noedit", "multiline",
    "onchange", "onselchange", "onclickon", "onclickoff", "onpush", "first",
    "last", "for", "sp", "tbl", "center", "right", "left",
)

PLATFORM_SUFFIXES = {"WIN": 3, "WINDOWS": 3, "MAC": 2, "UNIX": 1}

# The live window was recorded once with Win32/UIA; these are the observations,
# not a re-run of the UI.  ``observed`` keeps the localised (Spanish) caption.
DOCK_WINDOWS = (
    ("LBL_WINDOW_COMMAND", "Command", "Comando", "dock, top"),
    ("LBL_WINDOW_VARIABLES", "Variables", "Variables", "dock, right"),
    ("LBL_WINDOW_PROPERTIES", "Properties", "Propiedades", "dock, right"),
    ("LBL_WINDOW_RESULTS", "Results", "Resultados", "dock child of Command, RICHEDIT50W"),
    ("LBL_WINDOW_REVIEW", "History", None, "dock, tabbed with Results in Stata's default layout"),
    ("LBL_WINDOW_VIEWER", "Viewer", None, "dock, tabbed"),
    ("LBL_WINDOW_DATA_EDITOR", "Data Editor", None, "separate top-level window"),
    ("LBL_WINDOW_DO_FILE_EDITOR", "Do-file Editor", None, "separate top-level window"),
    ("LBL_WINDOW_GRAPH", "Graph", None, "separate top-level window"),
    ("LBL_WINDOW_VARIABLE_MANAGER", "Variables Manager", None, "separate top-level window"),
    ("LBL_WINDOW_PROJECT_MANAGER", "Project Manager", None, "separate top-level window"),
    ("LBL_WINDOW_SEM_BUILDER", "SEM Builder", None, "separate top-level window"),
    ("LBL_WINDOW_FRAMES", "Frames", None, "separate top-level window"),
)
PANE_VERBS = (
    "LBL_DOCKINGPANE_AUTOHIDE", "LBL_DOCKINGPANE_CLOSE", "LBL_DOCKINGPANE_MAXIMIZE",
    "LBL_DOCKINGPANE_RESTORE", "LBL_DOCKINGPANE_FILTERBAR", "LBL_DOCKINGPANE_MENU",
)
REVIEW_COLUMNS = (
    ("LBL_REVIEW_HEADER_NUM", "row number"),
    ("LBL_REVIEW_HEADER_COMMAND", "executed command"),
    ("LBL_REVIEW_HEADER_RC", "Stata return code"),
)
VARIABLES_COLUMNS = (
    "LBL_VARIABLES_HEADER_VARIABLE", "LBL_VARIABLES_HEADER_LABEL",
    "LBL_VARIABLES_HEADER_TYPE", "LBL_VARIABLES_HEADER_FORMAT",
    "LBL_VARIABLES_HEADER_VALUELABEL",
)
TOOLBARS_OBSERVED = (
    {"name": "Stata Menus", "evidence": "toolbar caption observed with Win32/UIA"},
    {"name": "Stata Toolbar", "evidence": "toolbar caption observed with Win32/UIA"},
)

# Qt/API features the GUI parity work depends on.  ``tokens`` are searched in
# openstat/gui.py so the report never claims a feature the code lacks.
OPENSTAT_FEATURES = (
    ("dock_model", ("QDockWidget", "addDockWidget", "dockWidgetArea"),
     "docks, floating/splittable panes, View > Window roster"),
    ("settings_persistence", ("QSettings",),
     "saved window geometry, dock layout and recent files"),
    ("review_window", ("ReviewPanel", "review_table"),
     "command history as a table with number/command/rc columns"),
    ("viewer_window", ("ViewerPanel", "viewer_text"),
     "Help/Viewer pane with Back/Forward/Reload/Print"),
    ("preferences_dialog", ("PreferencesDialog", "preferences_dialog"),
     "General preferences dialog (Edit > Preferences)"),
    ("statusbar_tabs", ("QSizeGrip",),
     "status bar size grip and permanent fields"),
    ("dialog_generator", ("command_dialog",),
     "per-command dialogs that build a command with options"),
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def read_text(path):
    """Read a Stata UI file, tolerating the install's encoding mix."""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _strip_line_comment(line):
    """Drop a ``//`` comment that is outside quotes."""
    quoted = False
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif not quoted and char == "/" and line[index:index + 2] == "//":
            return line[:index]
    return line


def strip_comments(text):
    """Remove Stata dialog comments, keeping line structure.

    Stata uses ``/*`` at end of line plus ``*/`` at the start of the next line as
    a line continuation, so such a comment collapses to a single space; any
    other block comment keeps its line breaks so statements stay separate.
    """
    out = []
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                break  # unbalanced: drop the rest
            line_start = text.rfind("\n", 0, index) + 1
            after = text.find("\n", end)
            after = length if after < 0 else after
            opens_at_eol = not text[line_start:index].strip()
            closes_at_sol = not text[end + 2:after].strip()
            body = text[index:end + 2]
            out.append(" " if (opens_at_eol and closes_at_sol) else "\n" * body.count("\n"))
            index = end + 2
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def _unbalanced(text):
    """True when a statement clearly continues on the next line."""
    quoted = False
    depth = 0
    for char in text:
        if char == '"':
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
    return quoted or depth > 0


def looks_like_control(statement):
    """True when a statement starts with ``<CONTROL_TYPE> <identifier>``."""
    parts = statement.split(None, 2)
    return (len(parts) >= 2 and parts[0].upper() in CONTROL_TYPES
            and re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", parts[1]) is not None)


def iter_statements(text):
    """Yield ``(line_number, statement)`` with continuations joined.

    A statement continues when it ends with ``///``, when its parentheses or
    quotes are still open, or when the next line is an attribute of the
    previous control (Stata writes ``label(...)``/``option(...)`` on their own
    line after a control's geometry).
    """
    statements = []          # [line_number, text]
    pending = ""
    start = 0
    attribute = re.compile(r"^(%s)\b" % "|".join(ATTRIBUTE_WORDS), re.I)
    for number, raw in enumerate(strip_comments(text).splitlines(), 1):
        line = _strip_line_comment(raw).strip()
        if not pending:
            if not line or line.startswith("*"):
                continue
            start = number
            pending = line
        else:
            pending = (pending + " " + line).strip()
        if pending.endswith("///"):
            pending = pending[:-3].rstrip()
            continue
        if _unbalanced(pending):
            continue
        if (statements and attribute.match(pending)
                and not looks_like_control(pending)):
            statements[-1][1] = statements[-1][1] + " " + pending
        else:
            statements.append([start, pending])
        pending = ""
    if pending:
        statements.append([start, pending])
    for line_number, statement in statements:
        yield line_number, statement


def attr_values(text, name):
    """Yield the raw inner text of every ``name(...)`` in *text*."""
    pattern = re.compile(r"\b%s\s*\(" % re.escape(name), re.I)
    for match in pattern.finditer(text):
        depth = 0
        quoted = False
        start = match.end() - 1
        for index in range(start, len(text)):
            char = text[index]
            if char == '"':
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        yield text[start + 1:index]
                        break


def attr_value(text, name):
    return next(iter(attr_values(text, name)), None)


def _has_word(text, word):
    quoted = False
    for index, char in enumerate(text):
        if char == '"':
            quoted = not quoted
        elif not quoted and text[index:index + len(word)].lower() == word.lower():
            before = text[index - 1] if index else " "
            after = text[index + len(word):index + len(word) + 1]
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return True
    return False


def parse_label_spec(spec):
    """Return ``(label_id, text, kind)`` for a ``label(...)`` argument."""
    if spec is None:
        return None, None, None
    spec = spec.strip()
    resource = attr_value(spec, "resource")
    if resource is not None:
        return resource.strip().strip('"'), None, "resource"
    if spec.lower().startswith("resource "):
        return spec[9:].strip().strip('"'), None, "resource"
    if len(spec) >= 2 and spec.startswith('"') and spec.endswith('"'):
        return None, spec[1:-1], "literal"
    if spec:
        return spec, None, "resource"
    return None, None, None


# ---------------------------------------------------------------------------
# resource label table
# ---------------------------------------------------------------------------
def parse_resource_file(path):
    """Parse ``ID = text`` lines, applying platform suffixes."""
    base = {}
    platform_values = {}
    current = "WIN" if sys.platform == "win32" else "UNIX"
    if sys.platform == "darwin":
        current = "MAC"
    for raw in read_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not value:
            continue
        head, dot, suffix = key.partition(".")
        if dot and suffix.upper() in PLATFORM_SUFFIXES:
            platform_values.setdefault(head, {})[suffix.upper()] = value
        elif dot and suffix.isupper():
            continue  # unknown platform tag: not this build's text
        else:
            base[key] = value
    merged = dict(base)
    for name, values in platform_values.items():
        if current in values:
            merged[name] = values[current]
        elif name not in merged and len(values) == 1:
            merged[name] = next(iter(values.values()))
    return merged


def load_labels(root):
    """Merge the install's resource tables into ``{id: text}``."""
    directory = Path(root) / "ado" / "base" / "resource"
    labels = {}
    stamps = {}
    for name in ("menu", "ui_core", "ui_ex", "dlg", "postest", "style"):
        path = directory / ("%s.resource" % name)
        if not path.is_file():
            continue
        for line in read_text(path).splitlines()[:4]:
            if line.startswith("##"):
                stamps[name] = line.lstrip("#").lstrip(".").strip()
                break
        for key, value in parse_resource_file(path).items():
            labels.setdefault(key, value)
    return labels, stamps


# ---------------------------------------------------------------------------
# .mnu menus
# ---------------------------------------------------------------------------
MNU_DIRECTIVE = re.compile(r"^#(?P<name>[A-Za-z_]+)\b\s*(?P<rest>.*)$")
MNU_NODE = re.compile(
    r'^(?:(?:"(?P<label>[^"]*)"|(?P<bare>[A-Za-z_][A-Za-z0-9_/.-]*))\s+)?'
    r'(?:(?P<ident>[A-Za-z_][A-Za-z0-9_]*)\s*)?\{\s*$')
MNU_ITEM = re.compile(r'^"(?P<label>[^"]*)"\s+(?P<action>.+?)\s*$')


def classify_action(action):
    """Split a menu action into ``(kind, command)``."""
    text = action.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    lowered = text.lower()
    if lowered.startswith("db "):
        return "dialog", text[3:].strip()
    if lowered.startswith("view dialog "):
        return "dialog-view", text[12:].strip()
    if lowered.startswith("view help "):
        return "help", text[10:].strip()
    if lowered.startswith("help "):
        return "help", text[5:].strip()
    if lowered in ("edit", "browse"):
        return "data-editor", lowered
    if lowered in ("varmanage", "sembuilder", "tablesbuilder"):
        return "window", lowered
    if lowered.startswith("view "):
        return "view", text[5:].strip()
    return "command", text


class MenuParser:
    """Parse ``.mnu`` files into nested menu nodes, following ``#include``."""

    def __init__(self, menu_dir, warnings, platform_tags=("WINDOWS",)):
        self.menu_dir = Path(menu_dir)
        self.warnings = warnings
        self.platform_tags = set(platform_tags)
        self.parsed = []

    def parse(self, name, chain=()):
        if name in chain:
            self.warnings["include_cycles"].append(name)
            return []
        path = self.menu_dir / (name if name.endswith(".mnu") else name + ".mnu")
        if not path.is_file():
            self.warnings["missing_includes"].append(str(path.name))
            return []
        self.parsed.append(path.name)
        return self._parse_text(path, chain + (name,))

    def _parse_text(self, path, chain):
        nodes = []          # current level
        stack = [nodes]
        conditions = []
        for number, raw in enumerate(read_text(path).splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("*") or line.startswith("//"):
                continue
            if line == "}" :
                if len(stack) > 1:
                    stack.pop()
                else:
                    self.warnings["unbalanced_braces"].append(
                        "%s:%d" % (path.name, number))
                if conditions and line == "}":
                    conditions.pop()
                continue
            if re.fullmatch(r"-{2,}\s*", line):
                stack[-1].append({"kind": "separator"})
                continue
            directive = MNU_DIRECTIVE.match(line)
            if directive:
                word = directive.group("name").lower()
                rest = directive.group("rest").strip()
                if word == "include":
                    stack[-1].extend(self.parse(rest.split()[0], chain)
                                     if rest else [])
                elif word in ("if", "ifdef", "ifndef"):
                    tag = rest.split("{")[0].strip().upper()
                    conditions.append(tag)
                    if tag in self.platform_tags or not tag:
                        stack.append(stack[-1])  # transparent block
                    else:
                        # skip the block body, keep the level
                        depth = 1
                        stack.append(self._Skip())
                        self.warnings.setdefault("skipped_platform_blocks", []).append(tag)
                        continue
                elif word == "else":
                    continue
                else:
                    self.warnings["unknown_directives"].append("%s: %s" % (path.name, line))
                continue
            node = MNU_NODE.match(line)
            if node:
                child = {
                    "kind": "menu",
                    "label": (node.group("label") or node.group("bare")
                              or node.group("ident")),
                    "id": node.group("ident") or node.group("label") or node.group("bare"),
                    "children": [],
                }
                if isinstance(stack[-1], list):
                    stack[-1].append(child)
                stack.append(child["children"])
                continue
            item = MNU_ITEM.match(line)
            if item:
                action = item.group("action").strip()
                if not action:
                    continue
                kind, command = classify_action(action)
                if kind == "command" and not command:
                    self.warnings["malformed_items"].append(
                        "%s:%d: %s" % (path.name, number, line[:80]))
                    continue
                if not isinstance(stack[-1], list):
                    continue
                stack[-1].append({
                    "kind": "item",
                    "label": item.group("label"),
                    "action": action,
                    "action_kind": kind,
                    "command": command,
                })
                continue
            self.warnings["malformed_items"].append(
                "%s:%d: %s" % (path.name, number, line[:80]))
        return nodes

    class _Skip(list):
        """Marker for a skipped conditional block."""


def parse_menus(root):
    """Return ``(roots, warnings, files)`` for the install's menu files."""
    menu_dir = Path(root) / "ado" / "base" / "s"
    warnings = {
        "missing_includes": [], "include_cycles": [], "unbalanced_braces": [],
        "unknown_directives": [], "malformed_items": [],
    }
    parser = MenuParser(menu_dir, warnings)
    roots = parser.parse("stata")
    if not roots:  # no montage file: read every .mnu independently
        for path in sorted(menu_dir.glob("*.mnu")):
            roots.extend(parser.parse(path.stem))
    for key in list(warnings):
        if not warnings[key]:
            warnings.pop(key)
    return roots, warnings, parser.parsed


def walk_menu(nodes, path=()):
    """Yield ``(path, node)`` for every node in the menu forest."""
    for node in nodes:
        if node.get("kind") == "menu":
            here = path + (node["label"],)
            yield here, node
            yield from walk_menu(node.get("children", []), here)
        else:
            yield path, node


# ---------------------------------------------------------------------------
# .dlg / .idlg dialogs
# ---------------------------------------------------------------------------
class DialogIndex:
    """Resolve ``INCLUDE name`` across the dialog tree."""

    def __init__(self, root):
        self.root = Path(root)
        self.by_stem = {}
        self.by_dir = {}
        for pattern in ("*.dlg", "*.idlg"):
            for path in self.root.rglob(pattern):
                self.by_stem.setdefault(path.stem.lower(), []).append(path)
                self.by_dir.setdefault(path.parent, {})[path.stem.lower()] = path

    def find(self, name, near=None):
        key = name.strip().strip('"').lower()
        key = key[:-5] if key.endswith(".idlg") else key[:-4] if key.endswith(".dlg") else key
        if near is not None:
            match = self.by_dir.get(Path(near).parent, {}).get(key)
            if match:
                return match
        candidates = self.by_stem.get(key)
        return candidates[0] if candidates else None


def parse_control(statement):
    """Parse ``TYPE id geometry..., attributes`` into a control record."""
    head = re.match(
        r"^(?P<type>[A-Za-z][A-Za-z0-9_]*)\s+(?P<id>[A-Za-z_][A-Za-z0-9_]*)",
        statement)
    if not head or head.group("type").upper() not in CONTROL_TYPES:
        return None
    control = {"type": head.group("type").upper(), "id": head.group("id")}
    label_id, literal, kind = parse_label_spec(attr_value(statement, "label"))
    if label_id:
        control["label"] = label_id
    if literal:
        control["text"] = literal
    if kind:
        control["label_kind"] = kind
    option = attr_value(statement, "option")
    if option is not None:
        control["option"] = option.strip().strip('"')
    optionarg = attr_value(statement, "optionarg")
    if optionarg is not None:
        control["optionarg"] = optionarg.strip().strip('"')
    tooltip = attr_value(statement, "tooltip")
    if tooltip is not None:
        tip_id, tip_text, _ = parse_label_spec(tooltip)
        if tip_id:
            control["tooltip"] = tip_id
        elif tip_text:
            control["tooltip_text"] = tip_text
    values = attr_value(statement, "values")
    if values is not None:
        control["values"] = values.strip().strip('"')
    for flag in ("numeric", "fv", "ts", "allowcat", "numonly", "notex", "multiline"):
        if _has_word(statement, flag):
            control[flag] = True
    return control


def parse_dialog_file(path, index, warnings, _depth=0, _chain=()):
    """Parse one ``.dlg``/``.idlg`` file into ``{tabs, loose, includes}``."""
    path = Path(path)
    key = str(path).lower()
    if _depth > 6 or key in _chain:
        warnings.setdefault("include_cycles", []).append(path.name)
        return {"tabs": [], "loose": [], "includes": []}
    result = {"tabs": [], "loose": [], "includes": [], "buttons": []}
    current = None
    skip = 0
    buttons = []
    for _number, statement in iter_statements(read_text(path)):
        word_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)", statement)
        if not word_match:
            continue
        word = word_match.group(1).upper()
        if word in SKIPPED_BLOCKS:
            skip = 1
            continue
        if word == "BEGIN":
            if skip:
                skip += 1
            continue
        if word == "END":
            if skip:
                skip -= 1
                continue
            if current is not None:
                current = None
            continue
        if skip:
            continue
        if word == "INCLUDE":
            rest = statement[len(word):].strip().split()[0] if statement[len(word):].strip() else ""
            if rest:
                result["includes"].append(rest.strip('"'))
                target = index.find(rest, near=path)
                if target is None:
                    warnings.setdefault("missing_includes", []).append(rest)
                    continue
                fragment = parse_dialog_file(target, index, warnings,
                                             _depth + 1, _chain + (key,))
                result["includes"].extend(fragment["includes"])
                if current is not None:
                    current["fields"].extend(fragment["loose"])
                else:
                    result["loose"].extend(fragment["loose"])
                for tab in fragment["tabs"]:
                    if current is not None:
                        current["fields"].extend(tab["fields"])
                    else:
                        result["tabs"].append(tab)
                result["buttons"].extend(fragment["buttons"])
            continue
        if word == "DIALOG":
            label_id, literal, kind = parse_label_spec(attr_value(statement, "label"))
            title_id, title_text, title_kind = parse_label_spec(
                attr_value(statement, "tabtitle"))
            name = (statement[len(word):].strip().split(",")[0] or "").strip()
            current = {
                "name": name or "main",
                "fields": [],
            }
            if label_id:
                current["title"] = label_id
            if literal:
                current["title_text"] = literal
            if title_id:
                current["tab"] = title_id
            if title_text:
                current["tab_text"] = title_text
            if kind:
                current["title_kind"] = "dialog-" + kind
            if title_kind:
                current["tab_kind"] = title_kind
            result["tabs"].append(current)
            continue
        if word in BUTTON_STATEMENTS:
            label_id, literal, _ = parse_label_spec(attr_value(statement, "label"))
            entry = {"kind": word.lower()}
            name = statement[len(word):].strip().split(",")[0].strip()
            if name:
                entry["id"] = name
            if label_id:
                entry["label"] = label_id
            if literal:
                entry["text"] = literal
            if current is not None:
                current.setdefault("buttons", []).append(entry)
            else:
                buttons.append(entry)
            continue
        if word in IGNORED_STATEMENTS:
            continue
        if word in CONTROL_TYPES:
            control = parse_control(statement)
            if control is None:
                warnings.setdefault("malformed_controls", []).append(
                    "%s: %s" % (path.name, statement[:80]))
                continue
            if current is not None:
                current["fields"].append(control)
            else:
                result["loose"].append(control)
            continue
        unknown = warnings.setdefault("unknown_statements", {})
        unknown["count"] = unknown.get("count", 0) + 1
        if len(unknown) <= 40:
            unknown.setdefault(word.lower(), "%s: %s" % (path.name, statement[:80]))
    result["buttons"].extend(buttons)
    return result


def parse_dialogs(root, index, warnings):
    """Return ``(dialogs, files)``: one record per ``.dlg`` file."""
    base = Path(root) / "ado" / "base"
    dialogs = []
    files = sorted(base.rglob("*.dlg"))
    for path in files:
        parsed = parse_dialog_file(path, index, warnings)
        tabs = parsed["tabs"]
        if not tabs and parsed["loose"]:
            tabs = [{"name": "main", "fields": parsed["loose"]}]
        elif tabs and parsed["loose"]:
            tabs[0]["fields"].extend(parsed["loose"])
        title_id = None
        title_text = None
        for tab in tabs:
            if tab.get("title") or tab.get("title_text"):
                title_id = tab.get("title")
                title_text = tab.get("title_text")
                if tab.get("name") == "main":
                    break
        record = {
            "name": path.stem,
            "file": str(path.relative_to(base)).replace("\\", "/"),
            "command": path.stem if not path.stem.endswith("_dlg") else path.stem[:-4],
        }
        if title_id:
            record["title"] = title_id
        if title_text:
            record["title_text"] = title_text
        if parsed["includes"]:
            record["includes"] = sorted(set(parsed["includes"]))
        record["tabs"] = []
        total = 0
        for tab in tabs:
            entry = {"name": tab.get("name", "main")}
            for source, target in (("tab", "tab"), ("tab_text", "tab_text"),
                                   ("title", "title"), ("title_text", "title_text")):
                if tab.get(source):
                    entry[target] = tab[source]
            if tab.get("buttons"):
                entry["buttons"] = tab["buttons"]
            entry["fields"] = tab["fields"]
            total += len(tab["fields"])
            record["tabs"].append(entry)
        if not record["tabs"]:
            continue
        record["field_count"] = total
        dialogs.append(record)
    return dialogs, files


# ---------------------------------------------------------------------------
# the OpenStat side, read from openstat/gui.py
# ---------------------------------------------------------------------------
def _attr_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _literal(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class OpenStatAnalyser:
    def __init__(self, repo_root):
        self.path = Path(repo_root) / "openstat" / "gui.py"
        self.errors = []
        self.actions = {}
        self.menus = []
        self.dialogs = []
        self.toolbars = []
        self.features = {}
        self.source = ""
        self.tree = None

    def run(self):
        if not self.path.is_file():
            self.errors.append("openstat/gui.py not found")
            return self
        self.source = read_text(self.path)
        try:
            self.tree = ast.parse(self.source)
        except SyntaxError as error:  # pragma: no cover - defensive
            self.errors.append("syntax error: %s" % error)
            return self
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == "OpenStatWindow":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "_make_actions":
                        self._actions(item)
                    elif isinstance(item, ast.FunctionDef) and item.name == "_make_menus":
                        self._menus(item)
                    elif isinstance(item, ast.FunctionDef) and item.name == "_make_toolbar":
                        self._toolbar(item)
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call):
                self._maybe_dialog(node)
        self._feature_scan()
        return self

    # -- _make_actions --------------------------------------------------
    def _actions(self, function):
        for node in ast.walk(function):
            targets, value = [], None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                targets, value = node.targets, node.value
            if not isinstance(value, ast.Call):
                continue
            name = _attr_name(value.func)
            if name != "_action":
                continue
            for target in targets:
                attribute = _attr_name(target)
                if not attribute:
                    continue
                text = _literal(value.args[0]) if value.args else None
                shortcut = None
                if len(value.args) > 2:
                    shortcut = _literal(value.args[2])
                keywords = {kw.arg for kw in value.keywords if kw.arg}
                self.actions[attribute] = {
                    "text": text,
                    "shortcut": shortcut,
                    "flags": sorted(keywords),
                    "line": getattr(node, "lineno", None),
                }

    # -- _make_menus ----------------------------------------------------
    def _menus(self, function):
        self._expand(function.body, None)

    def _expand(self, nodes, menu):
        """Walk statements of a function body, filling *menu*."""
        for node in nodes:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = _attr_name(node.targets[0])
                call = node.value
                if isinstance(call, ast.Call) and _attr_name(call.func) == "addMenu":
                    title = _literal(call.args[0]) if call.args else None
                    self.menus.append({"variable": target, "title": title,
                                       "items": [], "separators": 0})
                    continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                func = call.func
                if isinstance(func, ast.Attribute):
                    owner = _attr_name(func.value)
                    menu = self._menu_for(owner)
                    if menu is not None:
                        if func.attr == "addSeparator":
                            menu["separators"] += 1
                            continue
                        if func.attr == "addAction" and call.args:
                            entry = self._menu_item(call.args[0])
                            if entry:
                                menu["items"].append(entry)
                            continue
            if isinstance(node, ast.For):
                expanded = self._expand_iteration(node)
                for entry in expanded:
                    menu = self._menu_for(entry.pop("_owner", None))
                    if menu is not None:
                        menu["items"].append(entry)
                self._expand(node.body, menu)

    def _expand_iteration(self, node):
        iterable = node.iter
        elements = []
        if isinstance(iterable, (ast.Tuple, ast.List)):
            elements = list(iterable.elts)
        body_target = _attr_name(node.target)
        entries = []
        for element in elements:
            if isinstance(element, (ast.Tuple, ast.List)):
                names = [_attr_name(item) for item in element.elts]
                for statement in node.body:
                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                        call = statement.value
                        if not isinstance(call.func, ast.Attribute) or call.func.attr != "addAction":
                            continue
                        owner = _attr_name(call.func.value)
                        if call.args and _literal(call.args[0]) is None:
                            # addAction(action) with an action built in the loop
                            text = names[0] if names else None
                            shortcut = names[1] if len(names) > 1 else None
                            entries.append({"_owner": owner, "text": text,
                                            "shortcut_applied": None})
                        elif call.args:
                            inner = call.args[0]
                            entry = self._menu_item(inner)
                            if entry:
                                entry["_owner"] = owner
                                entries.append(entry)
            elif isinstance(element, ast.Attribute):
                entries.append({"_owner": None, "attr": element.attr})
        # resolve single-element attribute owners from the loop body
        for entry in entries:
            if entry.get("attr") and entry.get("_owner") is None:
                for statement in node.body:
                    if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                        call = statement.value
                        if isinstance(call.func, ast.Attribute) and call.func.attr == "addAction":
                            entry["_owner"] = _attr_name(call.func.value)
        return entries

    def _menu_for(self, variable):
        for menu in self.menus:
            if menu["variable"] == variable:
                return menu
        return None

    def _menu_item(self, node):
        """Describe a menu entry from an ``addAction(...)`` argument."""
        if isinstance(node, ast.Attribute):
            attribute = node.attr
            known = self.actions.get(attribute, {})
            return {"text": known.get("text"), "shortcut": known.get("shortcut"),
                    "action": attribute}
        if isinstance(node, ast.Call):
            name = _attr_name(node.func)
            text = _literal(node.args[0]) if node.args else None
            shortcut = _literal(node.args[2]) if len(node.args) > 2 else None
            entry = {"text": text, "shortcut": shortcut, "action": name}
            if name == "command_dialog":
                entry["dialog"] = self._dialog_from_call(node)
            elif isinstance(node.func, ast.Attribute) and name:
                entry["handler"] = name
            return entry
        return None

    def _dialog_from_call(self, node):
        arguments = list(node.args)
        title = _literal(arguments[0]) if arguments else None
        command = _literal(arguments[1]) if len(arguments) > 1 else None
        fields = []
        if len(arguments) > 2 and isinstance(arguments[2], (ast.List, ast.Tuple)):
            fields = [value for value in (_literal(e) for e in arguments[2].elts) if value]
        return {"title": title, "command": command, "fields": fields,
                "line": getattr(node, "lineno", None)}

    def _maybe_dialog(self, node):
        name = _attr_name(node.func)
        if name == "command_dialog":
            entry = self._dialog_from_call(node)
            if entry["title"] and not any(d.get("line") == entry["line"]
                                          for d in self.dialogs):
                self.dialogs.append(entry)

    def _toolbar(self, function):
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _attr_name(node.func) == "QToolBar":
                title = _literal(node.args[0]) if node.args else None
                self.toolbars.append({"name": title})

    def _feature_scan(self):
        for key, tokens, description in OPENSTAT_FEATURES:
            hits = [token for token in tokens if token in self.source]
            self.features[key] = {
                "present": bool(hits),
                "description": description,
                "evidence": hits,
            }

    def as_dict(self):
        return {
            "source": "openstat/gui.py",
            "menus": [{"title": menu["title"], "items": menu["items"],
                       "separators": menu["separators"]} for menu in self.menus],
            "dialogs": self.dialogs,
            "toolbars": self.toolbars,
            "features": self.features,
            "errors": self.errors,
        }


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------
def oracle_info(root):
    root = Path(root)
    ado = root / "ado" / "base"
    labels, stamps = load_labels(root)
    mnu_versions = {}
    for path in sorted((ado / "s").glob("*.mnu")):
        for line in read_text(path).splitlines()[:3]:
            if line.startswith("*!") and "version" in line.lower():
                mnu_versions[path.name] = line.lstrip("*! ").strip()
                break
    return {
        "root": str(root),
        "ado_base": str(ado),
        "resource_stamp": stamps.get("ui_core") or stamps.get("menu"),
        "mnu_versions": mnu_versions,
        "labels": labels,
    }


def build_manifest(root, repo_root=REPO_ROOT, labels_policy="menus,dialogs"):
    warnings = {}
    menus, menu_warnings, menu_files = parse_menus(root)
    warnings.update(menu_warnings)
    index = DialogIndex(Path(root) / "ado" / "base")
    dialogs, dialog_files = parse_dialogs(root, index, warnings)
    oracle = oracle_info(root)
    known = oracle.pop("labels")
    openstat = OpenStatAnalyser(repo_root).run().as_dict()

    want_menus = "all" in labels_policy or "menus" in labels_policy
    want_dialogs = "all" in labels_policy or "dialogs" in labels_policy
    want_fields = "all" in labels_policy
    used = {}
    unresolved = set()

    def resolve(identifier, keep):
        if not identifier:
            return None
        if not keep:
            return None
        text = known.get(identifier)
        if text is None:
            unresolved.add(identifier)
            return None
        used[identifier] = text
        return text

    def decorate(nodes, path=()):
        out = []
        for node in nodes:
            if node.get("kind") == "menu":
                label = node["label"]
                entry = {
                    "kind": "menu",
                    "label": label,
                    "id": node.get("id"),
                    "path": list(path) + [label],
                }
                text = resolve(label, want_menus)
                if text:
                    entry["text"] = text
                entry["children"] = decorate(node.get("children", []), path + (label,))
                out.append(entry)
            elif node.get("kind") == "item":
                entry = {
                    "kind": "item",
                    "label": node["label"],
                    "path": list(path) + [node["label"]],
                    "action": node["action"],
                    "action_kind": node["action_kind"],
                    "command": node["command"],
                }
                text = resolve(node["label"], want_menus)
                if text:
                    entry["text"] = text
                out.append(entry)
            else:
                out.append({"kind": "separator"})
        return out

    menu_tree = decorate(menus)

    for dialog in dialogs:
        if want_dialogs:
            if dialog.get("title"):
                text = resolve(dialog["title"], True)
                if text:
                    dialog["title_text"] = text
        for tab in dialog["tabs"]:
            if want_dialogs:
                for key, out_key in (("tab", "tab_text"), ("title", "title_text")):
                    if tab.get(key):
                        text = resolve(tab[key], True)
                        if text:
                            tab[out_key] = text
                for button in tab.get("buttons", []):
                    if button.get("label"):
                        text = resolve(button["label"], True)
                        if text:
                            button["text"] = text
            if want_fields:
                for field in tab["fields"]:
                    if field.get("label"):
                        text = resolve(field["label"], True)
                        if text:
                            field["text"] = text

    counts = count_surface(menu_tree, dialogs, dialog_files, menu_files,
                           openstat, used, unresolved)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "script": "qa/ui_manifest.py",
            "python": platform.python_version(),
            "platform": platform.system().lower(),
            "built": _dt.date.today().isoformat(),
            "label_policy": labels_policy,
        },
        "oracle": oracle,
        "sources": {
            "menu_files": menu_files,
            "dialog_files": len(dialog_files),
            "fragment_files": sum(1 for _ in (Path(root) / "ado" / "base").rglob("*.idlg")),
            "menu_roots": [node.get("label") for node in menus if node.get("kind") == "menu"],
        },
        "menus": menu_tree,
        "dialogs": dialogs,
        "window_surface": window_surface(known, want_dialogs),
        "openstat": openstat,
        "coverage": coverage(menu_tree, dialogs, openstat),
        "counts": counts,
        "warnings": warnings,
    }
    return manifest


def window_surface(known, resolve_text):
    def text_of(identifier):
        return known.get(identifier) if resolve_text else None

    return {
        "evidence": "recorded observation of the live Stata 19.5 window "
                    "(Win32/UIA), plus the installed resource label table",
        "framework": {
            "name": "Codejock XTP docking",
            "signals": ["XTPDockingPaneSplitter", "XTP docked panes"],
            "splitters": ["XTPDockingPaneSplitter"],
        },
        "main_window": {
            "menu_bar": ["FILE", "EDIT", "DATA", "GRAPHICS", "STATISTICS", "USER",
                         "WINDOW", "HELP"],
            "menu_bar_source": "compiled into the executable for File/Edit/User/"
                               "Window/Help; the Data, Graphics and Statistics "
                               "trees come from ado/base/s/*.mnu",
        },
        "docks": [
            {"id": identifier, "text": text_of(identifier), "observed": observed,
             "placement": placement}
            for identifier, _english, observed, placement in DOCK_WINDOWS
        ],
        "panes": [
            {"id": "LBL_WINDOW_RESULTS", "text": text_of("LBL_WINDOW_RESULTS"),
             "observed": "Resultados", "widget_class": "RICHEDIT50W",
             "parent": "LBL_WINDOW_COMMAND"},
            {"id": "LBL_WINDOW_VARIABLES", "text": text_of("LBL_WINDOW_VARIABLES"),
             "observed": "Variables", "widget_class": None, "parent": None},
            {"id": "LBL_WINDOW_PROPERTIES", "text": text_of("LBL_WINDOW_PROPERTIES"),
             "observed": "Propiedades", "widget_class": None, "parent": None},
        ],
        "pane_verbs": [{"id": identifier, "text": text_of(identifier)}
                       for identifier in PANE_VERBS],
        "review_columns": [{"id": identifier, "meaning": meaning}
                           for identifier, meaning in REVIEW_COLUMNS],
        "variables_columns": [{"id": identifier, "text": text_of(identifier)}
                              for identifier in VARIABLES_COLUMNS],
        "toolbars": list(TOOLBARS_OBSERVED),
        "toolbar_buttons": [{"id": identifier, "text": text_of(identifier)}
                            for identifier in (
                                "TB_MAIN_BREAK", "TB_MAIN_CLEAR_MORE", "TB_MAIN_LOG",
                                "TB_MAIN_DATA_EDITOR_BROWSE", "TB_MAIN_DATA_EDITOR_EDIT",
                                "TB_MAIN_VIEWER", "TB_MAIN_TOP_VIEWER", "TB_MAIN_GRAPH",
                                "TB_MAIN_GRAPH_TO_FRONT", "TB_MAIN_LAYOUT",
                            ) if known.get(identifier) or not resolve_text],
        "keyboard_shortcuts": {
            "source": "not present in the .mnu/.dlg/.resource files; Stata's menu "
                      "accelerators are compiled into the executable",
            "menu_mnemonic_examples": {
                key: known.get(key) for key in ("FILE", "EDIT", "DATA", "GRAPHICS",
                                                "STATISTICS", "USER", "WINDOW", "HELP")
                if known.get(key)
            } if resolve_text else {},
        },
    }


def coverage(menu_tree, dialogs, openstat):
    """Compare the Stata surface with what openstat/gui.py offers."""
    flat = [node for _path, node in walk_menu(menu_tree)]
    items = [node for node in flat if node.get("kind") == "item"]
    dialog_commands = {dialog["name"] for dialog in dialogs}
    openstat_dialog_commands = {
        entry["command"] for entry in openstat["dialogs"] if entry.get("command")
    }
    openstat_commands = set(openstat_dialog_commands)
    by_kind = {}
    for item in items:
        by_kind[item["action_kind"]] = by_kind.get(item["action_kind"], 0) + 1
    dialogs_with_file = [item for item in items
                         if item["action_kind"] == "dialog"
                         and item["command"].split()[0] in dialog_commands]
    return {
        "stata": {
            "menu_roots": sum(1 for node in menu_tree if node.get("kind") == "menu"),
            "menu_nodes": sum(1 for node in flat if node.get("kind") == "menu"),
            "menu_items": len(items),
            "menu_items_by_action": dict(sorted(by_kind.items())),
            "menu_items_with_dialog_file": len(dialogs_with_file),
            "dialogs": len(dialogs),
            "dialogs_with_option_bindings": sum(
                1 for dialog in dialogs
                for tab in dialog["tabs"]
                for field in tab["fields"] if field.get("option")),
        },
        "openstat": {
            "top_level_menus": len(openstat["menus"]),
            "menu_items": sum(len(menu["items"]) for menu in openstat["menus"]),
            "command_dialogs": len(openstat["dialogs"]),
            "toolbars": len(openstat["toolbars"]),
            "dialogs_backed_by_a_stata_dialog_file": sorted(
                openstat_commands & dialog_commands),
            "features_present": sorted(key for key, value in openstat["features"].items()
                                       if value["present"]),
            "features_missing": sorted(key for key, value in openstat["features"].items()
                                       if not value["present"]),
        },
        "gaps": GAP_TABLE,
    }


GAP_TABLE = [
    {
        "id": "dock_model",
        "stata": "Codejock XTP docking: Command/Variables/Properties docks, "
                 "Results as a child pane, splitter handles, auto-hide and "
                 "floating panes, View roster of windows",
        "openstat": "one fixed QSplitter with three columns; panels cannot be "
                    "moved, floated, hidden or restored by name",
        "status": "missing",
        "plan": "replace the QSplitter columns with QDockWidget instances "
                "(Command, Variables, Properties, Results) and a QMainWindow "
                "dock-area layout; keep the splitter only for the tab area",
    },
    {
        "id": "review_window",
        "stata": "History dock: a table with #, Command and _rc columns, "
                 "filtering and configure",
        "openstat": "QListWidget of command text; no return code column, no "
                    "table view, no per-row error flag in the list",
        "status": "partial",
        "plan": "add a Review model (number, command, rc) and a QTableView dock",
    },
    {
        "id": "viewer_window",
        "stata": "Viewer dock with Back/Forward/Find/Print/Reload and in-app "
                 "help rendering",
        "openstat": "Help opens QMessageBox / a markdown dialog; no Viewer "
                    "dock, no navigation history",
        "status": "missing",
        "plan": "implement a Viewer dock (QTextBrowser) and route `help` to it",
    },
    {
        "id": "preferences",
        "stata": "Edit > Preferences plus a General preferences dialog",
        "openstat": "no preferences surface at all",
        "status": "missing",
        "plan": "add a PreferencesDialog writing through QSettings",
    },
    {
        "id": "settings_persistence",
        "stata": "window geometry, dock layout, recent files and preferences "
                 "survive a restart",
        "openstat": "no QSettings; geometry, splitter sizes and recent files "
                    "are lost on exit",
        "status": "missing",
        "plan": "saveState()/restoreState() plus sizes and recent paths under "
                "QSettings('OpenStat','OpenStat')",
    },
    {
        "id": "menu_accelerators",
        "stata": "every top-level label carries an & accelerator from "
                 "menu.resource (&Data, &Edit, ...)",
        "openstat": "top-level menus carry & mnemonics, but the Edit menu "
                    "creates its QActions without a shortcut argument, so "
                    "Ctrl+Z/X/C/V/A are not advertised in the menu",
        "status": "partial",
        "plan": "pass the shortcut into _action() for the Edit menu and keep "
                "setShortcutVisibleInContextMenu",
    },
    {
        "id": "command_dialogs",
        "stata": "1,799 .dlg files (2,415 tabs) with per-control option bindings",
        "openstat": "10 generated dialogs that concatenate text fields into a "
                    "command line; no control is bound to a command option",
        "status": "missing",
        "plan": "add a declarative dialog spec (title, tabs, fields bound to "
                "option names) and a renderer, starting with regress/summarize",
    },
    {
        "id": "data_editor_ops",
        "stata": "Data Editor with edit/browse modes, sort, filter, pinning, "
                 "variable properties, value-label and format editors",
        "openstat": "Data Browser with an edit toggle and a go-to-observation "
                    "box only",
        "status": "partial",
        "plan": "add sort/filter/pin and a variable-properties dialog to the "
                "Data Browser model",
    },
    {
        "id": "help_subsystem",
        "stata": "help <topic> opens the Viewer on a .sthlp page; 3,463 help "
                 "files are installed",
        "openstat": "help builds a `help` command line; docs/compatibility.md "
                    "is shown in a dialog",
        "status": "partial",
        "plan": "index installed .sthlp files read-only and render them in the "
                "Viewer dock (no StataCorp prose committed)",
    },
]


def count_surface(menu_tree, dialogs, dialog_files, menu_files, openstat,
                  labels_used, unresolved):
    flat = [(path, node) for path, node in walk_menu(menu_tree)]
    items = [node for _path, node in flat if node.get("kind") == "item"]
    by_type = {}
    by_option = 0
    for dialog in dialogs:
        for tab in dialog["tabs"]:
            for field in tab["fields"]:
                by_type[field["type"]] = by_type.get(field["type"], 0) + 1
                if field.get("option"):
                    by_option += 1
    return {
        "menu_files": len(menu_files),
        "menu_roots": sum(1 for node in menu_tree if node.get("kind") == "menu"),
        "menu_nodes": sum(1 for _path, node in flat if node.get("kind") == "menu"),
        "menu_items": len(items),
        "menu_items_with_dialog": sum(1 for node in items
                                      if node["action_kind"] in ("dialog", "dialog-view")),
        "menu_items_with_help": sum(1 for node in items if node["action_kind"] == "help"),
        "menu_separators": sum(1 for _path, node in flat
                               if node.get("kind") == "separator"),
        "dialog_files": len(dialog_files),
        "dialogs": len(dialogs),
        "dialog_tabs": sum(len(dialog["tabs"]) for dialog in dialogs),
        "dialog_fields": sum(len(tab["fields"]) for dialog in dialogs
                             for tab in dialog["tabs"]),
        "dialog_fields_with_option": by_option,
        "field_types": dict(sorted(by_type.items(), key=lambda pair: -pair[1])),
        "labels_resolved": len(labels_used),
        "labels_unresolved": len(unresolved),
        "openstat_menus": len(openstat["menus"]),
        "openstat_menu_items": sum(len(menu["items"]) for menu in openstat["menus"]),
        "openstat_dialogs": len(openstat["dialogs"]),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def find_oracle(explicit=None):
    for candidate in (explicit, os.environ.get(STATA_ENV), *ORACLE_ROOTS):
        if not candidate:
            continue
        path = Path(candidate)
        if (path / "ado" / "base" / "s").is_dir():
            return path
    return None


def dump(manifest, path):
    path = Path(path)
    text = json.dumps(manifest, indent=1, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path, len(text.encode("utf-8"))


def flatten_menu(manifest):
    """``{"DATA > DATA_EDIT": "edit"}`` for a whole menu forest."""
    out = {}

    def walk(nodes, path=()):
        for node in nodes:
            if node.get("kind") == "menu":
                walk(node.get("children", []), path + (node["label"],))
            elif node.get("kind") == "item":
                out[" > ".join(path + (node["label"],))] = node.get("action")

    walk(manifest.get("menus", []))
    return out


def flatten_dialogs(manifest):
    out = {}
    for dialog in manifest.get("dialogs", []):
        fields = []
        for tab in dialog.get("tabs", []):
            fields.extend("%s:%s" % (tab.get("name"), field["id"])
                          for field in tab.get("fields", []))
        out[dialog.get("name")] = "".join([dialog.get("title") or ""]) + "|" + \
            ",".join(fields)
    return out


def diff_manifest(old, new, limit=40):
    differences = []
    old_menus, new_menus = flatten_menu(old), flatten_menu(new)
    for key in sorted(set(old_menus) | set(new_menus)):
        if old_menus.get(key) != new_menus.get(key):
            differences.append(("menu", key, old_menus.get(key), new_menus.get(key)))
    old_dialogs, new_dialogs = flatten_dialogs(old), flatten_dialogs(new)
    for key in sorted(set(old_dialogs) | set(new_dialogs)):
        if old_dialogs.get(key) != new_dialogs.get(key):
            differences.append(("dialog", key,
                                (old_dialogs.get(key) or "-")[:60],
                                (new_dialogs.get(key) or "-")[:60]))
    if old.get("counts") != new.get("counts"):
        for key in sorted(set(old.get("counts", {})) | set(new.get("counts", {}))):
            before = old.get("counts", {}).get(key)
            after = new.get("counts", {}).get(key)
            if before != after:
                differences.append(("count", key, before, after))
    return differences[:limit], len(differences)


REQUIRED_SECTIONS = ("schema_version", "oracle", "menus", "dialogs",
                     "window_surface", "openstat", "coverage", "counts")


def lint(manifest):
    problems = []
    for section in REQUIRED_SECTIONS:
        if section not in manifest:
            problems.append("missing section: %s" % section)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append("schema_version %r != %r"
                        % (manifest.get("schema_version"), SCHEMA_VERSION))
    if not manifest.get("menus"):
        problems.append("menus is empty")
    if not manifest.get("dialogs"):
        problems.append("dialogs is empty")
    # Anchors: a stable menu route and a stable dialog must both be present.
    menus = flatten_menu(manifest)
    if "STATISTICS" not in {key.split(" > ")[0] for key in menus}:
        problems.append("STATISTICS menu root missing")
    if not any(key.startswith("STATISTICS > ") for key in menus):
        problems.append("no STATISTICS submenu route")
    if not any(dialog.get("name") == "regress" for dialog in manifest.get("dialogs", [])):
        problems.append("regress dialog missing")
    return problems


def print_summary(manifest, stream=sys.stdout):
    counts = manifest["counts"]
    surface = manifest["coverage"]["stata"]
    print("menus      : %d files, %d roots, %d submenus, %d items "
          "(%d open a dialog, %d open help)"
          % (counts["menu_files"], counts["menu_roots"], counts["menu_nodes"],
             counts["menu_items"], counts["menu_items_with_dialog"],
             counts["menu_items_with_help"]), file=stream)
    print("dialogs    : %d .dlg files, %d dialogs, %d tabs, %d fields "
          "(%d bound to a command option)"
          % (counts["dialog_files"], counts["dialogs"], counts["dialog_tabs"],
             counts["dialog_fields"], counts["dialog_fields_with_option"]),
          file=stream)
    print("field types: %s" % ", ".join("%s=%d" % (name, value) for name, value
                                        in list(counts["field_types"].items())[:10]),
          file=stream)
    print("labels     : %d resolved, %d unresolved"
          % (counts["labels_resolved"], counts["labels_unresolved"]), file=stream)
    print("openstat   : %d menus, %d items, %d command dialogs, %d toolbars"
          % (counts["openstat_menus"], counts["openstat_menu_items"],
             counts["openstat_dialogs"],
             manifest["coverage"]["openstat"]["toolbars"]), file=stream)
    print("gaps       : %d recorded (%s missing, %s partial)"
          % (len(manifest["coverage"]["gaps"]),
             sum(1 for gap in manifest["coverage"]["gaps"] if gap["status"] == "missing"),
             sum(1 for gap in manifest["coverage"]["gaps"] if gap["status"] == "partial")),
          file=stream)
    print("warnings   : %s" % (", ".join("%s=%d" % (key, len(value))
                                         for key, value in manifest["warnings"].items())
                                or "none"), file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build or check the Stata UI parity manifest")
    parser.add_argument("--build", action="store_true", help="write the manifest")
    parser.add_argument("--check", action="store_true",
                        help="rebuild and compare with the committed manifest")
    parser.add_argument("--json", metavar="PATH", default=str(DEFAULT_JSON),
                        help="manifest path (default: qa/ui_manifest.json)")
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Stata installation root (autodetected)")
    parser.add_argument("--labels", default="menus,dialogs",
                        choices=("none", "menus", "dialogs", "menus,dialogs", "all"),
                        help="which UI strings to resolve from the install")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    target = Path(args.json)

    if not (args.build or args.check):
        args.build = True

    root = find_oracle(args.root)
    if root is None:
        print("no Stata installation found (looked at --root, %s and %s)"
              % (STATA_ENV, ", ".join(str(path) for path in ORACLE_ROOTS)),
              file=sys.stderr)
        if args.check and target.is_file():
            manifest = json.loads(read_text(target))
            problems = lint(manifest)
            if problems:
                print("manifest lint: %d problem(s)" % len(problems), file=sys.stderr)
                for problem in problems:
                    print("  - %s" % problem, file=sys.stderr)
                return 1
            print("manifest lint: ok (offline; %d menus, %d dialogs)"
                  % (manifest["counts"]["menu_items"], manifest["counts"]["dialogs"]))
            return 0
        return 2

    manifest = build_manifest(root, labels_policy=args.labels)
    if not args.quiet:
        print("oracle: %s" % root)
        print_summary(manifest)

    if args.build:
        path, size = dump(manifest, target)
        if not args.quiet:
            print("wrote %s (%.1f KiB)" % (path, size / 1024.0))

    if args.check:
        problems = lint(manifest)
        if target.is_file():
            old = json.loads(read_text(target))
            differences, total = diff_manifest(old, manifest)
            if total:
                print("drift: %d difference(s) against %s" % (total, target))
                for kind, key, before, after in differences:
                    print("  %-6s %-52s %r -> %r" % (kind, key[:52], before, after))
                problems.append("%d drift entries" % total)
        else:
            print("no committed manifest at %s" % target, file=sys.stderr)
            problems.append("missing manifest")
        if problems:
            for problem in problems:
                print("problem: %s" % problem, file=sys.stderr)
            return 1
        print("manifest check: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
