"""Small, quote-aware do-file lexer. It never executes Python source."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Statement:
    text: str
    line: int


def split_outside(text: str, separator: str = ",") -> list[str]:
    parts, start, depth, quote = [], 0, 0, False
    for i, char in enumerate(text):
        if char == '"':
            quote = not quote
        elif not quote:
            if char in "([":
                depth += 1
            elif char in ")]":
                depth -= 1
            elif char == separator and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
    parts.append(text[start:].strip())
    return parts


def tokens(text: str) -> list[str]:
    return re.findall(r'"[^"\n]*"|\S+', text)


def unquote(text: str) -> str:
    text = text.strip()
    if text.startswith('`"') and text.endswith('"\''):
        return text[2:-2]
    return text[1:-1] if text.startswith('"') and text.endswith('"') else text


def lex(script: str) -> list[Statement]:
    """Preserve physical source lines through comments and /// continuations."""
    script = script.lstrip("\ufeff")
    output, pending, pending_line = [], "", 1
    in_comment, delimiter = False, "\n"
    for line_number, raw in enumerate(script.splitlines(), 1):
        clean, quote, i, continued = "", False, 0, False
        while i < len(raw):
            if in_comment:
                if raw[i:i+2] == "*/":
                    in_comment, i = False, i + 2
                else:
                    i += 1
            elif not quote and raw[i:i+2] == "/*":
                in_comment, i = True, i + 2
            elif not quote and raw[i:i+3] == "///":
                continued = True
                break
            elif not quote and raw[i:i+2] == "//":
                break
            else:
                if raw[i] == '"':
                    quote = not quote
                clean += raw[i]
                i += 1
        if not clean.strip() or clean.lstrip().startswith("*"):
            continue
        if clean.strip().lower().startswith("#delimit"):
            value = clean.split(maxsplit=1)[1].strip()
            delimiter = ";" if value == ";" else "\n"
            continue
        if not pending:
            pending_line = line_number
        pending += " " + clean.strip()
        if continued:
            continue
        if delimiter == ";":
            pieces = split_outside(pending, ";")
            for piece in pieces[:-1]:
                if piece:
                    output.append(Statement(piece, pending_line))
            pending = pieces[-1]
            continue
        output.append(Statement(pending.strip(), pending_line))
        pending = ""
    if in_comment:
        raise ValueError("unclosed block comment")
    if pending.strip():
        output.append(Statement(pending.strip(), pending_line))
    return output
