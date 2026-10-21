"""Stata macro expansion and the extended macro functions.

Macros are stored as text. Expansion happens in two passes so that a macro
whose value contains another macro reference resolves the way Stata resolves
it, including compound double quotes and ```` ```` -style compound names.
"""
from __future__ import annotations

import re


class MacroError(ValueError):
    pass


# Extended macro function names understood by the ``local``/``global``
# commands.  Each entry maps a function name to the set of forms it accepts.
EXTENDED_FUNCTIONS = {
    "word": "count|of|nth",
    "list": "clean|uniq|retokenize|sizeof|sort|duplicates",
    "substr": "",
    "strlen": "",
    "strpos": "",
    "regexm": "",
    "regexr": "",
    "regexs": "",
    "subinstr": "",
    "upper": "",
    "lower": "",
    "proper": "",
    "trim": "",
    "ltrim": "",
    "rtrim": "",
    "real": "",
    "string": "",
    "compound": "",
    "macro": "name|list|listall|get|getenv",
    "dir": "",
    "pwd": "",
    "fnam": "",
    "fnames": "",
    "abbrev": "",
    "abbrevate": "",
    "hashone": "",
    "rc": "",
    "e": "",
    "r": "",
    "c": "",
    "n": "",
    "S": "",
    "itoken": "",
    "tokenget": "",
    "token": "",
    "tsunabbrev": "",
    "unknown": "",
    "type": "",
    "sizeof": "",
    "q": "",
}


def expand_macros(text: str, locals: dict, globals: dict, depth: int = 0,
                  returns: dict | None = None, eclass: dict | None = None,
                  cclass: dict | None = None) -> str:
    """Expand `` `name' `` and `` $name `` references.

    Compound double quotes (`` `"..."' ``) protect their content from
    expansion, matching Stata's behaviour.

    ``returns``, ``eclass`` and ``cclass`` supply the values behind
    `` `r(name)' ``, `` `e(name)' `` and `` `c(name)' ``.
    """
    if depth > 30:
        raise MacroError("macro expansion recursion limit")
    out, index, length = "", 0, len(text)
    while index < length:
        char = text[index]
        if char == '"':
            # A plain double quote does not protect a macro reference:
            # Stata expands "$name" inside a string.
            out += char
            index += 1
            continue
        if text.startswith('`"', index):
            end = text.find('"\'', index + 2)
            if end < 0:
                out += text[index:]
                break
            out += text[index:end + 2]
            index = end + 2
            continue
        if char == "`":
            match = re.match(r"`([A-Za-z_]\w*)(?:\(([A-Za-z_]\w*)\))?'", text[index:])
            if match:
                name = match[1]
                if name in {"r", "e", "c"} and match[2] is not None:
                    resolved = _resolve_class(name, match[2], returns, eclass, cclass)
                    out += expand_macros(resolved, locals, globals, depth + 1)
                    index += match.end()
                    continue
                # A backtick names a local macro; a dollar sign names a
                # global one.  Stata does not fall back from one to the
                # other.
                value = locals.get(name)
                if value is None:
                    out += match[0]
                else:
                    out += expand_macros(str(value), locals, globals, depth + 1)
                index += match.end()
                continue
        if char == "$":
            match = re.match(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))", text[index:])
            if match:
                name = match[1] or match[2]
                value = globals.get(name)
                if value is None:
                    out += match[0]
                else:
                    out += expand_macros(str(value), locals, globals, depth + 1)
                index += match.end()
                continue
        out += char
        index += 1
    return out


#: The format Stata uses when it turns a number into macro text.  Measured
#: on Stata 19.5: `` `r(mean)' ``, `` `e(b)' `` and `` `c(pi)' `` all expand
#: to the *unpadded* rendering of ``%18.0g`` -- 18 significant characters
#: minus the sign, the decimal point and the exponent.
MACRO_FORMAT = "%18.0g"


def _resolve_class(name: str, key: str, returns: dict | None, eclass: dict | None,
                   cclass: dict | None) -> str | None:
    """Return the value of `` `r(name)' ``, `` `e(name)' `` or `` `c(name)' ``.

    Stata expands these to *text*, not to a number: a numeric value is
    rendered with ``%18.0g`` and the padding is removed, so `` `c(pi)'``
    expands to ``3.141592653589793`` and `` `r(mean)' `` to
    ``.58553338``.  The text is then used wherever the macro appeared -- in
    a string literal it is shown verbatim, in an expression it is re-parsed
    and re-rendered with the default numeric format.

    A name that is not set expands to the empty string: Stata does not
    complain about an unknown ``r()``, ``e()`` or ``c()`` name in macro
    position.
    """
    table = {"r": returns, "e": eclass, "c": cclass}[name]
    if not table or key not in table:
        return ""
    value = table[key]
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        from .formats import format_value

        return format_value(float(value), MACRO_FORMAT).strip()
    return str(value)


def _split_words(text: str) -> list[str]:
    """Split like Stata's ``word`` function: whitespace separated runs."""
    return [word for word in re.split(r"\s+", text.strip()) if word]


def _subinstr(text: str, find: str, replace: str, count: int = -1) -> str:
    if not find:
        return text
    if count < 0:
        return text.replace(find, replace)
    parts = text.split(find, count)
    return replace.join(parts)


def _proper(text: str) -> str:
    return " ".join(word[:1].upper() + word[1:].lower() for word in _split_words(text))


def _regexr(text: str, pattern: str, replacement: str) -> str:
    def expand(match: re.Match) -> str:
        return re.sub(r"\$(\d)", lambda m: match.group(int(m[1]) or 0), replacement)
    return re.sub(pattern, expand, text)


