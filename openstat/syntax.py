"""Stata ``syntax`` command implementation.

Parses a command's argument list against a syntax specification and stores
the results in local macros, exactly as Stata's ``syntax`` does.  This is
the piece that makes third-party ado files runnable.

The specification grammar implemented here is the subset documented in
Stata's ``syntax`` help entry:

    syntax cmd [varlist | namelist | anything] [if] [in] [using file]
              [=exp] [weight] [, options]

Options are described as ``NAME`` (a switch), ``NAME(type)`` where the type
is ``integer``, ``real``, ``string``, ``namelist``, ``name`` or ``numlist``,
plus the ``asis`` modifier for string options.
"""
from __future__ import annotations

import re


class SyntaxError_(ValueError):
    pass


# ----------------------------------------------------------------------
# Specification parsing
# ----------------------------------------------------------------------
def _split_top(text: str, separator: str = ",") -> list[str]:
    """Split on ``separator`` at bracket depth zero."""
    parts, current, depth = [], "", 0
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        if char == separator and depth == 0:
            parts.append(current)
            current = ""
            continue
        current += char
    parts.append(current)
    return parts


def _split_option_list(text: str) -> list[str]:
    """Split a space-separated option list into its descriptors.

    ``MISSing Abbrev(int -1) Joint`` yields three descriptors; parentheses
    keep their contents together.
    """
    parts, current, depth = [], "", 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char.isspace() and depth == 0:
            if current.strip():
                parts.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        parts.append(current.strip())
    return parts


def _parse_option_spec(text: str) -> dict:
    """Parse one option descriptor such as ``DOF(integer 50)``."""
    match = re.fullmatch(r"([A-Za-z_]\w*)\s*(?:\((.*)\))?", text.strip(), re.S)
    if not match:
        raise SyntaxError_(f"invalid option {text.strip()!r}")
    name = match[1].lower()
    argument = (match[2] or "").strip()
    option = {"name": name, "kind": "switch", "default": "", "asis": False}
    if not argument:
        return option
    parts = argument.split()
    kind = parts[0].lower() if parts else ""
    if kind in {"integer", "int"}:
        option["kind"] = "integer"
    elif kind in {"real", "num"}:
        option["kind"] = "real"
    elif kind in {"string", "str"}:
        option["kind"] = "string"
    elif kind in {"namelist", "name"}:
        option["kind"] = "namelist"
    elif kind == "numlist":
        option["kind"] = "numlist"
    elif kind in {"anything", "passthru", "value"}:
        option["kind"] = kind
    else:
        option["kind"] = "value"
    # Anything after the type is either a default value or a modifier.
    rest = parts[1:]
    for position, extra in enumerate(rest):
        lowered = extra.lower()
        if lowered == "asis":
            option["asis"] = True
        elif lowered.startswith("default(") and lowered.endswith(")"):
            option["default"] = extra[len("default("):-1]
        elif lowered.startswith("min="):
            option["min"] = int(lowered[4:])
        elif lowered.startswith("max="):
            option["max"] = int(lowered[4:])
        elif position == 0:
            # ``NAME(integer 50)`` declares 50 as the default value.
            option["default"] = extra
    return option


def _parse_elements(spec: str) -> list[dict]:
    """Split a syntax specification into its ordered elements."""
    elements: list[dict] = []
    index, length = 0, len(spec)
    while index < length:
        char = spec[index]
        if char.isspace():
            index += 1
            continue
        if char in "[(":
            closing = "]" if char == "[" else ")"
            depth, start = 1, index + 1
            index += 1
            while index < length and depth:
                if spec[index] in "[(":
                    depth += 1
                elif spec[index] in ")]":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            if depth:
                raise SyntaxError_("unbalanced brackets in syntax specification")
            body = spec[start:index]
            name = body.split("(")[0].strip().lower() if "(" in body else body.strip().lower()
            elements.append({"optional": char == "[", "name": name, "body": body})
            index += 1
            continue
        match = re.match(r"[A-Za-z_]\w*", spec[index:])
        if match:
            elements.append({"optional": False, "name": match[0].lower(), "body": match[0]})
            index += match.end()
            continue
        index += 1
    return elements


