"""Machine-readable inventory of Stata's command interface.

Stata ships its manual as plain-text SMCL ``.sthlp`` help files.  This
module parses that corpus and reports *interface facts* only:

* the subject (command) name and the manual-section family it belongs to,
* the syntax shapes documented in the ``Syntax`` section,
* the option names and, where the help file states one, the option's
  argument type (``numeric``, ``string``, ``integer``, ``namelist``,
  ``varlist``, ...),
* the names of the ``e()`` / ``r()`` / ``s()`` / ``c()`` results a command
  stores.

Licensing
---------
This repository is MIT-licensed and must never contain StataCorp text.  The
parser therefore records interface facts and the *one-line functional title*
of a help file (a few words).  Help paragraphs, examples, option
descriptions and any other prose are never copied into the output.  Option
and command names are the functional interface and are recorded verbatim.

Public API
----------
``parse_help(path)``
    Parse one ``.sthlp`` file into a dictionary of interface facts.
``command_index(root)``
    Walk a corpus directory and return ``{command_name: {...}}``.
``scan_corpus(root)``
    Same as :func:`command_index` but also returns per-file statistics
    (files seen, files parsed, files skipped).
``build_targets(root, ...)`` / ``write_targets(root, out, ...)``
    Produce the ``qa/stata_targets.json`` document.
``implementation_gap(repo_root, index)``
    Compare the Stata command index with the commands OpenStat dispatches,
    and report the ones OpenStat does not implement.
``report_markdown(root, index, stats, gap)``
    Return the human readable summary written to ``qa/stata_targets.md``.

The parser never raises on a malformed file: unparsable files are counted as
skipped and reported in the ``counts`` block of the generated document.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
from pathlib import Path

__all__ = [
    "parse_help",
    "command_index",
    "scan_corpus",
    "build_targets",
    "write_targets",
    "implementation_gap",
    "main",
]

SCHEMA_VERSION = 1
GENERATOR = "qa/stata_help.py"

LICENSING_NOTE = (
    "Interface facts only: subject/command names, option names with their "
    "declared argument type, syntax shapes, stored-result names, and the "
    "one-line functional title of each help file. No help paragraphs, "
    "examples or option descriptions are copied."
)

#: Maximum number of words kept from a help file's one-line title.
MAX_TITLE_WORDS = 8

#: Maximum number of syntax shapes kept per command.
MAX_SYNTAX = 8

# ---------------------------------------------------------------------------
# SMCL rendering
# ---------------------------------------------------------------------------

#: Markers delimiting an option tag inside rendered text.
_OPT_OPEN, _OPT_CLOSE = "\x02", "\x07"

#: Markers delimiting a ``synopt`` table entry inside rendered text.
_SYN_OPEN, _SYN_CLOSE = "\x01", "\x08"

#: Markers delimiting a command token inside rendered text.
_CMD_OPEN, _CMD_CLOSE = "\x03", "\x06"

#: Markers delimiting a column directive inside rendered text.
_COL_OPEN, _COL_CLOSE = "\x04", "\x05"

#: Markers delimiting text that came from a markup directive.
_TXT_OPEN, _TXT_CLOSE = "\x0e", "\x0f"

#: Short names for the opening markers.
_OPT_MARK, _SYN_MARK = _OPT_OPEN, _SYN_OPEN
_CMD_MARK, _COL_MARK, _TXT_MARK = _CMD_OPEN, _COL_OPEN, _TXT_OPEN

#: All internal markers, removed before text leaves the module.
_MARKS = (_OPT_OPEN, _OPT_CLOSE, _SYN_OPEN, _SYN_CLOSE, _CMD_OPEN, _CMD_CLOSE,
          _TXT_OPEN, _TXT_CLOSE, _COL_OPEN, _COL_CLOSE)

#: Directives that introduce a line break.
_LINE_BREAK = {
    "p", "phang", "phang2", "phang3", "pmore", "pmore2", "pmore3",
    "pstd", "pin", "psee", "hline", "break", "p2line",
}

#: Directives that carry no interface information at all.
_DROP = {
    "smcl", "p_end", "p2colreset", "p2colset", "synoptline", "synopthdr",
    "synoptset", "syntab", "dlgtab", "marker", "title", "vieweralsosee",
    "viewerjumpto", "viewerdialog", "include", "manhelp", "mansection",
    "findalias", "txt", "res", "com", "err", "rm",
}

#: Directives whose content is rendered verbatim.
_VERBATIM = {
    "cmd", "bf", "hi", "ul", "center", "right", "bind", "p2col", "ccl",
    "it", "sf",
}

#: Characters produced by the ``{c ...}`` escape directive.
_C_ESCAPES = {
    "-(": "(", ")-": ")", "|": "|", "=": "=", ">": ">", "<": "<",
    "#": "#", "(": "(", ")": ")",
}

#: Bare placeholders that stand for a documented Stata construct.
_PLACEHOLDERS = {"ifin": "[if] [in]"}

_DIRECTIVE_RE = re.compile(r"\{([^{}]*)\}")
_OPTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?::([A-Za-z_][A-Za-z0-9_]*))?(.*)$", re.S)
_OPTION_TAG_RE = re.compile(r"\{opt(?:h)?\s+([^}]*)\}")
_CMD_TAG_RE = re.compile(r"\{cmd:([^}]*)\}")


def _option_parts(content: str):
    """Split an ``{opt ...}`` payload into ``(name, argument)``.

    Stata writes the *minimum abbreviation* of an option before the colon
    and the remainder of the word after it, so ``nocon:tant`` is the option
    ``noconstant`` and ``nogen:erate`` is ``nogenerate``.  The argument is
    the text inside the parentheses that follow the name, if any.
    """
    text = (content or "").strip()
    match = _OPTION_RE.match(text)
    if not match:
        return None, None
    name = match.group(1) + (match.group(2) or "")
    rest = match.group(3).strip()
    if rest.startswith(":"):
        rest = rest[1:].strip()
    argument = None
    if rest.startswith("("):
        depth = 0
        for index, char in enumerate(rest):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    argument = rest[1:index]
                    break
        else:
            argument = rest[1:]
    return name, argument


#: Canonical argument-type names for the spellings used by Stata's help.
_TYPE_WORDS = {
    "#": "numeric", "numlist": "numlist", "number": "numeric",
    "numeric": "numeric", "n": "numeric", "real": "numeric",
    "frac": "numeric", "level": "numeric", "cilevel": "numeric",
    "crlevel": "numeric", "percent": "numeric",
    "varlist": "varlist", "newvarlist": "newvarlist", "tsvarlist": "varlist",
    "clustvarlist": "varlist", "tvar": "varlist",
    "varname": "varname", "newvar": "varname", "newvarname": "varname",
    "varname_o": "varname", "newvar_o": "varname", "depvar": "varname",
    "name": "name", "newname": "name", "eqname": "name", "eqlist": "namelist",
    "namelist": "namelist", "constraints": "namelist",
    "string": "string", "strings": "string", "str#": "string", "strl": "string",
    "text": "string", "style": "string", "colorstyle": "string",
    "integer": "integer", "int": "integer",
    "anything": "anything", "passthru": "string",
    "filename": "filename", "file": "filename", "fname": "filename",
    "directory": "filename", "path": "filename", "cssfile": "filename",
    "matname": "matname", "matrix": "matname", "cname": "cname",
    "framename": "name", "exp": "exp", "lhs": "exp", "rhs": "exp",
    "mataexpr": "exp", "plot": "string", "mata": "name",
}


def _argument_type(argument):
    """Return the canonical type name for an option argument, or ``None``."""
    if not argument:
        return None
    text = argument.strip()
    if not text:
        return None
    # ``strings:string`` and ``regress##vcetype:vcetype`` name a type after
    # the colon; a numeric range such as ``1:2`` is kept verbatim.
    text = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*(?:#+[A-Za-z_][A-Za-z0-9_]*)*:", "", text)
    text = text.strip().lower()
    if not text:
        return None
    return _TYPE_WORDS.get(text, text)


def _directive(raw: str) -> str:
    """Render a single SMCL directive (``{...}`` without the braces)."""
    raw = raw.strip()
    if not raw or raw == "..." or raw.startswith("*"):
        return ""
    match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", raw)
    if not match:
        return ""
    name = match.group(1).lower()
    rest = raw[match.end():]
    colon = rest.startswith(":")
    content = (rest[1:] if colon else rest).strip()

    if name in ("opt", "opth"):
        option, argument = _option_parts(content)
        if not option:
            return ""
        text = option if not argument else "%s(%s)" % (option, argument)
        return _CMD_OPEN + _OPT_OPEN + text + _OPT_CLOSE + _CMD_CLOSE
    if name == "synopt":
        text = content.strip()
        if text.startswith(":"):
            text = text[1:].strip()
        return _SYN_OPEN + text + _SYN_CLOSE
    if name in _DROP:
        return ""
    if name in _LINE_BREAK:
        return "\n"
    if name == "cmd":
        return _CMD_OPEN + content + _CMD_CLOSE
    if name in ("cmdab", "optab"):
        parts = content.split(":")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return _CMD_OPEN + parts[0] + parts[1] + _CMD_CLOSE
        return _CMD_OPEN + content + _CMD_CLOSE
    if name == "c":
        return _C_ESCAPES.get(content.strip(), content.strip())
    if name == "space":
        count = content.strip().split()
        try:
            return " " * int(count[0]) if count else " "
        except ValueError:
            return " "
    if name == "tab":
        return "\t"
    if name == "col":
        return _COL_OPEN + content.strip() + _COL_CLOSE
    if name in ("help", "helpb"):
        target = content.split(":", 1)[0].strip()
        if "##" in target:
            target = target.rsplit("##", 1)[1]
        else:
            words = target.split()
            target = words[-1] if words else ""
        return _TXT_OPEN + target + _TXT_CLOSE
    if name == "search":
        return content.split(":", 1)[1].strip() if ":" in content else ""
    if name == "browse":
        quoted = re.match(r'"[^"]*"\s*:\s*(.*)$', content, re.S)
        return quoted.group(1).strip() if quoted else ""
    if name in _VERBATIM:
        return _TXT_OPEN + content + _TXT_CLOSE
    if colon:
        # Unknown directive with a payload: drop it rather than risk
        # copying prose into the output.
        return ""
    # A bare word is a placeholder such as {depvar}, {varlist} or {ifin}.
    word = _PLACEHOLDERS.get(match.group(1), match.group(1))
    return _TXT_OPEN + word + _TXT_CLOSE


def _render(text: str) -> str:
    """Render SMCL markup to plain text, keeping the internal markers."""
    previous = None
    current = text or ""
    while current != previous:
        previous = current
        current = _DIRECTIVE_RE.sub(lambda m: _directive(m.group(1)), current)
    return current


def _plain(text: str) -> str:
    """Render SMCL to plain text and drop the internal markers."""
    rendered = _render(text)
    for mark in _MARKS + (_COL_MARK,):
        rendered = rendered.replace(mark, "")
    return rendered


def _strip_marks(text: str) -> str:
    for mark in _MARKS:
        text = text.replace(mark, "")
    return _strip_col_marks(text)


# ---------------------------------------------------------------------------
# File level parsing
# ---------------------------------------------------------------------------

_P2COL_RE = re.compile(r"\{p2col(?!set)\s*:")
_HEADER_TOKEN_RE = re.compile(r"\{(bf|hi|cmd|cmdab|optab)(?::|h)([^{}]*)\}")
_TITLE_RE = re.compile(r"\{title:([^}]*)\}")
_RESULT_RE = re.compile(r"\{cmd:([ersc])\(([A-Za-z0-9_]+)")
_SECTION_SYNTAX = re.compile(r"^syntax\b")
_SECTION_OPTIONS = re.compile(r"^options?\b")
_SECTION_RESULTS = re.compile(r"^(stored results?|results)\b")


def _read_text(path) -> str:
    """Read a help file, tolerating the encodings Stata uses."""
    data = Path(path).read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _match_brace(text: str, start: int) -> int:
    """Return the index of the brace closing the one at ``start``."""
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _header(text: str, stem: str):
    """Return ``(subject, family, title)`` for a help file.

    The manual header of a help file names the manual section the subject
    belongs to, the subject itself and a one-line functional title.  The
    subject is taken from the first marked token of the header, so that
    ``{cmd:args} (in {bf:[P] syntax})`` documents ``args`` and not
    ``syntax``.
    """
    family = None
    subject = None
    title = ""
    for match in _P2COL_RE.finditer(text):
        end = _match_brace(text, match.start())
        if end < 0:
            continue
        block = text[match.start():end + 1]
        if "{hline 2}" not in block:
            # Not the manual header but a table inside the file.
            continue
        inner = text[match.end():end]
        bracket = re.search(r"\[([^\]]*)\]", inner)
        if bracket:
            family = bracket.group(1).strip() or None
        for tag in _HEADER_TOKEN_RE.finditer(inner):
            kind = tag.group(1)
            payload = tag.group(2).strip()
            if kind == "cmdab":
                parts = payload.split(":")
                payload = parts[0] + parts[1] if len(parts) > 1 else payload
            if kind in ("bf", "hi"):
                if not payload.startswith("["):
                    continue
                payload = payload[payload.find("]") + 1:].strip()
                if not payload:
                    continue
                subject = payload
                break
            if payload and not payload.startswith("["):
                subject = payload
                break
        following = re.match(r"\s*(?P<title>.*?)\{p_end\}", text[end + 1:], re.S)
        if following:
            title = _normalise_title(_plain(following.group("title")))
        break
    if not subject:
        return stem, family, title or _title_section(text)
    return subject, family, title or _title_section(text)


def _normalise_title(title: str) -> str:
    """Collapse a title to a short, single line of a few words."""
    text = re.sub(r"\s+", " ", title or "").strip()
    words = text.split(" ")
    if len(words) > MAX_TITLE_WORDS:
        text = " ".join(words[:MAX_TITLE_WORDS])
    return text


def _title_section(text: str) -> str:
    """Return the one-line title of a file that has no manual header."""
    for name, body in _sections(text):
        if name.strip().lower() != "title":
            continue
        for line in _plain(body).splitlines():
            line = re.sub(r"\s+", " ", line).strip()
            if line:
                return _normalise_title(line)
    return ""


def _sections(text: str):
    """Split a help file into ``(section_name, section_text)`` pairs."""
    matches = list(_TITLE_RE.finditer(text))
    result = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1).strip(), text[start:end]))
    return result


def _paragraphs(rendered: str):
    """Split rendered text into non-empty paragraphs."""
    for block in re.split(r"\n\s*\n", rendered):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines:
            yield " ".join(lines)


def _strip_col_marks(text: str) -> str:
    """Remove the column markers from a line, keeping the column text."""
    return re.sub(re.escape(_COL_OPEN) + r"[^" + re.escape(_COL_CLOSE) + r"]*" + re.escape(_COL_CLOSE), " ", text)


def _is_reference_line(line: str) -> bool:
    """True for the right-hand column of a two column syntax layout."""
    numbers = [int(value) for value in re.findall(re.escape(_COL_OPEN) + r"(\d+)" + re.escape(_COL_CLOSE), line)]
    if not any(number >= 20 for number in numbers):
        return False
    return _CMD_MARK not in line


def _split_command_lines(text, subject_key: str):
    """Split a paragraph at every occurrence of the documented command."""
    if isinstance(text, (list, tuple)):
        text = " ".join(text)
    pattern = re.compile(
        re.escape(_CMD_OPEN) + r"(?:"
        + re.escape(_OPT_OPEN) + re.escape(subject_key) + re.escape(_OPT_CLOSE)
        + r"|" + re.escape(subject_key) + re.escape(_CMD_CLOSE) + r")",
        re.IGNORECASE)
    parts = []
    start = 0
    for match in pattern.finditer(text):
        if match.start() > start:
            parts.append(text[start:match.start()])
        start = match.start()
    parts.append(text[start:])
    return [part for part in parts if part.strip()]


def _syntax_candidates(rendered: str, subject: str):
    """Return the command lines of a rendered Syntax section, with marks.

    The syntax diagrams of a help file are paragraphs; a diagram is split
    at every occurrence of the documented command so that a two column
    layout yields one candidate per command.  A candidate has to start
    with a command token that the help file marked up as one (``{cmd}``,
    ``{cmdab}`` or ``{opt}``), so prose such as "Describe data in memory"
    never becomes a syntax line.
    """
    subject_key = (subject or "").strip().lower()
    if not subject_key or " " in subject_key:
        return []
    kept = []
    for raw in rendered.splitlines():
        line = raw.strip()
        if not line or _SYN_MARK in line:
            kept.append("")
            continue
        if _is_reference_line(line):
            continue
        kept.append(_strip_col_marks(line).strip())
    candidates = []
    for paragraph in _paragraphs("\n".join(kept)):
        for segment in _split_command_lines(paragraph, subject_key):
            text = segment.strip()
            if not text.startswith(_CMD_MARK):
                continue
            head = _strip_marks(text).split(" ")[0].lower().rstrip(":")
            if head != subject_key:
                continue
            candidates.append(text)
    return candidates


_MARKED_RE = re.compile(
    re.escape(_OPT_OPEN) + r".*?" + re.escape(_OPT_CLOSE) + "|"
    + re.escape(_SYN_OPEN) + r".*?" + re.escape(_SYN_CLOSE) + "|"
    + re.escape(_TXT_OPEN) + r".*?" + re.escape(_TXT_CLOSE) + "|"
    + re.escape(_COL_OPEN) + r".*?" + re.escape(_COL_CLOSE) + "|"
    + re.escape(_CMD_OPEN) + r".*?" + re.escape(_CMD_CLOSE), re.S)


def _plain_words(text: str):
    """Return the words of a line that did not come from any markup.

    A syntax diagram is built from markup: placeholders, option names and
    command names.  The words that are left over are prose, and a line
    that is mostly prose is a sentence, not a syntax shape.
    """
    return [word for word in _MARKED_RE.sub(" ", text).split()
            if any(char.isalnum() for char in word)]


def _looks_like_syntax(candidate: str) -> bool:
    """Reject sentences that happen to start with the command name."""
    if len(_plain_words(candidate)) > 2:
        return False
    line = _strip_marks(candidate)
    if len(line.split()) > 3 and not any(char in line for char in "[(|=,"):
        return False
    return True


def _syntax_lines(rendered: str, subject: str):
    """Return the syntax shapes documented in a rendered Syntax section."""
    found = []
    for candidate in _syntax_candidates(rendered, subject):
        if not _looks_like_syntax(candidate):
            continue
        line = re.sub(r"\s+", " ", _strip_marks(candidate)).strip()
        if not line:
            continue
        if line not in found:
            found.append(line)
        if len(found) >= MAX_SYNTAX:
            break
    return found


def _option_chunks(rendered: str):
    """Yield ``(name, argument)`` for every option tag in rendered text."""
    pattern = re.escape(_OPT_OPEN) + r"([^" + _OPT_CLOSE + r"]*)" + re.escape(_OPT_CLOSE)
    for chunk in re.findall(pattern, rendered):
        option, argument = _option_parts(chunk)
        if option:
            yield option, argument


def _leading_token(text: str):
    """Return ``(kind, payload)`` for the marked token a paragraph starts with.

    ``kind`` is ``"opt"`` for an ``{opt}`` tag and ``"cmd"`` for a ``{cmd}``
    tag; ``payload`` is the text of the tag.
    """
    if not text.startswith(_CMD_OPEN):
        return None, None
    text = text[1:]
    if text.startswith(_OPT_OPEN):
        end = text.find(_OPT_CLOSE, 1)
        if end < 0:
            return None, None
        return "opt", text[1:end]
    end = text.find(_CMD_CLOSE, 1)
    if end < 0:
        return None, None
    return "cmd", text[:end]


#: An option named by a ``{cmd:...}`` tag, with an optional argument.
_CMD_OPTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\(([^()]*)\))?$")


def _command_token_option(payload: str, subject: str):
    """Return ``(name, argument)`` when a ``{cmd:...}`` tag names an option.

    Stata writes option names with ``{cmd:...}`` as often as with
    ``{opt:...}``; a command name is never an option of itself.
    """
    text = (payload or "").strip()
    if re.match(r"^[ersc]\(.*\)$", text):
        return None, None
    match = _CMD_OPTION_RE.match(text)
    if not match:
        return None, None
    name = match.group(1)
    if subject and name.lower() == subject.lower():
        return None, None
    argument = match.group(2)
    if argument is not None and not re.match(r"^[A-Za-z0-9_#%.]+$", argument):
        argument = None
    return name, argument


def _declared_options(rendered: str, subject: str):
    """Return the options declared at the start of an Options paragraph."""
    declared = []
    for paragraph in _paragraphs(rendered):
        text = paragraph.strip()
        kind, payload = _leading_token(text)
        if kind == "opt":
            for option, argument in _option_chunks(text):
                if subject and option.lower() == subject.lower():
                    continue
                if (option, argument) not in declared:
                    declared.append((option, argument))
        elif kind == "cmd":
            option, argument = _command_token_option(payload, subject)
            if option and (option, argument) not in declared:
                declared.append((option, argument))
    return declared


def _table_options(text: str, subject: str):
    """Return the options listed in the tabulated tables of a section.

    Only option tags that sit inside a ``{synopt}`` or ``{p2col}`` table
    are taken, so that option-like words used in prose (for example the
    variance estimators of ``vce()``) are not mistaken for options.
    """
    options = []
    for match in re.finditer(r"\{(synopt|p2col)\b", text):
        end = _match_brace(text, match.start())
        inner = text[match.start():end + 1] if end > 0 else ""
        for tag in _OPTION_TAG_RE.finditer(inner):
            option, argument = _option_parts(tag.group(1))
            if not option or (subject and option.lower() == subject.lower()):
                continue
            if (option, argument) not in options:
                options.append((option, argument))
        for tag in _CMD_TAG_RE.finditer(inner):
            option, argument = _command_token_option(tag.group(1), subject)
            if option and (option, argument) not in options:
                options.append((option, argument))
    return options


def _stored_results(text: str):
    """Return the ``e()``/``r()``/``s()``/``c()`` names in a section."""
    results = []
    for match in _RESULT_RE.finditer(text):
        scope = match.group(1)
        name = match.group(2)
        tail = text[match.end():match.end() + 12]
        if tail.startswith("}{it:#}"):
            name += "#"
        full = "%s(%s)" % (scope, name)
        if full not in results:
            results.append(full)
    return results


def parse_help(path) -> dict:
    """Parse one ``.sthlp`` help file into a dictionary of interface facts.

    Returns a dictionary with the keys ``path``, ``subject``, ``family``,
    ``title``, ``syntax``, ``options``, ``stored_results`` and ``sections``.
    ``options`` is a list of ``{"name": str, "type": str | None}`` records.
    The function never raises: a file that cannot be parsed comes back with
    ``ok`` set to ``False`` and empty collections.
    """
    path = Path(path)
    record = {
        "path": str(path),
        "subject": path.stem,
        "family": None,
        "title": "",
        "syntax": [],
        "options": [],
        "stored_results": [],
        "sections": [],
        "ok": False,
    }
    try:
        text = _read_text(path)
    except (OSError, ValueError):
        return record
    try:
        subject, family, title = _header(text, path.stem)
        record["subject"] = subject
        record["family"] = family
        record["title"] = title
        sections = _sections(text)
        record["sections"] = [name for name, _ in sections]

        syntax = []
        options = []
        results = []
        for name, body in sections:
            key = name.strip().lower()
            # A tabulated option list is an option list wherever it sits.
            for option, argument in _table_options(body, subject):
                options.append((option, argument))
            if _SECTION_SYNTAX.match(key):
                for line in _syntax_lines(_render(body), subject):
                    if line not in syntax:
                        syntax.append(line)
                for candidate in _syntax_candidates(_render(body), subject):
                    for option, argument in _option_chunks(candidate):
                        if subject and option.lower() == subject.lower():
                            continue
                        options.append((option, argument))
            elif _SECTION_OPTIONS.match(key):
                for option, argument in _declared_options(_render(body), subject):
                    options.append((option, argument))
            elif _SECTION_RESULTS.match(key):
                for result in _stored_results(body):
                    if result not in results:
                        results.append(result)
        record["syntax"] = syntax[:MAX_SYNTAX]
        merged = {}
        for option, argument in options:
            if option not in merged or (merged[option] is None and argument):
                merged[option] = argument
        record["options"] = [
            {"name": name, "type": _argument_type(argument)}
            for name, argument in merged.items()
        ]
        record["stored_results"] = results
        record["ok"] = True
    except Exception:  # pragma: no cover - a malformed file must not stop the scan
        return record
    return record


# ---------------------------------------------------------------------------
# Corpus level
# ---------------------------------------------------------------------------

def _relative(path: Path, root: Path) -> str:
    try:
        return os.path.relpath(str(path), str(root))
    except ValueError:  # pragma: no cover - different drives on Windows
        return path.name


def scan_corpus(root):
    """Walk a help corpus and return ``(index, stats)``.

    ``index`` maps a subject name to the interface facts of the help file
    that documents it.  When several files document the same subject (a
    command and its postestimation page, or a page that documents the
    syntax of an earlier release) the file named after the subject wins and
    the others are listed in ``help_files``; their facts are not mixed in,
    so that a superseded syntax never passes for the current one.

    ``stats`` reports how many files were seen, parsed and skipped.
    """
    root = Path(root)
    grouped = {}
    order = []
    stats = {"files": 0, "parsed": 0, "skipped": 0, "commands": 0,
             "skipped_files": [], "folders": {}}
    if not root.is_dir():
        return {}, stats
    for path in sorted(root.rglob("*.sthlp")):
        if not path.is_file():
            continue
        stats["files"] += 1
        folder = _relative(path, root).split(os.sep)[0]
        stats["folders"][folder] = stats["folders"].get(folder, 0) + 1
        try:
            record = parse_help(path)
        except Exception:  # pragma: no cover - parse_help already guards
            record = {"subject": path.stem, "ok": False}
        if not record.get("ok"):
            stats["skipped"] += 1
            if len(stats["skipped_files"]) < 50:
                stats["skipped_files"].append(_relative(path, root))
            continue
        stats["parsed"] += 1
        subject = record["subject"] or path.stem
        if subject not in grouped:
            grouped[subject] = []
            order.append(subject)
        grouped[subject].append((path, record))

    index = {}
    for subject in order:
        documents = grouped[subject]
        primary = None
        for path, record in documents:
            if path.stem == subject:
                primary = (path, record)
                break
        if primary is None:
            primary = documents[0]
        path, record = primary
        entry = {
            "help": _relative(path, root),
            "title": record["title"],
            "family": record["family"],
            "syntax": list(record["syntax"]),
            "options": list(record["options"]),
            "stored_results": list(record["stored_results"]),
        }
        if len(documents) > 1:
            entry["help_files"] = [_relative(item[0], root) for item in documents]
        index[subject] = entry
    stats["commands"] = len(index)
    return index, stats


def command_index(root) -> dict:
    """Return ``{command_name: {...}}`` for a help corpus directory.

    Each record holds ``help`` (the path of the help file relative to
    ``root``), ``title``, ``family``, ``syntax``, ``options`` and
    ``stored_results``.
    """
    index, _ = scan_corpus(root)
    return index


def build_targets(root, source_version="StataNow 19.5", notes=None) -> dict:
    """Build the ``stata_targets.json`` document for a help corpus."""
    index, stats = scan_corpus(root)
    commands = {}
    for name in sorted(index):
        entry = index[name]
        commands[name] = {
            "help": entry["help"],
            "title": entry["title"],
            "family": entry["family"],
            "syntax": entry["syntax"],
            "options": entry["options"],
            "stored_results": entry["stored_results"],
        }
        if entry.get("help_files"):
            commands[name]["help_files"] = entry["help_files"]
    document = {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "source_root": str(root),
        "source_version": source_version,
        "counts": {
            "files": stats["files"],
            "parsed": stats["parsed"],
            "skipped": stats["skipped"],
            "commands": stats["commands"],
        },
        "notes": list(notes or []),
        "commands": commands,
    }
    return document


def write_targets(root, out_path, source_version="StataNow 19.5", notes=None) -> dict:
    """Write ``stata_targets.json`` and return the document that was written."""
    document = build_targets(root, source_version=source_version, notes=notes)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, ensure_ascii=True, separators=(",", ":"))
        stream.write("\n")
    return document


# ---------------------------------------------------------------------------
# Comparison with the OpenStat implementation
# ---------------------------------------------------------------------------

#: Names that look like a command name (used to recognise dispatch tables).
_COMMAND_LIKE = {
    "about", "adopath", "append", "areg", "args", "assert", "bar", "box",
    "by", "bysort", "capture", "cd", "centile", "clear", "codebook",
    "collapse", "compress", "confirm", "contract", "correlate", "count",
    "cross", "decode", "describe", "destring", "dir", "display", "do",
    "dot", "drop", "ds", "duplicates", "egen", "else", "encode", "end",
    "erase", "ereturn", "estadd", "estimates", "estout", "eststo", "esttab",
    "exit", "expand", "export", "file", "findit", "foreach", "format",
    "forvalues", "generate", "gettoken", "global", "graph", "gsort", "help",
    "hist", "histogram", "if", "import", "include", "inspect", "isid",
    "joinby", "kdensity", "keep", "label", "levelsof", "line", "list",
    "local", "log", "logistic", "logit", "ls", "macro", "margins", "mark",
    "markout", "marksample", "mata", "mata:", "matrix", "mean", "merge",
    "mkdir", "more", "net", "noisily", "numlist", "order", "poisson",
    "post", "postclose", "postfile", "postutil", "predict", "preserve",
    "probit", "program", "pwd", "quietly", "recast", "recode", "reghdfe",
    "regress", "rename", "replace", "reshape", "restore", "return", "run",
    "save", "saveold", "scalar", "scatter", "search", "set", "shell",
    "sleep", "sort", "ssc", "summarize", "syntax", "sysdir", "tabstat",
    "tabulate", "tempfile", "tempname", "tempvar", "test", "timer",
    "tokenize", "tostring", "total", "tsset", "ttest", "twoway", "type",
    "unab", "use", "version", "which", "while", "xtdescribe", "xtreg",
    "xtset", "xtsum",
}


def _dispatch_commands(repo_root) -> set:
    """Collect the command names OpenStat dispatches on, statically.

    The inventory is read from the source with ``ast``; no module is
    imported and no command is executed.  It lists the commands the
    dispatcher can name, which is a statement about the dispatch table and
    not a claim that every command is fully implemented.
    """
    package = Path(repo_root) / "openstat"
    commands = set()
    for path in sorted(package.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                names = {key.value for key in node.value.keys
                         if isinstance(key, ast.Constant) and isinstance(key.value, str)}
                if names and names <= _COMMAND_LIKE:
                    commands |= names
            if not isinstance(node, ast.Compare) or not isinstance(node.left, ast.Name):
                continue
            if node.left.id != "command":
                continue
            for operator, value in zip(node.ops, node.comparators):
                if isinstance(operator, ast.Eq) and isinstance(value, ast.Constant) \
                        and isinstance(value.value, str):
                    commands.add(value.value)
                elif isinstance(operator, (ast.In, ast.NotIn)) and isinstance(value, (ast.Set, ast.Tuple, ast.List)):
                    commands.update(item.value for item in value.elts
                                    if isinstance(item, ast.Constant) and isinstance(item.value, str))
    return commands


def _alias_table(repo_root) -> set:
    """Return the alias names declared by ``openstat/engine.py``."""
    engine = Path(repo_root) / "openstat" / "engine.py"
    try:
        tree = ast.parse(engine.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ALIASES"
                for target in node.targets):
            try:
                return set(ast.literal_eval(node.value))
            except ValueError:
                return set()
    return set()


def implementation_gap(repo_root, index):
    """Compare the Stata command index with the OpenStat dispatch table.

    Returns a dictionary with the OpenStat command inventory and the Stata
    commands that OpenStat does not dispatch on.
    """
    implemented = _dispatch_commands(repo_root)
    aliases = _alias_table(repo_root)
    known = {name.lower() for name in implemented} | {name.lower() for name in aliases}
    missing = []
    for name in sorted(index):
        key = name.lower()
        if not key or " " in key:
            continue
        if key in known:
            continue
        missing.append(name)
    return {
        "implemented": sorted(implemented),
        "aliases": sorted(aliases),
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# Human readable report
# ---------------------------------------------------------------------------

#: Stata commands that OpenStat does not implement yet, ordered by how much
#: of the estimation workflow they carry.  The report lists the ones that
#: the help corpus documents and that the OpenStat dispatch table does not
#: name.
HIGH_VALUE_COMMANDS = (
    "table", "etable", "dtable", "mixed", "menl", "glm", "gmm", "contrast",
    "marginsplot", "nbreg", "binreg", "arch", "xtgee", "xtlogit", "xtprobit",
    "xtpoisson", "xtnbreg", "xtcloglog", "ivqregress", "heckman", "nlsur",
    "tobit", "intreg", "meprobit", "mprobit", "asroprobit", "asmprobit",
    "asmixlogit", "meqrlogit", "meqrpoisson", "reg3", "sunflower", "pwcompare",
    "grmap", "telasso", "mediate", "cate", "cmroprobit", "cmsummarize",
    "cmxtmixlogit", "stcurve", "stmgintcox", "mds", "mdsmat", "arima", "var",
    "vec", "prais", "newey", "svyset", "simulate", "estat", "lincom", "nlcom",
    "testnl", "predictnl", "suest", "hausman", "cohort", "datasig",
    "xtabond", "xtmixed", "stmixed", "sem", "gsem", "factor", "pca", "mds",
)


def report_markdown(root, index, stats, gap, source_version="StataNow 19.5",
                    top_families=20, wanted=40):
    """Return a human readable summary of the corpus and the implementation gap."""
    families = {}
    for name, entry in index.items():
        key = entry.get("family") or "(no manual section)"
        bucket = families.setdefault(key, {"subjects": 0, "options": 0, "syntax": 0, "commands": []})
        bucket["subjects"] += 1
        bucket["options"] += len(entry["options"])
        bucket["syntax"] += len(entry["syntax"])
        bucket["commands"].append((name, len(entry["options"])))
    lines = []
    add = lines.append
    add("# Stata command interface targets")
    add("")
    add("Generated by `qa/stata_help.py` from the help corpus of " + source_version + ".")
    add("")
    add("* source root: `%s`" % root)
    add("* help files: %d (parsed %d, skipped %d)" % (
        stats["files"], stats["parsed"], stats["skipped"]))
    add("* distinct subjects: %d" % stats["commands"])
    add("* interface facts: %d syntax shapes, %d options, %d stored results" % (
        sum(len(entry["syntax"]) for entry in index.values()),
        sum(len(entry["options"]) for entry in index.values()),
        sum(len(entry["stored_results"]) for entry in index.values())))
    add("")
    add("Only interface facts are recorded: command names, option names with their")
    add("declared argument type, syntax shapes, stored-result names and the one-line")
    add("functional title of a help file. No help prose or examples are copied.")
    add("")
    add("## Corpus layout")
    add("")
    add("The corpus is split into single-letter folders that hold the help files of")
    add("one manual section each.")
    add("")
    add("| folder | files |")
    add("| --- | --- |")
    for folder, count in sorted(stats["folders"].items(), key=lambda item: (-item[1], item[0])):
        add("| `%s` | %d |" % (folder, count))
    add("")
    add("## Manual sections")
    add("")
    add("| section | subjects | options | syntax |")
    add("| --- | --- | --- | --- |")
    for key, bucket in sorted(families.items(), key=lambda item: (-item[1]["options"], item[0])):
        add("| %s | %d | %d | %d |" % (key, bucket["subjects"], bucket["options"], bucket["syntax"]))
    add("")
    add("## The %d largest families by option count" % top_families)
    add("")
    add("| family | subjects | options | busiest commands |")
    add("| --- | --- | --- | --- |")
    ranked = sorted(families.items(), key=lambda item: (-item[1]["options"], item[0]))[:top_families]
    for key, bucket in ranked:
        busiest = sorted(bucket["commands"], key=lambda item: (-item[1], item[0]))[:5]
        add("| %s | %d | %d | %s |" % (
            key, bucket["subjects"], bucket["options"],
            ", ".join("%s (%d)" % (name, count) for name, count in busiest)))
    add("")
    add("## Commands OpenStat does not implement")
    add("")
    add("The OpenStat dispatch table names %d commands and %d aliases; %d Stata" % (
        len(gap["implemented"]), len(gap["aliases"]), len(gap["missing"])))
    add("subjects in the corpus are not named by it. The high value gaps are below.")
    add("")
    implemented = {name.lower() for name in gap["implemented"]}
    implemented |= {name.lower() for name in gap["aliases"]}
    chosen = []
    seen = set()
    for name in HIGH_VALUE_COMMANDS:
        entry = index.get(name)
        if entry is None or name.lower() in implemented or name in seen:
            continue
        seen.add(name)
        chosen.append(name)
        if len(chosen) >= wanted:
            break
    if len(chosen) < wanted:
        rest = [name for name in gap["missing"]
                if name not in seen and " " not in name and ":" not in name
                and not name.startswith("_") and index[name].get("family")]
        rest.sort(key=lambda name: (-len(index[name]["options"]), name))
        for name in rest:
            if len(chosen) >= wanted:
                break
            seen.add(name)
            chosen.append(name)
    add("| command | family | options | syntax |")
    add("| --- | --- | --- | --- |")
    for name in chosen:
        entry = index[name]
        add("| `%s` | %s | %d | %d |" % (name, entry.get("family") or "-",
                                        len(entry["options"]), len(entry["syntax"])))
    add("")
    add("## Notes")
    add("")
    add("* The dispatch inventory is read statically from `openstat/*.py` with")
    add("  `ast`; nothing is imported or executed. Naming a command in the")
    add("  dispatch table is not a claim that the command is fully implemented.")
    add("* Subjects that are help topics rather than commands (postestimation")
    add("  pages, option glossaries, prefix variants such as `bayes: regress`)")
    add("  are part of the index but are not commands OpenStat can implement.")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a machine-readable inventory of Stata's command interface "
                    "from the installed .sthlp help corpus.")
    parser.add_argument("--root", default=r"C:\Program Files\StataNow19\ado\base",
                        help="help corpus directory")
    parser.add_argument("--out", default="qa/stata_targets.json",
                        help="where to write the JSON document")
    parser.add_argument("--version", default="StataNow 19.5",
                        help="version string recorded in the document")
    parser.add_argument("--indent", action="store_true",
                        help="write the JSON indented instead of compact")
    parser.add_argument("--report", default=None,
                        help="also write a human readable markdown report")
    parser.add_argument("--repo", default=".",
                        help="repository root used for the implementation gap")
    args = parser.parse_args(argv)
    notes = [
        LICENSING_NOTE,
        "When several help files document the same subject, the file named "
        "after the subject provides 'help' and its facts; the other files "
        "are listed in 'help_files' and are not mixed in, so that a "
        "superseded syntax never passes for the current one.",
        "Option 'type' is the argument kind named by the help file "
        "(numeric, string, integer, namelist, varlist, ...) and is null when "
        "the file does not state one.",
        "The whole corpus fits in about 1.3 MB, so no per-file detail was "
        "dropped: every subject keeps its help path, title, family, syntax, "
        "options and stored results. The per-file section list that "
        "parse_help returns is not stored in the document.",
        "Subjects are the names the help files document, which includes help "
        "topics (postestimation pages, option glossaries, prefix variants) "
        "and not only executable commands.",
    ]
    document = build_targets(args.root, source_version=args.version, notes=notes)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as stream:
        if args.indent:
            json.dump(document, stream, ensure_ascii=True, indent=1)
        else:
            json.dump(document, stream, ensure_ascii=True, separators=(",", ":"))
        stream.write("\n")
    if args.report:
        index, stats = scan_corpus(args.root)
        gap = implementation_gap(args.repo, index)
        report = Path(args.report)
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(report_markdown(args.root, index, stats, gap,
                                         source_version=args.version))
    counts = document["counts"]
    print("files=%(files)d parsed=%(parsed)d skipped=%(skipped)d commands=%(commands)d" % counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())