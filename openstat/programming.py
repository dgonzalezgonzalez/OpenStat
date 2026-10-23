"""Independent implementations of documented programming primitives.

Unsupported grammar is rejected explicitly. No vendor ado source is included.
Reference contracts: https://www.stata.com/manuals/psyntax.pdf,
https://www.stata.com/manuals/pgettoken.pdf, and pmark.pdf.

The engine routes the commands of this module through ``_programming`` and
owns the program-support commands itself, so the mixin below renames the
helpers that would otherwise shadow the engine's own methods.

``syntax`` publishes the sample it captured as ``_syntax_varlist``,
``_syntax_if`` and ``_syntax_in`` and records the specification in the
``_syntax_spec`` local; the engine's ``marksample`` reads those attributes
to build the to-use marker of a program.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .errors import StataError
from .parser import outside_positions, quoted_end, split_outside, tokens, unquote


def expand_macros(text, locals_, globals_, *, evaluate=None, extended=None, error_type=ValueError, limit=100):
    """Expand nested local/global references while retaining compound quotes.

    Both ``prefix`name'' and ${prefix`name'} resolve their inner names first.
    Backslash-protected macro introducers remain literal for this expansion.
    """
    protected = {}

    def fail(message):
        raise error_type(message)

    def expand(value, depth):
        if depth > limit:
            fail("macro expansion recursion limit")
        output, i = [], 0
        while i < len(value):
            if value[i] == "\\" and i+1 < len(value) and value[i+1] in "`$":
                marker = f"\x00macro_{len(protected)}\x00"
                protected[marker] = value[i+1]
                output.append(marker)
                i += 2
                continue
            if value[i] == "`" and not value.startswith('`"', i):
                start, nesting, j = i+1, 1, i+1
                while j < len(value):
                    if value[j] == "`" and not value.startswith('`"', j):
                        nesting += 1
                    elif value[j] == "'":
                        nesting -= 1
                        if nesting == 0:
                            break
                    j += 1
                if nesting:
                    fail("unclosed local macro reference")
                name = expand(value[start:j], depth+1)
                if name.startswith("="):
                    if evaluate is None:
                        fail("inline expression macro requires an evaluator")
                    replacement = evaluate(name[1:])
                    if isinstance(replacement, (np.integer, np.floating, int, float)):
                        replacement = "." if pd.isna(replacement) else format(replacement, ".15g")
                elif name.startswith(":"):
                    if extended is None:
                        fail("inline extended macro requires a resolver")
                    replacement = extended(name[1:].strip())
                elif evaluate is not None and re.fullmatch(r"[rec]\([A-Za-z_]\w*\)",name):
                    replacement = evaluate(name)
                    if isinstance(replacement,(np.integer,np.floating,int,float)):
                        replacement = "." if pd.isna(replacement) else format(replacement,".15g")
                else:
                    replacement = locals_.get(name, "")
                output.append(expand(str(replacement), depth+1))
                i = j+1
                continue
            if value[i] == "$":
                if value.startswith("${", i):
                    end = value.find("}", i+2)
                    if end < 0:
                        fail("unclosed global macro reference")
                    name = expand(value[i+2:end], depth+1)
                    i = end+1
                else:
                    match = re.match(r"[A-Za-z_]\w*", value[i+1:])
                    if not match:
                        output.append("$")
                        i += 1
                        continue
                    name = match[0]
                    i += len(name)+1
                output.append(expand(str(globals_.get(name, "")), depth+1))
                continue
            output.append(value[i])
            i += 1
        return "".join(output)

    result = expand(str(text), 0)
    for marker, literal in protected.items():
        result = result.replace(marker, literal)
    return result


def _error(message, rc=198):
    """Raise the runtime's error type with Stata's return-code convention."""
    raise StataError(message, rc)


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def numeric_range(text, *, integer=False, maximum=100000):
    """Expand one documented numeric range, with a finite allocation bound."""
    if match := re.fullmatch(rf"\s*({_NUMBER})\s*/\s*({_NUMBER})\s*", text):
        start, stop, step = float(match[1]), float(match[2]), 1.
    elif match := re.fullmatch(rf"\s*({_NUMBER})\s*[([]\s*({_NUMBER})\s*[)\]]\s*({_NUMBER})\s*", text):
        start, step, stop = map(float, (match[1], match[2], match[3]))
    elif match := re.fullmatch(rf"\s*({_NUMBER})\s+({_NUMBER})\s*(?:to|:)\s*({_NUMBER})\s*", text):
        start, second, stop = map(float, (match[1], match[2], match[3]))
        step = second-start
    else:
        _error("invalid numeric range", 121)
    if integer and any(value != int(value) for value in (start, step, stop)):
        _error("integer loop range required", 198)
    if not step:
        _error("zero loop increment", 198)
    count = max(0, int(np.floor((stop-start)/step + 1e-10))+1)
    if count > maximum:
        _error("numeric range exceeds execution limit", 901)
    return [format(start+i*step, ".15g") for i in range(count)]


def numlist(text, *, maximum=1600, missing=False):
    """Expand numeric lists used by foreach, syntax, and the numlist command."""
    result, i = [], 0
    # Longer forms must precede individual numbers; a two-number range consumes
    # its first number only when a colon or 'to' follows the second number.
    pattern = re.compile(rf"({_NUMBER}\s+{_NUMBER}\s*(?:to|:)\s*{_NUMBER}|{_NUMBER}\s*[([]\s*{_NUMBER}\s*[)\]]\s*{_NUMBER}|{_NUMBER}\s*/\s*{_NUMBER}|{_NUMBER}|\.)")
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        match = pattern.match(text, i)
        if not match or (match.end() < len(text) and not text[match.end()].isspace()):
            _error("invalid numlist", 121)
        item = match[0]
        if item == ".":
            if not missing:
                _error("invalid numlist has missing values", 127)
            result.append(".")
        elif re.fullmatch(_NUMBER, item):
            result.append(format(float(item), ".15g"))
        else:
            result.extend(numeric_range(item, maximum=maximum))
        if len(result) > maximum:
            _error("invalid numlist has too many elements", 123)
        i = match.end()
    return result


def _balanced_end(text, start):
    depth, i = 1, start+1
    while i < len(text):
        end = quoted_end(text, i)
        if end is not None:
            i = end
            continue
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if not depth:
                return i+1
        i += 1
    _error("unclosed parentheses")


def _split_arguments(text: str) -> list[str]:
    """Split an argument list at its top-level commas.

    The engine's parser splits on the commas that lie outside quotes and
    brackets, but it does not read a compound double-quoted string as one
    run.  A command that takes a string is therefore split here, so that
    ``tokenize `"a, b"''`` keeps its string intact.
    """
    parts, start, depth, index = [], 0, 0, 0
    while index < len(text):
        end = quoted_end(text, index)
        if end is not None:
            index = end
            continue
        char = text[index]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return parts


def _option_words(text):
    """Read name or name(argument), preserving nested argument expressions."""
    result, i = [], 0
    while i < len(text):
        while i < len(text) and (text[i].isspace() or text[i] == ","):
            i += 1
        if i == len(text):
            break
        match = re.match(r"[A-Za-z_]\w*", text[i:])
        if not match:
            _error(f"invalid option {text[i:]}")
        start, name = i, match[0]
        i += len(name)
        while i < len(text) and text[i].isspace():
            i += 1
        argument = None
        if i < len(text) and text[i] == "(":
            end = _balanced_end(text, i)
            argument = text[i+1:end-1]
            i = end
        result.append((name.lower(), argument, text[start:i].strip()))
    return result


def _descriptor_units(text, initially_optional=False):
    result, i, optional = [], 0, int(initially_optional)
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        if text[i] in "[]":
            optional += 1 if text[i] == "[" else -1
            if optional < 0:
                _error("unmatched syntax bracket")
            i += 1
            continue
        start = i
        while i < len(text) and not text[i].isspace() and text[i] not in "[]":
            if text[i] == "(":
                i = _balanced_end(text, i)
            else:
                i += 1
        result.append((text[start:i], bool(optional)))
    if optional:
        _error("unclosed syntax bracket")
    return result


@dataclass
class _Option:
    name: str
    minimum: int
    kind: str | None
    modifiers: str
    optional: bool
    macro: str


_WEIGHTS = {"aweight": "aweight", "aw": "aweight", "fweight": "fweight", "fw": "fweight",
            "iweight": "iweight", "iw": "iweight", "pweight": "pweight", "pw": "pweight"}


class ProgrammingCommands:
    """Mixin implementing the programming primitives the engine routes here.

    The engine owns the program-support commands (``marksample``, ``mark``,
    ``markout``, ``tempname``, ``tempvar`` and ``tempfile``) and it defines
    ``_gettoken`` and ``_numlist`` itself.  A method defined directly on
    ``Engine`` always wins over a mixin's, and the earlier base class wins
    among the mixins, so the implementations below use names that cannot
    shadow the engine's own.
    """

    def _programming(self, command, arg):
        """Claim one of the programming commands, or return ``False``.

        ``True`` means the command was handled here; ``False`` leaves it to
        the engine's own dispatch table.
        """
        handlers = {"syntax": self._syntax, "tokenize": self._tokenize,
                    "gettoken": self._programming_gettoken, "unab": self._unab,
                    "ds": self._ds, "levelsof": self._levelsof,
                    "numlist": self._programming_numlist}
        handler = handlers.get(command)
        if handler is not None:
            handler(arg)
            return True
        if command in {"local", "global"} and re.match(r"\w+\s*:", arg):
            self._programming_extended_macro(command, arg)
            return True
        if command == "macro" and arg.startswith("shift"):
            match = re.fullmatch(r"shift(?:\s+(\d+))?", arg)
            if not match:
                _error("invalid macro shift")
            amount = int(match[1] or 1)
            numbered = {int(k): v for k, v in self.locals.items() if k.isdigit() and int(k) > 0}
            for k in numbered:
                self.locals.pop(str(k), None)
            self.locals.update({str(k-amount): v for k, v in numbered.items() if k > amount})
            return True
        return False

    def _programming_temp_name(self) -> str:
        """Return a fresh hidden name such as ``__os000001``.

        The newer engine has no ``_temporary_name`` helper, so the mixin
        keeps its own counter-based allocator.  The name never collides
        with a dataset column, a scalar or a matrix.
        """
        index = getattr(self, "_programming_temp_id", 0) + 1
        while True:
            self._programming_temp_id = index
            name = f"__os{index:06d}"
            if (name not in self.data and name not in self.scalars
                    and name not in self.matrices):
                return name
            index += 1

    def _programming_numlist(self, arg):
        parts = split_outside(arg)
        options = self.options(" ".join(parts[1:]), {"ascending", "descending", "integer", "missingokay", "min", "max", "range", "sort"})
        minimum, maximum = int(options.get("min", 1)), int(options.get("max", 1600))
        if minimum < 0 or maximum > 1600 or maximum < minimum:
            _error("invalid numlist limits", 198)
        values = numlist(unquote(parts[0]), maximum=maximum, missing="missingokay" in options)
        if len(values) < minimum:
            _error("invalid numlist has too few elements", 122)
        numbers = [np.inf if value == "." else float(value) for value in values]
        if "integer" in options and any(np.isfinite(v) and v != int(v) for v in numbers):
            _error("invalid numlist has noninteger elements", 126)
        if "ascending" in options and any(a >= b for a,b in zip(numbers,numbers[1:])):
            _error("invalid numlist has elements out of order", 124)
        if "descending" in options and any(a <= b for a,b in zip(numbers,numbers[1:])):
            _error("invalid numlist has elements out of order", 124)
        if "range" in options:
            comparisons = re.findall(rf"(<=|>=|<|>)({_NUMBER})", str(options["range"]))
            if not comparisons or re.sub(rf"(?:<=|>=|<|>){_NUMBER}", "", str(options["range"])).strip():
                _error("invalid numlist range", 198)
            for operator, boundary in comparisons:
                bound = float(boundary)
                tests = {"<": lambda x:x<bound, "<=":lambda x:x<=bound, ">":lambda x:x>bound, ">=":lambda x:x>=bound}
                if not all(tests[operator](value) for value in numbers):
                    _error("invalid numlist has elements outside of allowed range", 125)
        if "sort" in options:
            values = [value for _,value in sorted(zip(numbers,values), key=lambda item:item[0])]
        self.returns.clear()
        self.returns["numlist"] = " ".join(values)

    def _programming_varlist_spec(self, value, specification, *, default=False, new=False):
        match = re.fullmatch(r"(varlist|varname|newvarlist|newvarname)(?:\((.*)\))?", specification, re.I)
        if not match:
            _error("invalid varlist descriptor")
        kind, spec = match[1].lower(), match[2] or ""
        known = re.sub(r"\b(?:min|max)\s*=\s*\d+|default\s*=\s*none|\b(?:numeric|string|generate)\b", " ", spec)
        if known.strip():
            _error(f"varlist descriptor not implemented: {known.strip()}", 199)
        minimum = re.search(r"\bmin\s*=\s*(\d+)", spec)
        maximum = re.search(r"\bmax\s*=\s*(\d+)", spec)
        min_count = int(minimum[1]) if minimum else 1
        max_count = int(maximum[1]) if maximum else (1 if kind.endswith("name") else 32767)
        if new:
            names = value.split()
            for name in names:
                self._new_name(name)
            if len(set(names)) != len(names):
                _error("duplicate new variable name", 198)
        else:
            names = self.varlist(value, default=default and "default=none" not in spec.replace(" ", ""))
        if len(names) < min_count:
            _error("too few variables specified", 102)
        if len(names) > max_count:
            _error("too many variables specified", 103)
        if not new:
            if "numeric" in spec.split() and any(not pd.api.types.is_numeric_dtype(self.data[n]) for n in names):
                _error("string variables not allowed in varlist", 109)
            if "string" in spec.split() and any(pd.api.types.is_numeric_dtype(self.data[n]) for n in names):
                _error("numeric variables not allowed in varlist", 109)
        elif "generate" in spec.split():
            for name in names:
                self.data[name] = "" if "string" in spec.split() else np.nan
        return " ".join(names)

    def _syntax(self, description):
        comma = next((i for i in outside_positions(description, "(") if description[i] == ","), None)
        if comma is None:
            head, tail, option_optional = description, "", False
        else:
            head, tail = description[:comma], description[comma+1:]
            option_optional = head.count("[") > head.count("]")
            if option_optional:
                index = head.rfind("[")
                head = head[:index] + head[index+1:]
        units = _descriptor_units(head)
        option_units = _descriptor_units(tail, initially_optional=option_optional)
        descriptors, weights, var_descriptor, option_defs, extra = {}, [], None, [], False
        for item, optional in units:
            lower = item.lower()
            if re.match(r"(?:new)?var(?:list|name)(?:\(|$)", lower):
                if var_descriptor:
                    _error("multiple list descriptors")
                var_descriptor = (item, optional)
            elif re.match(r"(?:name|namelist|anything)(?:\(|$)", lower):
                if var_descriptor:
                    _error("multiple list descriptors")
                var_descriptor = (item, optional)
            elif lower.rstrip("/") in {"if", "in", "using"}:
                descriptors[lower.rstrip("/")] = (optional, lower.endswith("/"))
            elif lower.rstrip("/") in _WEIGHTS:
                weights.append((_WEIGHTS[lower.rstrip("/")], lower.endswith("/")))
            elif lower in {"=exp", "=/exp"}:
                descriptors["exp"] = (optional, lower == "=/exp")
            else:
                _error(f"syntax descriptor not implemented: {item}", 199)
        for item, optional in option_units:
            if item == "*":
                extra = True
                continue
            match = re.fullmatch(r"([A-Za-z_]\w*)(?:\((.*)\))?", item)
            if not match:
                _error("invalid option descriptor")
            raw_name, content = match[1], match[2]
            minimum = max((i+1 for i, char in enumerate(raw_name) if char.isupper()), default=len(raw_name))
            name = raw_name.lower()
            macro = name[2:] if raw_name.startswith("no") else name
            kind, _, modifiers = (content or "").partition(" ")
            kind = {"int":"integer", "str":"string"}.get(kind,kind)
            option_defs.append(_Option(name, minimum, kind or None, modifiers.strip(), optional, macro[:31]))
        if weights and "exp" in descriptors:
            _error("syntax cannot allow both weights and =exp")
        call = str(self.locals.get("0", getattr(self, "_program_call_args", "")))
        call_parts = split_outside(call)
        if len(call_parts) > 2:
            # Commas inside option arguments are preserved by split_outside.
            _error("invalid extra comma in command")
        before, option_text = call_parts[0], call_parts[1] if len(call_parts) == 2 else ""
        parsed, weight_type, weight_expression = {}, "", ""
        for key in descriptors:
            parsed[key] = ""
        positions = []
        for i in outside_positions(before):
            if before[i:i+1] == "[":
                continue
            if i == 0 or before[i-1].isspace():
                match = re.match(r"(if|in|using)(?=\s|$)", before[i:])
                if match:
                    positions.append((i, match[1]))
        # Weight brackets are not exposed by outside_positions; locate them with
        # the same quote-aware scanner and remove before qualifier slicing.
        i, bracket = 0, None
        while i < len(before):
            end = quoted_end(before, i)
            if end is not None:
                i = end
                continue
            if before[i] == "[":
                end = before.find("]", i+1)
                if end < 0:
                    _error("unclosed weight")
                if bracket is not None:
                    _error("multiple weight expressions")
                bracket = (i, end+1, before[i+1:end])
                i = end+1
            else:
                i += 1
        if bracket:
            match = re.fullmatch(r"\s*([A-Za-z]*)\s*=\s*(.+?)\s*", bracket[2])
            if not match or not weights:
                _error("weights not allowed", 101)
            alias = match[1].lower()
            weight_type = _WEIGHTS.get(alias, "") if alias else weights[0][0]
            if weight_type not in {w[0] for w in weights}:
                _error("weight type not allowed", 101)
            weight_expression = match[2]
            before = before[:bracket[0]] + " " + before[bracket[1]:]
            positions = []
            for i in outside_positions(before):
                if i == 0 or before[i-1].isspace():
                    match = re.match(r"(if|in|using)(?=\s|$)", before[i:])
                    if match:
                        positions.append((i, match[1]))
        base = before[:positions[0][0]].strip() if positions else before.strip()
        for n, (position, key) in enumerate(positions):
            if key not in descriptors:
                _error(f"{key} not allowed", 101)
            if parsed[key]:
                _error(f"multiple {key} qualifiers")
            value = before[position+len(key):positions[n+1][0] if n+1 < len(positions) else None].strip()
            if not value:
                _error(f"{key} expression required")
            if key == "in" and not re.fullmatch(r"(?:-?\d+|f|l)(?:/(?:-?\d+|f|l))?", value):
                _error("invalid in range")
            if key == "using":
                if len(tokens(value)) != 1:
                    _error("invalid using filename")
                parsed[key] = unquote(value) if descriptors[key][1] else "using " + '"' + unquote(value) + '"'
            else:
                parsed[key] = value if descriptors[key][1] else key + " " + value
        if "exp" in descriptors:
            exp_pos = next((i for i in outside_positions(base) if base[i] == "="), None)
            if exp_pos is not None:
                expression = base[exp_pos+1:].strip()
                if not expression:
                    _error("expression required")
                parsed["exp"] = expression if descriptors["exp"][1] else "= " + expression
                base = base[:exp_pos].strip()
        for key, (optional, _) in descriptors.items():
            if not optional and not parsed[key]:
                _error(f"{key} required", 100)
        if var_descriptor:
            specification, optional = var_descriptor
            kind = specification.split("(", 1)[0].lower()
            if not base and not optional:
                _error(f"{kind} required", 100)
            if kind in {"varlist", "varname", "newvarlist", "newvarname"}:
                if not base and optional and "default=none" in specification.replace(" ", ""):
                    parsed["varlist"] = ""
                elif not base and optional and kind.startswith("new"):
                    parsed["varlist"] = ""
                else:
                    parsed["varlist"] = self._programming_varlist_spec(base, specification, default=optional, new=kind.startswith("new"))
                if kind.startswith("new"):
                    parsed["typlist"] = " ".join("float" for _ in parsed["varlist"].split())
            else:
                details = specification.partition("(")[2].removesuffix(")")
                known_details = re.sub(r'\b(?:name|id)\s*=\s*(?:"[^"\n]*"|\w+)|\b(?:min|max)\s*=\s*\d+|\blocal\b', " ", details)
                if known_details.strip():
                    _error(f"list descriptor not implemented: {known_details.strip()}", 199)
                name_match = re.search(r"(?:^|\s)name\s*=\s*(\w+)", details)
                target = name_match[1] if name_match else "namelist" if kind in {"name", "namelist"} else "anything"
                if kind in {"name", "namelist"}:
                    names = base.split()
                    pattern = r"\w{1,31}" if "local" in details.split() else r"[A-Za-z_]\w{0,31}"
                    if any(not re.fullmatch(pattern, name) for name in names):
                        _error("invalid name")
                    maximum = re.search(r"\bmax\s*=\s*(\d+)", details)
                    minimum = re.search(r"\bmin\s*=\s*(\d+)", details)
                    if len(names) > (int(maximum[1]) if maximum else 1 if kind == "name" else 32767):
                        _error("too many names specified", 103)
                    if len(names) < (int(minimum[1]) if minimum else int(not optional)):
                        _error("too few names specified", 102)
                    parsed[target] = " ".join(names)
                else:
                    parsed[target] = base
        elif base:
            _error("varlist not allowed", 101)
        if weights:
            parsed["weight"] = weight_type
            slash = next((slash for name, slash in weights if name == weight_type), False)
            parsed["exp"] = ("" if slash else "= ") + weight_expression if weight_type else ""
        specified, remaining = set(), []
        for name, value, original in _option_words(option_text):
            matches = [option for option in option_defs if option.name.startswith(name) and len(name) >= option.minimum]
            if len(matches) > 1:
                _error(f"option {name} ambiguous")
            if not matches:
                if extra:
                    remaining.append(original)
                    continue
                _error(f"option {name} not allowed")
            option = matches[0]
            if option.name in specified:
                _error(f"option {option.name} specified more than once")
            specified.add(option.name)
            parsed[option.macro] = self._syntax_option(option, value)
        for option in option_defs:
            if option.name not in specified:
                if not option.optional:
                    _error(f"option {option.name} required")
                if option.kind in {"integer", "real"}:
                    if not option.modifiers:
                        _error(f"default required for optional {option.kind} option")
                    parsed[option.macro] = self._syntax_option(option, option.modifiers)
                elif option.kind in {"cilevel", "crlevel"}:
                    parsed[option.macro] = str(getattr(self, "_confidence_level", 95))
                else:
                    parsed[option.macro] = ""
        if extra:
            parsed["options"] = " ".join(remaining)
        self.locals.update(parsed)
        # The engine's ``marksample`` builds the to-use marker from the three
        # attributes below, so ``syntax`` publishes them under exactly those
        # names: the marker then honours the ``if``/``in`` qualifiers and the
        # varlist that this specification captured.
        self._syntax_varlist = parsed.get("varlist", "")
        self._syntax_if = parsed.get("if", "")
        self._syntax_in = parsed.get("in", "")
        self.locals["_syntax_spec"] = description.strip()
        # Keep sample information with this local-macro scope. Nested programs
        # cannot overwrite the parent's marksample restrictions.
        self.locals["__openstat_syntax_varlist"] = parsed.get("varlist", "")
        for key in ("if", "in"):
            value = parsed.get(key, "")
            self.locals["__openstat_syntax_"+key] = key+" "+value if value and descriptors[key][1] else value
        self.locals["__openstat_syntax_weight"] = weight_type
        self.locals["__openstat_syntax_weight_exp"] = weight_expression

    def _syntax_option(self, option, value):
        kind = option.kind
        if kind is None:
            if value is not None:
                _error(f"option {option.name} does not take an argument")
            return option.name
        if value is None or not value.strip():
            _error(f"option {option.name} requires an argument")
        if kind == "string":
            if option.modifiers not in {"", "asis"}:
                _error("string option modifier not implemented", 199)
            return value if option.modifiers == "asis" else unquote(value)
        if kind == "passthru":
            return option.name + "(" + value + ")"
        if kind in {"integer", "real", "cilevel", "crlevel"}:
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()):
                _error(f"option {option.name} requires a {kind}")
            number = float(value)
            if kind == "integer" and number != int(number):
                _error(f"option {option.name} requires an integer")
            if kind in {"cilevel", "crlevel"} and not 0 < number < 100:
                _error("invalid confidence level")
            return format(number, ".15g")
        if kind in {"varlist", "varname"}:
            return self._programming_varlist_spec(value, kind + "(" + option.modifiers + ")")
        if kind in {"name", "namelist"}:
            names = value.split()
            if any(not re.fullmatch(r"[A-Za-z_]\w{0,31}", name) for name in names):
                _error("invalid name")
            if kind == "name" and len(names) != 1:
                _error("one name required")
            if option.modifiers:
                _error("namelist option modifiers not implemented", 199)
            return " ".join(names)
        _error(f"option descriptor {kind} not implemented", 199)

    def _take_token(self, text, *, parse=" ", quotes=False, bind=False):
        text = text.lstrip()
        if not text:
            return "", "", False
        end = quoted_end(text, 0)
        quoted = end is not None
        if end is None:
            if text[0] in parse and not text[0].isspace():
                end = 1
            else:
                end, nesting = 0, 0
                while end < len(text):
                    quote_end = quoted_end(text, end)
                    if quote_end is not None:
                        end = quote_end
                        continue
                    char = text[end]
                    if bind and char == "(":
                        nesting += 1
                    elif bind and char == ")":
                        nesting -= 1
                    if (char.isspace() or char in parse) and nesting == 0:
                        break
                    end += 1
        first = text[:end]
        return first if quotes else unquote(first), text[end:].lstrip(), quoted

    def _tokenize(self, arg):
        parts = _split_arguments(arg)
        if len(parts) > 2:
            _error("invalid tokenize syntax")
        options = dict((name, value) for name, value, _ in _option_words(parts[1] if len(parts) > 1 else ""))
        if set(options) - {"parse"}:
            _error("unsupported tokenize option", 199)
        parse = unquote(options.get("parse") or " ")
        value, result = unquote(parts[0]), []
        while value.strip():
            first, value, _ = self._take_token(value, parse=parse)
            result.append(first)
        for key in list(self.locals):
            if key.isdigit() and int(key) > 0:
                del self.locals[key]
        self.locals.update({str(i): word for i, word in enumerate(result, 1)})

    def _programming_gettoken(self, arg):
        """Implement ``gettoken``.

        The source is the text after the colon.  A single word that names a
        local macro is read from that macro, which is how the older branch
        used the command; every other text is taken literally.
        """
        parts = _split_arguments(arg)
        match = re.fullmatch(r"(\w+)(?:\s+(\w+))?\s*:\s*(.*)", parts[0], re.S)
        if not match or len(parts) > 2:
            _error("invalid gettoken syntax")
        options = dict((name, value) for name, value, _ in _option_words(parts[1] if len(parts) > 1 else ""))
        if set(options) - {"parse", "quotes", "qed", "bind"}:
            _error("gettoken option not implemented", 199)
        source = match[3].strip()
        if re.fullmatch(r"\w+", source) and source in self.locals:
            source = str(self.locals[source])
        first, rest, quoted = self._take_token(source, parse=unquote(options.get("parse") or " "),
                                               quotes="quotes" in options, bind="bind" in options)
        self.locals[match[1]] = first
        if match[2]:
            self.locals[match[2]] = rest
        if "qed" in options:
            if not options["qed"] or not re.fullmatch(r"\w+", options["qed"]):
                _error("qed requires a local macro name")
            self.locals[options["qed"]] = str(int(quoted))

    def _unab(self, arg):
        match = re.fullmatch(r"(\w+)\s*:\s*(.+)", arg)
        if not match:
            _error("invalid unab syntax")
        self.locals[match[1]] = " ".join(self.varlist(match[2]))

    def _ds(self, arg):
        parts = split_outside(arg)
        options = self.options(" ".join(parts[1:]), {"has", "not"})
        selected = self.varlist(parts[0])
        if "has" in options:
            content = str(options["has"])
            if content not in {"type numeric", "type string"}:
                _error("ds has() filter not implemented", 199)
            numeric = content == "type numeric"
            selected = [name for name in selected if pd.api.types.is_numeric_dtype(self.data[name]) == numeric]
        if "not" in options:
            selected = [name for name in self.data if name not in selected]
        self.returns.clear()
        self.returns["varlist"] = " ".join(selected)
        if selected:
            self.emit("  ".join(selected))

    def _levelsof(self, arg):
        parts = split_outside(arg)
        base, mask = self.qualifiers(parts[0])
        names = self.varlist(base, default=False)
        if len(names) != 1:
            _error("one variable required", 103)
        options = self.options(" ".join(parts[1:]), {"local", "clean", "missing", "separate"})
        series = self.data.loc[mask, names[0]]
        values = series.drop_duplicates().tolist()
        values = sorted((v for v in values if pd.notna(v) and v != ""))
        if "missing" in options and series.isna().any():
            values.append(np.nan)
        strings = not pd.api.types.is_numeric_dtype(series)
        formatted = [str(value) if strings else self._format(value) for value in values]
        if strings and "clean" not in options:
            formatted = ['`"' + value + '"\'' for value in formatted]
        separator = unquote(str(options["separate"])) if "separate" in options else " "
        output = separator.join(formatted)
        self.returns.clear()
        self.returns["levels"] = output
        if "local" in options:
            target = str(options["local"])
            if not re.fullmatch(r"\w+", target):
                _error("local() requires a macro name")
            self.locals[target] = output
        self.emit(output)

    def _programming_extended_macro(self, command, arg):
        match = re.fullmatch(r"(\w+)\s*:\s*(.*)", arg)
        name, expression = match[1], match[2].strip()
        value = None
        if match := re.fullmatch(r"word\s+count\s+(.*)", expression):
            value = str(len(tokens(match[1])))
        elif match := re.fullmatch(r"word\s+(-?\d+)\s+of\s+(.*)", expression):
            words, index = tokens(match[2]), int(match[1])
            value = unquote(words[index-1]) if 1 <= index <= len(words) else ""
        elif match := re.fullmatch(r"(subinstr|subinword)\s+(local|global)\s+(\w+)\s+(.*)", expression):
            parts = split_outside(match[4])
            args = tokens(parts[0])
            if len(args) != 2 or len(parts) > 2 or (len(parts) == 2 and parts[1] != "all"):
                _error("invalid macro substitution")
            original = str((self.locals if match[2] == "local" else self.globals).get(match[3], ""))
            old, new = map(unquote, args)
            if not old:
                _error("empty substitution target")
            count = 0 if len(parts) == 2 else 1
            value = re.sub(r"(?<!\S)"+re.escape(old)+r"(?!\S)", lambda _: new, original, count=count) if match[1] == "subinword" else original.replace(old, new, -1 if count == 0 else 1)
        elif match := re.fullmatch(r"list\s+(.*)", expression):
            value = self._macro_list(match[1])
        elif match := re.fullmatch(r"(variable\s+label|value\s+label|type|format)\s+(\w+)", expression):
            kind, variable = match[1], match[2]
            self.varlist(variable)
            if kind == "variable label":
                value = self.variable_labels.get(variable, "")
            elif kind == "value label":
                value = self.value_label_names.get(variable, "")
            elif kind == "format":
                value = self.formats.get(variable, "%9.0g")
            else:
                dtype = self.data[variable].dtype
                value = self.storage_types.get(variable)
                if value is None:
                    value = "float" if dtype==np.dtype("float32") else "double" if pd.api.types.is_float_dtype(dtype) else "long" if pd.api.types.is_integer_dtype(dtype) else "str" + str(max(1, self.data[variable].fillna("").str.len().max()))
        else:
            _error("extended macro function not implemented", 199)
        (self.locals if command == "local" else self.globals)[name] = value

    def _macro_list(self, expression):
        if match := re.fullmatch(r"(uniq|dups|sort|sizeof)\s+(\w+)", expression):
            words = tokens(str(self.locals.get(match[2], "")))
            operation = match[1]
            if operation == "sizeof":
                return str(len(words))
            if operation == "sort":
                return " ".join(sorted(words))
            if operation == "uniq":
                return " ".join(dict.fromkeys(words))
            seen, duplicates = set(), []
            for word in words:
                if word in seen and word not in duplicates:
                    duplicates.append(word)
                seen.add(word)
            return " ".join(duplicates)
        if match := re.fullmatch(r"posof\s+(.*?)\s+in\s+(\w+)", expression):
            words = [unquote(x) for x in tokens(str(self.locals.get(match[2], "")))]
            word = unquote(match[1])
            return str(words.index(word)+1 if word in words else 0)
        if match := re.fullmatch(r"(\w+)\s*([|&-]|in|==|===|!=)\s*(\w+)", expression):
            left, right = tokens(str(self.locals.get(match[1], ""))), tokens(str(self.locals.get(match[3], "")))
            operator = match[2]
            if operator == "|":
                return " ".join(dict.fromkeys(left+right))
            if operator in {"&", "-"}:
                return " ".join(dict.fromkeys(word for word in left if (word in right) == (operator == "&")))
            if operator == "in":
                return str(int(set(left) <= set(right)))
            if operator == "===":
                return str(int(left == right))
            equal = set(left) == set(right)
            return str(int(equal if operator == "==" else not equal))
        _error("macro list function not implemented", 199)