def parse_syntax(spec: str) -> tuple[list[dict], list[dict]]:
    """Return the ordered elements and the option list of a specification.

    The option list is the bracketed group that follows the specification's
    top-level comma; everything before it describes the command's arguments.
    """
    spec = spec.strip()
    option_text = ""
    body = spec
    depth, option_start, option_end = 0, None, None
    for index, char in enumerate(spec):
        if char == "[":
            depth += 1
            if depth == 1:
                # The option list is the bracket group whose content begins
                # with a comma, as in ``[, DETail]``.
                following = spec[index + 1:].lstrip()
                if following.startswith(","):
                    option_start = index
        elif char == "]":
            depth -= 1
            if depth == 0 and option_start is not None and option_end is None:
                option_end = index
    if option_start is not None and option_end is not None:
        option_text = spec[option_start + 1:option_end].lstrip().lstrip(",")
        body = spec[:option_start].rstrip().rstrip(",").rstrip()
    options = [_parse_option_spec(part) for part in _split_option_list(option_text)]
    return _parse_elements(body), options


# ----------------------------------------------------------------------
# Argument list parsing
# ----------------------------------------------------------------------
def _split_arguments(text: str) -> tuple[str, str]:
    """Split an argument list into its command part and its option part."""
    depth, quote, compound, index = 0, False, False, 0
    options_at = None
    while index < len(text):
        char = text[index]
        if compound:
            if char == '"' and not quote:
                compound = False
            else:
                quote = not quote
            index += 1
            continue
        if quote:
            if char == '"':
                quote = False
            index += 1
            continue
        if text.startswith('`"', index):
            compound = True
            index += 2
            continue
        if char == '"':
            quote = True
            index += 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            options_at = index
            break
        index += 1
    if options_at is None:
        return text.strip(), ""
    return text[:options_at].strip(), text[options_at + 1:].strip()


def _split_options(text: str) -> list[tuple[str, str | None, str]]:
    """Split an option string into (name, value, raw) triples."""
    result: list[tuple[str, str | None, str]] = []
    index, length = 0, len(text)
    while index < length:
        while index < length and (text[index].isspace() or text[index] == ","):
            index += 1
        if index >= length:
            break
        match = re.match(r"[A-Za-z_]\w*", text[index:])
        if not match:
            index += 1
            continue
        name = match[0]
        start = index
        index += match.end()
        while index < length and text[index].isspace():
            index += 1
        if index < length and text[index] == "(":
            depth, value_start = 1, index + 1
            index += 1
            while index < length and depth:
                if text[index] == "(":
                    depth += 1
                elif text[index] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                index += 1
            value = text[value_start:index]
            index += 1
            result.append((name, value, text[start:index]))
        else:
            result.append((name, None, text[start:index]))
    return result


def _strip_if_in(text: str) -> tuple[str, str, str]:
    """Remove the ``if``/``in`` qualifiers from a command's argument list."""
    depth, quote, compound, index = 0, False, False, 0
    starts = []
    while index < len(text):
        char = text[index]
        if compound:
            if char == '"' and not quote:
                compound = False
            else:
                quote = not quote
            index += 1
            continue
        if quote:
            if char == '"':
                quote = False
            index += 1
            continue
        if text.startswith('`"', index):
            compound = True
            index += 2
            continue
        if char == '"':
            quote = True
            index += 1
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif depth == 0:
            for word in ("if", "in"):
                if text.startswith(word, index) and (index == 0 or text[index - 1].isspace()):
                    after = index + len(word)
                    if after < len(text) and (text[after].isspace() or text[after] == "("):
                        starts.append((index, word))
        index += 1
    if not starts:
        return text.strip(), "", ""
    position, word = starts[0]
    if_in = text[position:].strip()
    if word == "if":
        condition = re.match(r"if\s+(.*)", if_in, re.S)
        return text[:position].strip(), condition[1].strip() if condition else "", ""
    match = re.match(r"in\s+(\S+)", if_in)
    return text[:position].strip(), "", match[1] if match else ""


