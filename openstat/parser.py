"""Stata do-file lexer.

Handles Stata's physical-line rules: ``*`` and ``//`` comments, nested
``/* */`` block comments, ``///`` continuations, ``#delimit ;`` /
``#delimit cr``, compound double quotes and the block braces that split one
physical line into several statements.  The lexer never executes Python
source.

Three conventions in this module come from measurements on Stata 19.5
(probes and transcripts in ``qa/_scratch``), not from reading StataCorp
sources:

* ``//`` and ``///`` start a comment only at the start of a logical
  statement - at the beginning of a line, after whitespace, or right after a
  ``;`` under ``#delimit ;``.  ``local a abc//def`` therefore keeps
  ``abc//def`` and ``from(https://example.org/path)`` keeps its URL, while
  ``local b xyz //def`` drops the comment.
* A ``/* ... */`` block comment nests, starts even when glued to the previous
  token, and is replaced by **one space**, so ``gen/* c */ x=1`` reads as
  ``gen  x=1`` instead of gluing the command word to its argument.
* Braces are statement boundaries when they stand alone as a token.  Stata
  itself rejects ``if 1 { display "x" }`` with r(198) ``matching close brace
  not found``; OpenStat splits the line so the same program runs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

COMPOUND_OPEN = '`"'
COMPOUND_CLOSE = '"\''
_OPENERS = "([{"
_CLOSERS = ")]}"


@dataclass
class Statement:
    text: str
    line: int


# ----------------------------------------------------------------------
# Quoted runs
# ----------------------------------------------------------------------
def _quoted_span(text: str, start: int) -> tuple[int | None, bool]:
    """Read the quoted run at ``start``.

    Returns ``(end, terminated)``.  ``end`` is ``None`` when ``start`` does
    not open a quote.  An unterminated run reports the end of the text with
    ``terminated`` false, so callers decide whether that is an error
    (:func:`quoted_end`) or merely the end of a physical line (``lex``).
    """
    if text.startswith(COMPOUND_OPEN, start):
        depth, index = 1, start + 2
        while index < len(text):
            if text.startswith(COMPOUND_OPEN, index):
                depth += 1
                index += 2
                continue
            if text.startswith(COMPOUND_CLOSE, index):
                depth -= 1
                index += 2
                if not depth:
                    return index, True
                continue
            index += 1
        return len(text), False
    if start < len(text) and text[start] == '"':
        end = text.find('"', start + 1)
        if end < 0:
            return len(text), False
        return end + 1, True
    return None, True


def quoted_end(text: str, start: int) -> int | None:
    """Return the exclusive end of the quoted run at ``start``, or ``None``.

    Compound double quotes nest and protect ordinary double quotes inside
    them.  An unterminated quote is an error rather than a silently split
    argument, so callers see a malformed program instead of a wrong value.
    """
    end, terminated = _quoted_span(text, start)
    if end is None:
        return None
    if terminated:
        return end
    raise ValueError("unclosed compound string"
                     if text.startswith(COMPOUND_OPEN, start) else "unclosed string")


def tokens(text: str) -> list[str]:
    """Split on whitespace, keeping quoted and compound-quoted runs together."""
    output: list[str] = []
    index, length = 0, len(text)
    while index < length:
        if text[index].isspace():
            index += 1
            continue
        start = index
        while index < length:
            end, _terminated = _quoted_span(text, index)
            if end is not None:
                index = end
                continue
            if text[index].isspace():
                break
            index += 1
        output.append(text[start:index])
    return output


def unquote(text: str) -> str:
    """Strip one layer of quotes, leaving the inner text untouched."""
    text = text.strip()
    if text.startswith(COMPOUND_OPEN):
        try:
            end = quoted_end(text, 0)
        except ValueError:
            end = None
        if end == len(text):
            return text[2:-2]
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def outside_positions(text: str, brackets: str = "(["):
    """Yield the character positions that are outside quotes and brackets."""
    depth, index = 0, 0
    closers = {"(": ")", "[": "]", "{": "}"}
    closing = "".join(closers[bracket] for bracket in brackets)
    while index < len(text):
        end = quoted_end(text, index)
        if end is not None:
            index = end
            continue
        char = text[index]
        if char in brackets:
            depth += 1
        elif char in closing:
            depth -= 1
        elif depth == 0:
            yield index
        index += 1


# ----------------------------------------------------------------------
# Argument splitting
# ----------------------------------------------------------------------
def split_outside(text: str, separator: str = ",") -> list[str]:
    """Split on ``separator`` while respecting quotes, brackets and ``{}``.

    Unterminated quotes are treated as ordinary text: this helper runs on
    fragments, where raising would turn a partial argument into a crash.
    """
    parts: list[str] = []
    start = index = depth = 0
    length = len(text)
    while index < length:
        end, _terminated = _quoted_span(text, index)
        if end is not None:
            index = end
            continue
        char = text[index]
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth = max(depth - 1, 0)
        elif char == separator and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return parts


def parse_options(text: str) -> list[tuple[str, str | bool]]:
    """Read ``name`` / ``name(value)`` options without losing nested content.

    Option values may contain commas, quotes and nested parentheses, so the
    value is read as a balanced run rather than split on punctuation.  Names
    are lower-cased, mirroring Stata's option matching.
    """
    result: list[tuple[str, str | bool]] = []
    index = 0
    while index < len(text):
        while index < len(text) and (text[index].isspace() or text[index] == ","):
            index += 1
        if index == len(text):
            break
        match = re.match(r"[A-Za-z_]\w*", text[index:])
        if not match:
            raise ValueError(f"invalid option {text[index:]}")
        name = match[0].lower()
        index += len(match[0])
        name_end = index
        while index < len(text) and text[index].isspace():
            index += 1
        value: str | bool = True
        if index < len(text) and text[index] == "(":
            start, depth = index + 1, 1
            index += 1
            while index < len(text) and depth:
                end = quoted_end(text, index)
                if end is not None:
                    index = end
                    continue
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                index += 1
            if depth:
                raise ValueError(f"unclosed option {name}()")
            value = text[start:index - 1]
        else:
            index = name_end
        if index < len(text) and not (text[index].isspace() or text[index] == ","):
            raise ValueError(f"invalid option {text[index:]}")
        result.append((name, value))
    return result


# ----------------------------------------------------------------------
# Comments
# ----------------------------------------------------------------------
def _comment_allowed(line: str, index: int) -> bool:
    """Whether a ``//`` at ``index`` starts a comment rather than being text."""
    if index == 0 or line[index - 1].isspace():
        return True
    return line[index - 1] == ";"


