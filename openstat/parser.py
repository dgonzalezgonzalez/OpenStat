"""Quote-aware do-file lexer. Parsing never executes source code."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Statement:
    text: str
    line: int


def quoted_end(text: str, start: int) -> int | None:
    """Return the exclusive end of a simple/compound quoted string, or None.

    Compound quotes can nest and protect ordinary double quotes inside them.
    A missing closing quote is an error rather than a silently split argument.
    """
    if text.startswith('`"', start):
        depth, i = 1, start + 2
        while i < len(text):
            if text.startswith('`"', i):
                depth += 1
                i += 2
            elif text.startswith('"\'', i):
                depth -= 1
                i += 2
                if not depth:
                    return i
            else:
                i += 1
        raise ValueError("unclosed compound quote")
    if start < len(text) and text[start] == '"':
        end = text.find('"', start + 1)
        if end < 0:
            raise ValueError("unclosed string")
        return end + 1
    return None


def outside_positions(text: str, brackets: str = "(["):
    """Yield character positions outside quotes and balanced nested brackets."""
    depth, i = 0, 0
    closers = {"(": ")", "[": "]", "{": "}"}
    closing = "".join(closers[b] for b in brackets)
    while i < len(text):
        end = quoted_end(text, i)
        if end is not None:
            i = end
            continue
        char = text[i]
        if char in brackets:
            depth += 1
        elif char in closing:
            depth -= 1
        elif depth == 0:
            yield i
        i += 1


def split_outside(text: str, separator: str = ",") -> list[str]:
    parts, start = [], 0
    for i in outside_positions(text):
        if text[i] == separator:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return parts


def tokens(text: str) -> list[str]:
    """Whitespace-separated words, keeping simple and compound quotes intact."""
    result, i = [], 0
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        start = i
        while i < len(text) and not text[i].isspace():
            end = quoted_end(text, i)
            i = end if end is not None else i + 1
        result.append(text[start:i])
    return result


def unquote(text: str) -> str:
    text = text.strip()
    if text.startswith(('`"', '"')) and quoted_end(text, 0) == len(text):
        return text[2:-2] if text.startswith('`"') else text[1:-1]
    return text


def parse_options(text: str) -> list[tuple[str, str | bool]]:
    """Read options without losing quoted or nested parenthesized contents."""
    result, i = [], 0
    while i < len(text):
        while i < len(text) and (text[i].isspace() or text[i] == ","):
            i += 1
        if i == len(text):
            break
        match = re.match(r"[A-Za-z_]\w*", text[i:])
        if not match:
            raise ValueError(f"invalid option {text[i:]}")
        name = match[0].lower()
        i += len(match[0])
        name_end = i
        while i < len(text) and text[i].isspace():
            i += 1
        value = True
        if i < len(text) and text[i] == "(":
            start, depth = i+1, 1
            i += 1
            while i < len(text) and depth:
                end = quoted_end(text, i)
                if end is not None:
                    i = end
                    continue
                if text[i] == "(":
                    depth += 1
                elif text[i] == ")":
                    depth -= 1
                i += 1
            if depth:
                raise ValueError(f"unclosed option {name}()")
            value = text[start:i-1]
        else:
            i = name_end
        if i < len(text) and not (text[i].isspace() or text[i] == ","):
            raise ValueError(f"invalid option {text[i:]}")
        result.append((name, value))
    return result


def read_source(path) -> str:
    """Read Unicode scripts and legacy Windows scripts without lossy decoding."""
    content = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeError:
            continue
    raise UnicodeError("script could not be decoded")


def _structural_statements(text: str, line: int) -> list[Statement]:
    """Normalize control braces, preserving braces in strings and ${macros}."""
    result, start, i = [], 0, 0
    while i < len(text):
        end = quoted_end(text, i)
        if end is not None:
            i = end
            continue
        if text[i] == "{" and i and text[i-1] == "$":
            end = text.find("}", i+1)
            if end >= 0:
                i = end+1
                continue
        fragment = text[start:i].strip()
        control = r"(?:(?:quietly|qui|capture|cap|noisily|noi)\s*:?[ \t]+)*(?:if|else|forvalues|forvalue|forval|forva|forv|foreach|foreac|forea|fore|while)\b"
        prefix = r"(?:(?:quietly|qui|capture|cap|noisily|noi)\s*:?[ \t]*)+"
        if text[i] == "{" and (re.match("^"+control, fragment) or re.fullmatch(prefix, fragment)):
            result.append(Statement(fragment + " {", line))
            start = i+1
        elif text[i] == "}" and (not fragment or result):
            if fragment:
                result.append(Statement(fragment, line))
            result.append(Statement("}", line))
            start = i+1
        i += 1
    if text[start:].strip():
        result.append(Statement(text[start:].strip(), line))
    return result


def lex(script: str) -> list[Statement]:
    """Preserve physical source lines through comments and continuations.

    Semicolon mode spans physical lines; semicolons inside quotes and brackets
    are protected. Delimiter directives are recognized only as complete lines.
    """
    script = script.lstrip("\ufeff")
    output, pending, pending_line = [], "", 1
    in_comment, delimiter = 0, "\n"
    for line_number, raw in enumerate(script.splitlines(), 1):
        clean, i, continued = [], 0, False
        while i < len(raw):
            if in_comment:
                if raw.startswith("/*", i):
                    in_comment += 1
                    i += 2
                elif raw.startswith("*/", i):
                    in_comment -= 1
                    i += 2
                    if not in_comment:
                        clean.append(" ")
                else:
                    i += 1
            elif raw.startswith("/*", i):
                in_comment, i = 1, i+2
            elif raw.startswith("///", i) and (i == 0 or raw[i-1].isspace()):
                continued = True
                break
            elif raw.startswith("//", i) and (i == 0 or raw[i-1].isspace()):
                break
            else:
                end = quoted_end(raw, i)
                if end is not None:
                    clean.append(raw[i:end])
                    i = end
                else:
                    clean.append(raw[i])
                    i += 1
        clean = "".join(clean).strip()
        if not clean or clean.startswith("*"):
            continue
        directive = re.fullmatch(r"#delimit\s+(;|cr)\s*;?", clean, flags=re.I)
        if directive:
            if pending.strip():
                raise ValueError("delimiter change before command terminator")
            delimiter = ";" if directive[1] == ";" else "\n"
            continue
        if clean.lower().startswith("#delimit"):
            raise ValueError("invalid delimiter")
        if not pending:
            pending_line = line_number
        pending += (" " if pending else "") + clean
        if continued:
            continue
        if delimiter == ";":
            pieces = split_outside(pending, ";")
            for piece in pieces[:-1]:
                if piece:
                    output.extend(_structural_statements(piece, pending_line))
            pending = pieces[-1]
            continue
        output.extend(_structural_statements(pending, pending_line))
        pending = ""
    if in_comment:
        raise ValueError("unclosed block comment")
    if pending.strip():
        output.extend(_structural_statements(pending, pending_line))
    return output