# ----------------------------------------------------------------------
# The command
# ----------------------------------------------------------------------
class SyntaxCommand:
    """Implements ``syntax`` for a program."""

    def __init__(self, engine, spec: str):
        self.engine = engine
        self.spec = spec
        self.elements, self.options = parse_syntax(spec)

    # -- helpers -------------------------------------------------------
    def _element_names(self) -> list[str]:
        return [element["name"] for element in self.elements]

    def _option_matches(self, name: str) -> dict | None:
        """Match a user-supplied option name against the specification.

        Stata allows unambiguous abbreviations of option names, so
        ``MISSing`` matches the declared ``MISSING``.
        """
        lowered = name.lower()
        for option in self.options:
            declared = option["name"]
            if declared == lowered:
                return option
        candidates = [option for option in self.options
                      if option["name"].startswith(lowered)]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise SyntaxError_(f"option {name} is ambiguous")
        return None

    # -- main ----------------------------------------------------------
    def run(self, argument_text: str) -> dict:
        engine = self.engine
        head, option_text = _split_arguments(argument_text)
        head, if_condition, in_range = _strip_if_in(head)
        supplied: dict[str, str | None] = {}
        for name, value, _raw in _split_options(option_text):
            option = self._option_matches(name)
            if option is None:
                raise SyntaxError_(f"option {name} not allowed")
            if option["name"] in supplied:
                raise SyntaxError_(f"option {option['name']} specified more than once")
            supplied[option["name"]] = value

        results: dict[str, str] = {}
        names = self._element_names()
        if "if" in names:
            results["if"] = f"if {if_condition}" if if_condition else ""
        if "in" in names:
            results["in"] = f"in {in_range}" if in_range else ""

        remaining = head
        for element in self.elements:
            name = element["name"]
            if name in {"if", "in"}:
                continue
            body = element["body"]
            if name in {"varlist", "newvarlist", "namelist"}:
                if not element["optional"] and not remaining:
                    raise SyntaxError_(f"{name} required")
                results[name] = remaining
                remaining = ""
            elif name == "anything":
                if not element["optional"] and not remaining:
                    raise SyntaxError_("anything required")
                results[name] = remaining
                remaining = ""
            elif name == "using":
                results["using"] = remaining
                remaining = ""
            elif name == "weight":
                results["weight"] = remaining
                remaining = ""
            elif name == "=":
                results["exp"] = remaining.lstrip("= ").strip()
                remaining = ""
            elif not element["optional"]:
                match = re.match(r"(\S+)", remaining)
                if not match:
                    raise SyntaxError_(f"{name} required")
                results[name] = match[1]
                remaining = remaining[match.end():].strip()
            else:
                results.setdefault(name, "")

        # Options become macros named after the option.
        for option in self.options:
            name = option["name"]
            if name in supplied:
                value = supplied[name]
                if option["kind"] == "switch":
                    if value not in (None, ""):
                        raise SyntaxError_(f"option {name} does not take a value")
                    results[name] = "1"
                elif option["kind"] == "string":
                    results[name] = value if value is not None else ""
                    if option["asis"] and value is not None:
                        results[name] = value
                elif option["kind"] == "integer":
                    text = (value or "").strip()
                    if not text:
                        raise SyntaxError_(f"option {name} requires an integer")
                    try:
                        int(text)
                    except ValueError as error:
                        raise SyntaxError_(f"option {name} requires an integer") from error
                    results[name] = text
                elif option["kind"] == "real":
                    text = (value or "").strip()
                    if not text:
                        raise SyntaxError_(f"option {name} requires a number")
                    try:
                        float(text)
                    except ValueError as error:
                        raise SyntaxError_(f"option {name} requires a number") from error
                    results[name] = text
                elif option["kind"] == "numlist":
                    text = (value or "").strip()
                    if text and not re.fullmatch(
                            r"-?\d+(\s*(/\s*-?\d+|\s*\(\s*-?\d+\s*\)\s*-?\d+))?"
                            r"(\s*,\s*-?\d+(\s*(\(\s*-?\d+\s*\)\s*-?\d+)?)?", text):
                        raise SyntaxError_(f"option {name} requires a numlist")
                    results[name] = text
                elif option["kind"] == "namelist":
                    text = (value or "").strip()
                    if text and not re.fullmatch(r"[A-Za-z_]\w*(\s+[A-Za-z_]\w*)*", text):
                        raise SyntaxError_(f"option {name} requires a namelist")
                    results[name] = text
                else:
                    results[name] = value if value is not None else ""
            else:
                results[name] = option["default"]
        return results
