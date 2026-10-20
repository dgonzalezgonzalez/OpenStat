"""Stata do-file lexer.

Handles Stata's physical-line rules: ``*`` and ``//`` comments, ``/* */``
block comments, ``///`` continuations, ``#delimit ;`` / ``#delimit cr`` and
compound double quotes. The lexer never executes Python source.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Statement:
    text: str
    line: int


def split_outside(text: str, separator: str = ",") -> list[str]:
    """Split on ``separator`` while respecting quotes, brackets and ``{}``."""
    parts, start, depth, quote, compound = [], 0, 0, False, False
    index = 0
    while index < len(text):
        char = text[index]
        if compound:
            if text.startswith("`\"", index):
                quote = not quote
                compound = False
                index += 2
                continue
            if char == '"' and not quote:
                compound = True
                index += 1
                continue
        elif quote:
            if char == '"':
                quote = False
        else:
            if text.startswith('`"', index):
                compound = True
                index += 2
                continue
            if char == '"':
                quote = True
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif char == separator and depth == 0:
                parts.append(text[start:index].strip())
                start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return parts


def tokens(text: str) -> list[str]:
    """Split on whitespace, keeping quoted and compound-quoted runs together."""
    return re.findall(r'`"[^"]*"\'|"[^"\n]*"|\S+', text)


def unquote(text: str) -> str:
    text = text.strip()
    if text.startswith('`"') and text.endswith('"\''):
        return text[2:-2]
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        return text[1:-1]
    return text


def _strip_comment(line: str, in_block: bool = False) -> tuple[str, bool, bool]:
    """Remove comments from one physical line.

    Returns the code, whether the line ended with a ``///`` continuation and
    whether the line ended inside a ``/* ... */`` block comment.  Stata's
    do-file reader lets block comments span physical lines.
    """
    clean, quote, compound, index = "", False, False, 0
    continued = False
    if in_block:
        end = line.find("*/")
        if end < 0:
            return "", False, True
        line = line[end + 2:]
    while index < len(line):
        char = line[index]
        if compound:
            if char == '"' and not quote:
                compound = False
            else:
                quote = not quote
            clean += char
            index += 1
            continue
        if quote:
            clean += char
            index += 1
            if char == '"':
                quote = False
            continue
        if line.startswith('`"', index):
            compound = True
            clean += '`"'
            index += 2
            continue
        if char == '"':
            quote = True
            clean += char
            index += 1
            continue
        if line.startswith("/*", index):
            end = line.find("*/", index + 2)
            if end < 0:
                return clean, False, True
            index = end + 2
            continue
        if line.startswith("///", index):
            continued = True
            break
        if line.startswith("//", index):
            break
        clean += char
        index += 1
    return clean, continued, False


def _is_comment_start(line: str) -> bool:
    return line.strip().startswith("*")


def lex(script: str) -> list[Statement]:
    """Turn do-file text into an ordered list of executable statements."""
    script = script.lstrip("\ufeff")
    output: list[Statement] = []
    pending, pending_line = "", 1
    delimiter = "\n"
    in_block = False
    for line_number, raw in enumerate(script.splitlines(), 1):
        if pending == "" and not raw.strip() and not in_block:
            continue
        stripped = raw.strip()
        if pending == "" and not in_block and _is_comment_start(raw):
            continue
        clean, continued, in_block = _strip_comment(raw, in_block)
        if pending == "" and not clean.strip():
            continue
        if pending == "" and clean.strip().lower().startswith("#delimit"):
            value = clean.strip().split(maxsplit=1)
            mode = value[1].strip().lower() if len(value) > 1 else "cr"
            delimiter = ";" if mode.startswith(";") else "\n"
            continue
        if pending == "":
            pending_line = line_number
        pending = pending + (" " if pending else "") + clean.strip()
        if continued:
            continue
        if delimiter == ";":
            for piece in split_outside(pending, ";"):
                if piece.strip():
                    output.append(Statement(piece.strip(), pending_line))
            pending = ""
            continue
        output.append(Statement(pending.strip(), pending_line))
        pending = ""
    if pending.strip():
        output.append(Statement(pending.strip(), pending_line))
    return output