def _strip_comment(line: str, block: int = 0) -> tuple[str, int, bool]:
    """Remove comments from one physical line.

    Returns the code, the ``/* */`` nesting depth still open at the end of
    the line, and whether the line ended with a ``///`` continuation.
    """
    clean: list[str] = []
    index, length = 0, len(line)
    continued = False
    while index < length:
        if block:
            if line.startswith("/*", index):
                block += 1
                index += 2
            elif line.startswith("*/", index):
                block -= 1
                index += 2
                if not block:
                    clean.append(" ")
            else:
                index += 1
            continue
        end, _terminated = _quoted_span(line, index)
        if end is not None:
            clean.append(line[index:end])
            index = end
            continue
        if line.startswith("/*", index):
            block = 1
            index += 2
            continue
        if line.startswith("//", index) and _comment_allowed(line, index):
            continued = line.startswith("///", index)
            break
        clean.append(line[index])
        index += 1
    return "".join(clean), block, continued


# ----------------------------------------------------------------------
# Statement boundaries
# ----------------------------------------------------------------------
def _standalone_brace(text: str, index: int, length: int) -> bool:
    before = text[index - 1] if index else " "
    after = text[index + 1] if index + 1 < length else " "
    return before.isspace() and after.isspace()


def _split_braces(text: str, line: int) -> list[Statement]:
    """Split one logical statement at braces that stand alone as tokens.

    ``if 1 { display "{" } else { display 2 }`` becomes seven statements.
    A brace inside a quoted string is part of the string, and ``{$name}`` or
    ``${name}`` never splits because the brace is glued to its neighbours.
    """
    output: list[Statement] = []
    start, index, length = 0, 0, len(text)
    while index < length:
        end, _terminated = _quoted_span(text, index)
        if end is not None:
            index = end
            continue
        char = text[index]
        if char in "{}" and _standalone_brace(text, index, length):
            if char == "{":
                piece = text[start:index + 1].strip()
                if piece:
                    output.append(Statement(piece, line))
            else:
                piece = text[start:index].strip()
                if piece:
                    output.append(Statement(piece, line))
                output.append(Statement("}", line))
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail:
        output.append(Statement(tail, line))
    return output