def apply_macro_function(name: str, arguments: list[str]) -> str:
    """Evaluate one extended macro function and return its text value."""
    name = name.lower()
    if name == "word":
        if not arguments:
            raise MacroError("word() requires arguments")
        form = arguments[0].lower()
        if form == "count":
            if len(arguments) != 2:
                raise MacroError("word count() requires one argument")
            return str(len(_split_words(arguments[1])))
        if form == "of":
            if len(arguments) != 3:
                raise MacroError("word of() requires two arguments")
            index = int(arguments[1])
            words = _split_words(arguments[2])
            return words[index - 1] if 1 <= index <= len(words) else ""
        if form == "nth":
            if len(arguments) != 3:
                raise MacroError("word nth() requires two arguments")
            index = int(arguments[1])
            words = _split_words(arguments[2])
            return words[index - 1] if 1 <= index <= len(words) else ""
        raise MacroError(f"unsupported word() form {form}")
    if name == "list":
        if not arguments:
            raise MacroError("list() requires arguments")
        form = arguments[0].lower()
        rest = arguments[1:]
        if form == "clean":
            return " ".join(_split_words(" ".join(rest)))
        if form == "uniq":
            seen, result = set(), []
            for word in _split_words(" ".join(rest)):
                if word not in seen:
                    seen.add(word)
                    result.append(word)
            return " ".join(result)
        if form == "retokenize":
            if len(rest) != 2:
                raise MacroError("list retokenize() requires two arguments")
            separator, source = rest[0], rest[1]
            return separator.join(_split_words(source))
        if form == "sizeof":
            return str(len(_split_words(" ".join(rest))))
        if form == "sort":
            return " ".join(sorted(_split_words(" ".join(rest))))
        if form == "duplicates":
            words = _split_words(" ".join(rest))
            counts: dict[str, int] = {}
            for word in words:
                counts[word] = counts.get(word, 0) + 1
            return " ".join(word for word in words if counts[word] > 1)
        raise MacroError(f"unsupported list() form {form}")
    if name == "substr":
        if len(arguments) != 3:
            raise MacroError("substr() requires three arguments")
        try:
            start, length = int(arguments[1]), int(arguments[2])
        except ValueError as error:
            raise MacroError("substr() requires numeric arguments") from error
        if start < 1 or length < 0:
            raise MacroError("substr() arguments out of range")
        return arguments[0][start - 1:start - 1 + length]
    if name == "strlen":
        if len(arguments) != 1:
            raise MacroError("strlen() requires one argument")
        return str(len(arguments[0]))
    if name == "strpos":
        if len(arguments) != 2:
            raise MacroError("strpos() requires two arguments")
        position = arguments[0].find(arguments[1])
        return str(position + 1)
    if name == "regexm":
        if len(arguments) != 2:
            raise MacroError("regexm() requires two arguments")
        try:
            return "1" if re.search(arguments[1], arguments[0]) else "0"
        except re.error as error:
            raise MacroError(f"invalid regular expression: {error}") from error
    if name == "regexr":
        if len(arguments) != 2:
            raise MacroError("regexr() requires two arguments")
        try:
            return _regexr(arguments[0], arguments[1], r"\g<0>")
        except re.error as error:
            raise MacroError(f"invalid regular expression: {error}") from error
    if name == "regexs":
        if len(arguments) != 2:
            raise MacroError("regexs() requires two arguments")
        try:
            match = re.search(arguments[1], arguments[0])
        except re.error as error:
            raise MacroError(f"invalid regular expression: {error}") from error
        if not match:
            return ""
        return "\n".join(str(group) for group in match.groups())
    if name == "subinstr":
        if len(arguments) < 3:
            raise MacroError("subinstr() requires at least three arguments")
        count = int(arguments[3]) if len(arguments) > 3 else -1
        return _subinstr(arguments[0], arguments[1], arguments[2], count)
    if name == "upper":
        return arguments[0].upper() if arguments else ""
    if name == "lower":
        return arguments[0].lower() if arguments else ""
    if name == "proper":
        return _proper(arguments[0]) if arguments else ""
    if name == "trim":
        return arguments[0].strip() if arguments else ""
    if name == "ltrim":
        return arguments[0].lstrip() if arguments else ""
    if name == "rtrim":
        return arguments[0].rstrip() if arguments else ""
    if name == "real":
        try:
            return format(float(arguments[0].strip()), ".15g")
        except (ValueError, IndexError):
            return "."
    if name == "string":
        if len(arguments) < 2:
            raise MacroError("string() requires two arguments")
        from .formats import format_value
        return format_value(float(arguments[0]), arguments[1])
    if name == "compound":
        return '`"' + (arguments[0] if arguments else "") + '"' if arguments else '""'
    if name == "abbrev" or name == "abbrevate":
        if len(arguments) != 3:
            raise MacroError("abbrev() requires three arguments")
        return _abbrev(arguments[0], int(arguments[1]), int(arguments[2]))
    if name == "hashone":
        return arguments[0] if arguments else ""
    if name == "rc":
        return arguments[0] if arguments else ""
    if name in {"e", "r", "c", "n", "S"}:
        return arguments[0] if arguments else ""
    if name == "macro":
        return arguments[0] if arguments else ""
    if name == "type":
        return arguments[0] if arguments else ""
    if name == "q":
        return arguments[0] if arguments else ""
    if name == "sizeof":
        return str(len(arguments[0])) if arguments else "0"
    raise MacroError(f"extended macro function {name}() is not implemented")


def _abbrev(name: str, min_length: int, max_length: int) -> str:
    """Shorten ``name`` to at least ``min_length`` characters if possible."""
    if len(name) <= min_length:
        return name
    for size in range(min_length, min(max_length, len(name)) + 1):
        candidate = name[:size]
        if candidate.lower() == name[:size].lower():
            return candidate
    return name
