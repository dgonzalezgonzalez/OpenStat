"""Independent, bounded implementations of labels, conversions and matrices."""
from __future__ import annotations

import fnmatch
import re

import numpy as np
import pandas as pd

from .mata import MataError
from .parser import split_outside, unquote


_NAME = re.compile(r"[A-Za-z_]\w{0,31}\Z", re.ASCII)
_NUMERIC_FORMAT = re.compile(r"%(-?\d+)(?:\.(\d+))?([fge])([c]?)\Z")
_STRING_FORMAT = re.compile(r"%-?\d+s\Z")
_DATE_FORMAT = re.compile(r"%t[cdwmqhybC][A-Za-z0-9_./,:!+\- ]*\Z")


class MetadataCommands:
    """Mixin; unsupported options and constructs fail before data mutation."""

    @staticmethod
    def _metadata_words(text):
        from .engine import StataError

        result, position = [], 0
        for match in re.finditer(r'"[^"\n]*"|[^\s"]+', text):
            if text[position:match.start()].strip():
                raise StataError("invalid or unclosed quoted string")
            result.append(match[0])
            position = match.end()
        if text[position:].strip():
            raise StataError("invalid or unclosed quoted string")
        return result

    @staticmethod
    def _metadata_text(text, limit):
        from .engine import StataError

        text = text.strip()
        if '"' in text:
            if not re.fullmatch(r'"[^"\n]*"', text):
                raise StataError("unsupported label quoting")
            text = text[1:-1]
        if len(text) > limit:
            raise StataError(f"label exceeds {limit} characters")
        return text

    @staticmethod
    def _metadata_name(name):
        from .engine import StataError

        if not _NAME.fullmatch(name):
            raise StataError(f"invalid name {name}")
        return name

    def _one_metadata_variable(self, text):
        from .engine import StataError

        if not text.strip():
            raise StataError("variable name required")
        names = self.varlist(text)
        if len(names) != 1:
            raise StataError("exactly one variable required")
        return names[0]

    @staticmethod
    def _metadata_numeric_format(value, fmt):
        from .engine import StataError

        match = _NUMERIC_FORMAT.fullmatch(fmt)
        if not match:
            raise StataError("conversion supports numeric f, g and e formats only", 199)
        if pd.isna(value):
            return "."
        precision = int(match[2]) if match[2] is not None else (0 if match[3] == "f" else 12)
        if match[3] == "g" and precision == 0:
            precision = 12
        if precision > 30 or abs(int(match[1])) > 244:
            raise StataError("unsupported numeric format width or precision", 199)
        spec = ("," if match[4] else "") + f".{precision}{match[3]}"
        return format(float(value), spec).strip()

    def _metadata(self, command, arg):
        from .engine import StataError

        parts = split_outside(arg)
        base = parts[0].strip()
        option_text = ",".join(parts[1:])

        if command == "recast":
            raise StataError("recast storage types are not implemented", 199)

        if command == "label":
            head = base.split(maxsplit=1)
            if not head:
                raise StataError("label subcommand required")
            subcommand, rest = head[0], head[1].strip() if len(head) > 1 else ""
            options = self.options(option_text, {"add", "modify", "replace"} if subcommand == "define" else set())
            if subcommand == "variable":
                fields = rest.split(maxsplit=1)
                if len(fields) != 2:
                    raise StataError("label variable requires variable and label")
                name = self._one_metadata_variable(fields[0])
                self.variable_labels[name] = self._metadata_text(fields[1], 80)
                return
            if subcommand == "data":
                self.dataset_label = self._metadata_text(rest, 80)
                return
            if subcommand == "define":
                words = self._metadata_words(rest)
                if len(words) < 3 or len(words) % 2 != 1:
                    raise StataError("label define requires numeric-value and label pairs")
                name = self._metadata_name(words[0])
                if len(options) > 1 or any(value is not True for value in options.values()):
                    raise StataError("use one of add, modify or replace without arguments")
                values = {}
                for key, label in zip(words[1::2], words[2::2]):
                    if not re.fullmatch(r"[+-]?\d+", key):
                        raise StataError("value labels currently require integer nonmissing codes", 199)
                    number = int(key)
                    if not -2147483647 <= number <= 2147483620:
                        raise StataError("value label code out of range")
                    if number in values:
                        raise StataError(f"duplicate value label code {number}")
                    values[number] = self._metadata_text(label, 32000)
                existing = self.value_labels.get(name)
                if existing is not None and not options:
                    raise StataError(f"label {name} already defined", 110)
                if "add" in options and existing is not None and set(values).intersection(existing):
                    raise StataError("add cannot change existing value labels; use modify")
                self.value_labels[name] = ({**existing, **values} if existing is not None and "replace" not in options else values)
                return
            if subcommand == "values":
                words = rest.split()
                if not words:
                    raise StataError("label values requires a variable list")
                # A single argument removes its association. Multiple variables can
                # be detached with an explicit dot, or assigned a named definition.
                label = words[-1] if len(words) > 1 else "."
                names = self.varlist(" ".join(words[:-1]) if len(words) > 1 else words[0])
                if label != ".":
                    self._metadata_name(label)
                for name in names:
                    if not pd.api.types.is_numeric_dtype(self.data[name]):
                        raise StataError("may not label strings", 181)
                for name in names:
                    if label == ".":
                        self.value_label_names.pop(name, None)
                    else:
                        self.value_label_names[name] = label
                return
            if subcommand in {"list", "drop"}:
                names = rest.split()
                if names == ["_all"] or (not names and subcommand == "list"):
                    names = list(self.value_labels)
                if not names and subcommand == "drop" and rest != "_all":
                    raise StataError("label drop requires label names")
                for name in names:
                    if name not in self.value_labels:
                        raise StataError(f"label {name} not found", 111)
                for name in names:
                    if subcommand == "drop":
                        del self.value_labels[name]
                    else:
                        self.emit(f"{name}:")
                        for code, label in sorted(self.value_labels[name].items()):
                            self.emit(f"{code:g} {label}")
                return
            raise StataError(f"label {subcommand} not implemented", 199)

        if command == "format":
            self.options(option_text, set())
            words = base.split()
            formats = [word for word in words if word.startswith("%")]
            if len(formats) != 1:
                raise StataError("format requires a variable list and one display format")
            fmt = formats[0]
            variables = [word for word in words if word != fmt]
            if not variables:
                raise StataError("variable list required")
            names = self.varlist(" ".join(variables))
            string_format = bool(_STRING_FORMAT.fullmatch(fmt))
            if not string_format and not (_NUMERIC_FORMAT.fullmatch(fmt) or _DATE_FORMAT.fullmatch(fmt)):
                raise StataError(f"display format {fmt} not implemented", 199)
            width = re.match(r"%-?(\d+)", fmt)
            if width and not 1 <= int(width[1]) <= 244:
                raise StataError("display format width out of range")
            for name in names:
                numeric = pd.api.types.is_numeric_dtype(self.data[name])
                if numeric == string_format:
                    raise StataError("format does not match variable type", 120)
            for name in names:
                self.formats[name] = fmt
            return

        if command in {"encode", "decode"}:
            options = self.options(option_text, {"generate", "label", "noextend"} if command == "encode" else {"generate"})
            source = self._one_metadata_variable(base)
            target = options.get("generate")
            if not isinstance(target, str) or not target.strip():
                raise StataError(f"{command} requires generate(newvar)")
            self._new_name(target)
            values = self.data[source]
            if command == "encode":
                if pd.api.types.is_numeric_dtype(values):
                    raise StataError("encode requires a string variable", 109)
                if any(not isinstance(value, str) for value in values.dropna()):
                    raise StataError("encode requires string values", 109)
                label_name = options.get("label", target)
                if not isinstance(label_name, str):
                    raise StataError("label() requires a label name")
                self._metadata_name(label_name)
                if "noextend" in options and options["noextend"] is not True:
                    raise StataError("noextend does not accept arguments")
                labels = dict(self.value_labels.get(label_name, {}))
                reverse = {}
                for code in sorted(labels):
                    reverse.setdefault(labels[code], code)
                next_code = max([0, *[int(code) for code in labels if code > 0]]) + 1
                for value in sorted(set(values.dropna()) - {""}):
                    if value not in reverse:
                        if "noextend" in options:
                            raise StataError(f"value {value!r} not in label {label_name}", 182)
                        if next_code > 2147483620:
                            raise StataError("too many encoded values", 134)
                        labels[next_code], reverse[value] = value, next_code
                        next_code += 1
                converted = values.map(lambda value: np.nan if pd.isna(value) or value == "" else reverse[value])
                self.value_labels[label_name] = labels
                self.value_label_names[target] = label_name
                self.formats[target] = "%8.0g"
            else:
                if not pd.api.types.is_numeric_dtype(values):
                    raise StataError("decode requires a numeric variable", 109)
                label_name = self.value_label_names.get(source)
                if label_name not in self.value_labels:
                    raise StataError(f"value label for {source} not found", 111)
                labels = self.value_labels[label_name]
                fmt = self.formats.get(source, "%12.0g")
                converted = values.map(lambda value: "" if pd.isna(value) else labels[value] if value in labels else self._metadata_numeric_format(value, fmt))
                self.formats[target] = "%9s"
            self.data[target] = converted
            if source in self.variable_labels:
                self.variable_labels[target] = self.variable_labels[source]
            return

        if command in {"destring", "tostring"}:
            options = self.options(option_text, {"generate", "replace", "force", "ignore"} if command == "destring" else {"generate", "replace", "force", "format"})
            names = self.varlist(base)
            if not base or not names:
                raise StataError("variable list required")
            if ("generate" in options) == ("replace" in options):
                raise StataError("specify exactly one of generate() or replace")
            for flag in ("replace", "force"):
                if flag in options and options[flag] is not True:
                    raise StataError(f"{flag} does not accept arguments")
            if "generate" in options:
                generated = options["generate"]
                if not isinstance(generated, str):
                    raise StataError("generate() requires new variable names")
                targets = generated.split()
                if len(targets) != len(names) or len(set(targets)) != len(targets):
                    raise StataError("generate() must name one distinct variable per source")
                for name in targets:
                    self._new_name(name)
            else:
                targets = names
            converted = {}
            fmt = options.get("format", "%12.0g")
            if not isinstance(fmt, str):
                raise StataError("format() requires a numeric display format")
            ignored = options.get("ignore", "")
            if not isinstance(ignored, str):
                raise StataError("ignore() requires characters")
            ignored = unquote(ignored)
            for source, target in zip(names, targets):
                values = self.data[source]
                if command == "destring":
                    if pd.api.types.is_numeric_dtype(values):
                        converted[target] = values.copy()
                        continue
                    if any(not isinstance(value, str) for value in values.dropna()):
                        raise StataError("destring requires string or numeric variables", 109)
                    strings = values.fillna("").map(lambda value: value.translate(str.maketrans("", "", ignored)).strip())
                    if strings.str.fullmatch(r"\.[a-z]").any():
                        raise StataError("extended missing values are not implemented", 199)
                    missing = strings.isin(["", "."])
                    numbers = pd.to_numeric(strings.mask(missing), errors="coerce").astype(float)
                    invalid = (~missing) & (numbers.isna() | ~np.isfinite(numbers))
                    if invalid.any() and "force" not in options:
                        raise StataError(f"{source} contains nonnumeric characters; use force", 7)
                    converted[target] = numbers.mask(invalid)
                else:
                    if not pd.api.types.is_numeric_dtype(values):
                        raise StataError("tostring requires numeric variables", 109)
                    strings = values.map(lambda value: self._metadata_numeric_format(value, fmt))
                    if "force" not in options:
                        roundtrip = pd.to_numeric(strings.str.replace(",", "", regex=False), errors="coerce")
                        if ((roundtrip != values) & values.notna()).any():
                            raise StataError(f"{source} cannot be converted reversibly; use force", 7)
                    converted[target] = strings
            # All inputs/options are validated before committing any columns.
            for source, target in zip(names, targets):
                self.data[target] = converted[target]
                self.formats[target] = "%9.0g" if command == "destring" else "%9s"
                self.value_label_names.pop(target, None)
                if source in self.variable_labels:
                    self.variable_labels[target] = self.variable_labels[source]
            return
        raise StataError(f"metadata command {command} not implemented", 199)

    def _matrix(self, arg):
        from .engine import StataError

        parts = split_outside(arg)
        base = parts[0].strip()
        words = base.split()
        if words and words[0] in {"list", "drop", "dir"}:
            action = words[0]
            options = self.options(",".join(parts[1:]), {"format"} if action == "list" else set())
            patterns = words[1:] or (["_all"] if action in {"list", "dir"} else [])
            if not patterns or (action == "dir" and words[1:]):
                raise StataError(f"invalid matrix {action} syntax")
            selected = []
            for pattern in patterns:
                names = list(self.matrices) if pattern == "_all" else [name for name in self.matrices if fnmatch.fnmatchcase(name, pattern)]
                if not names:
                    raise StataError(f"matrix {pattern} not found", 111)
                selected.extend(name for name in names if name not in selected)
            fmt = options.get("format")
            if fmt is not None and not isinstance(fmt, str):
                raise StataError("format() requires a numeric format")
            if fmt is not None:
                self._metadata_numeric_format(0, fmt)
            for name in selected:
                if action == "drop":
                    del self.matrices[name]
                elif action == "dir":
                    self.emit(f"{name}: {np.atleast_2d(self.matrices[name]).shape}")
                else:
                    value = np.atleast_2d(self.matrices[name])
                    rendered = "\n".join(" ".join(self._metadata_numeric_format(x, fmt) for x in row) for row in value) if fmt else str(value)
                    self.emit(f"{name}[{value.shape[0]},{value.shape[1]}]\n{rendered}")
            return
        if len(parts) != 1:
            raise StataError("matrix assignment options are not implemented", 199)
        match = re.fullmatch(r"([A-Za-z_]\w{0,31})\s*=\s*(.+)", base, re.ASCII)
        if not match:
            raise StataError("matrix assignment or supported subcommand required", 199)
        name, expression = match.groups()
        overlay = {**self.scalars, **self.matrices}
        def result_matrix(found):
            key = found[0]
            value = self.matrices.get(key, self.matrices.get(found[1]))
            if value is None:
                raise StataError(f"matrix {key} not found", 111)
            alias = f"__openstat_result_{found[1]}"
            overlay[alias] = value
            return alias
        expression = re.sub(r"e\((b|V)\)", result_matrix, expression)
        allowed = {"I", "J", "transpose", "invsym", "luinv", "det", "trace", "diagonal", "diag", "sqrt", "exp", "ln", "rows", "cols", "sum", "mean", "colsum", "rowsum", "cross"}
        calls = re.findall(r"\b([A-Za-z_]\w*)\s*\(", expression)
        if any(call not in allowed for call in calls) or "**" in expression or any(char in expression for char in (":", "^", '"')):
            raise StataError("unsupported matrix expression", 199)
        original = self.mata.variables
        try:
            self.mata.variables = overlay
            value = np.asarray(self.mata.evaluate(expression))
            if value.ndim > 2 or value.dtype.kind not in "biuf":
                raise StataError("only real numeric matrices are implemented", 199)
            value = np.atleast_2d(value).astype(float)
            if not all(value.shape):
                raise StataError("empty matrices are not implemented", 199)
        except (MataError, ValueError, TypeError, np.linalg.LinAlgError) as error:
            raise StataError(str(error), 503) from error
        finally:
            self.mata.variables = original
        self.matrices[name] = value.copy()