def _mata_state(text: str, in_mata: bool) -> bool:
    """Track bare ``mata`` ... ``end`` blocks, whose braces are source code."""
    value = text.strip().lower()
    if not in_mata and value in {"mata", "mata:"}:
        return True
    if in_mata and value == "end":
        return False
    return in_mata


def _delimiter_mode(rest: str) -> str:
    """Read the mode of a ``#delimit`` directive."""
    mode = rest.strip().lower()
    if mode.startswith(";"):
        return ";"
    while mode.endswith(";"):
        mode = mode[:-1].strip()
    if mode in {"cr", ""}:
        return "\n"
    raise ValueError(f"invalid #delimit delimiter {mode!r}; use ';' or 'cr'")


def _split_delimiter(text: str, delimiter: str) -> tuple[list[str], str]:
    """Split on ``delimiter`` outside quotes and ``()`` / ``[]``.

    Returns the completed pieces plus the unterminated remainder, so a
    bracketed expression continued on the next physical line is joined with
    a single space instead of becoming its own statement.  Braces are not
    counted: under ``#delimit ;`` they are ordinary statement text.
    """
    pieces: list[str] = []
    start = index = depth = 0
    length = len(text)
    while index < length:
        end, _terminated = _quoted_span(text, index)
        if end is not None:
            index = end
            continue
        char = text[index]
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(depth - 1, 0)
        elif char == delimiter and depth == 0:
            piece = text[start:index].strip()
            if piece:
                pieces.append(piece)
            start = index + 1
        index += 1
    return pieces, text[start:].strip()


def _emit(text: str, line: int, in_mata: bool) -> list[Statement]:
    """Turn one logical statement into statements, splitting braces."""
    text = text.strip()
    if not text:
        return []
    if in_mata:
        return [Statement(text, line)]
    return _split_braces(text, line)


# ----------------------------------------------------------------------
# The lexer
# ----------------------------------------------------------------------
def lex(script: str) -> list[Statement]:
    """Turn do-file text into an ordered list of executable statements."""
    script = script.lstrip("\ufeff")
    output: list[Statement] = []
    pending, pending_line = "", 1
    delimiter = "\n"
    block = 0
    in_mata = False
    for number, raw in enumerate(script.splitlines(), 1):
        if not pending and not block:
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.lower().startswith("#delimit"):
                delimiter = _delimiter_mode(stripped[len("#delimit"):])
                continue
            if stripped.startswith("*") and not stripped.startswith("/*"):
                continue
        clean, block, continued = _strip_comment(raw, block)
        if not pending and not clean.strip():
            continue
        if not pending:
            pending_line = number
        pending = f"{pending} {clean.strip()}".strip() if pending else clean.strip()
        if continued:
            continue
        if delimiter == ";":
            pieces, remainder = _split_delimiter(pending, ";")
            for piece in pieces:
                output.extend(_emit(piece, pending_line, in_mata))
                in_mata = _mata_state(piece, in_mata)
            pending = remainder
            if remainder:
                pending_line = number
            continue
        output.extend(_emit(pending, pending_line, in_mata))
        in_mata = _mata_state(pending, in_mata)
        pending = ""
    if pending.strip():
        output.extend(_emit(pending, pending_line, in_mata))
    return output
