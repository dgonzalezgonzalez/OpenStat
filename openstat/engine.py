"""Independent Stata-style interpreter. Unsupported syntax fails explicitly.

This runtime does not wrap Stata or include StataCorp code. Only the
documented subset is implemented. Numerical work uses NumPy, SciPy and
statsmodels.
"""
from __future__ import annotations

import copy
import fnmatch
import json
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .expression import Evaluator, ExpressionError, NotFoundError, truth
from .panel import PanelCommands
from .formats import format_value
from .macros import MacroError, apply_macro_function, expand_macros
from .mata import Mata, MataError
from .packages import PackageError, PackageManager
from .parser import Statement, lex, split_outside, tokens, unquote
from .statistics import StatisticalCommands
from .graphics import GraphicsCommands
from .data_commands import IOCommands
from .generation import GenerationCommands
from .metadata import MetadataCommands
from .program_support import ProgramSupportCommands
from .syntax import SyntaxCommand, SyntaxError_


class StataError(Exception):
    def __init__(self, message, rc=198, line=None):
        super().__init__(message)
        self.rc, self.line = rc, line
        #: Set when the message has already been written to the transcript.
        self.reported = False


class _ExitFlow(Exception):
    def __init__(self, code=0):
        self.code = code


@dataclass
class RunResult:
    output: str
    rc: int = 0
    line: int | None = None


ALIASES = {
    "gen": "generate", "g": "generate", "egen": "egen", "repl": "replace",
    "su": "summarize", "sum": "summarize", "summ": "summarize",
    "reg": "regress", "di": "display", "dis": "display", "disp": "display",
    "qui": "quietly", "cap": "capture", "noi": "noisily", "des": "describe",
    "desc": "describe", "l": "list", "cou": "count", "tab": "tabulate",
    "corr": "correlate", "ren": "rename", "mat": "matrix", "loc": "local",
    "glo": "global", "prog": "program", "ret": "return", "eret": "ereturn",
    "lab": "label", "ds": "describe", "bys": "bysort", "bysor": "bysort",
    "replace": "replace", "egen": "egen", "marksam": "marksample",
    "markout": "markout", "gettok": "gettoken", "ereturn": "ereturn",
    "scalar": "scalar", "matrix": "matrix", "file": "file", "log": "log",
    "ml": "ml", "svy": "svy", "xi": "xi", "mata": "mata", "temp": "tempfile",
    "tl": "twoway", "tw": "twoway", "gr": "graph", "reghdfe": "regress",
    "marksample": "marksample", "mark": "mark", "tempvar": "tempvar",
    "tempname": "tempname", "tempfile": "tempfile", "args": "args",
    "gettoken": "gettoken", "syntax": "syntax", "ereturn": "ereturn",
}

# Options that may follow ``program name``.  Real ado files use all of these.
PROGRAM_OPTIONS = {
    "rclass", "eclass", "sclass", "sortpreserve", "bysable", "byable",
    "allowable", "nobreak", "range", "recast", "noprogram", "maybeprefix",
    "prefix", "add", "quiet",
}


class Engine(IOCommands, MetadataCommands, StatisticalCommands, GraphicsCommands,
            GenerationCommands, ProgramSupportCommands, PanelCommands):
    def __init__(self, cwd: Path | str | None = None, ado_dir: Path | str | None = None):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.data = pd.DataFrame()
        self.variable_labels = {}
        self.value_labels = {}
        self.value_label_names = {}
        self.formats = {}
        self.dataset_label = ""
        self.notes = {}
        self.locals = {}
        self.panel_id = None
        self.panel_time = None
        self.globals = {}
        self.scalars = {}
        self.matrices = {}
        self.returns = {}
        self.estimates = {}
        self.coefficients = {}
        self.programs = {}
        self.program_options = {}
        self.stored_estimates = {}
        self.graphs = []
        self._figure = None
        self._fit = None
        self._model_columns = []
        self._preserved = None
        self._cancel = threading.Event()
        self._run_lock = threading.Lock()
        self._depth = 0
        self._steps = 0
        self._output = []
        self._quiet = 0
        self._log = None
        self.rc = 0
        self.rng = np.random.default_rng()
        self.packages = PackageManager(Path(ado_dir) if ado_dir else None)
        self.mata = Mata(self)
        self.evaluator = Evaluator(self)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------
    def cancel(self):
        self._cancel.set()

    def emit(self, text):
        if not self._quiet:
            self._output.append(str(text))
            if self._log:
                self._log.write(str(text) + "\n")
                self._log.flush()

    def run(self, script: str) -> RunResult:
        if not self._run_lock.acquire(blocking=False):
            return RunResult("engine busy", 1)
        try:
            self._cancel.clear()
            self._output = []
            self._steps = 0
            try:
                statements = lex(script)
                self._execute(statements)
                self.rc = 0
                return RunResult("\n".join(self._output), 0)
            except _ExitFlow as flow:
                self.rc = flow.code
                return RunResult("\n".join(self._output), flow.code)
            except (StataError, MacroError, SyntaxError_, ValueError, OSError,
                    KeyError, TypeError, IndexError) as error:
                rc = getattr(error, "rc", 198)
                self.rc = rc
                line = getattr(error, "line", None)
                self._quiet = 0
                if not getattr(error, "reported", False):
                    self.emit(f"{error}\nr({rc});" + (f" [line {line}]" if line else ""))
                else:
                    self.emit(f"r({rc});" + (f" [line {line}]" if line else ""))
                return RunResult("\n".join(self._output), rc, line)
        finally:
            self._run_lock.release()

    def run_file(self, path) -> RunResult:
        path = self.path(str(path))
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as error:
            return RunResult(str(error), 601)
        old = self.locals
        self.locals = {}
        try:
            return self.run(source)
        finally:
            self.locals = old

    def path(self, value, extension=None):
        path = Path(unquote(value))
        if extension and not path.suffix:
            path = path.with_suffix(extension)
        return path if path.is_absolute() else self.cwd / path

    def expand(self, text):
        return expand_macros(text, self.locals, self.globals)

    def evaluate(self, text):
        try:
            value = self.evaluator.evaluate(text)
            if isinstance(value, np.ndarray) and value.ndim == 0:
                value = value.item()
            if isinstance(value, (float, np.floating)) and not np.isfinite(value):
                return np.nan
            if isinstance(value, (pd.Series, np.ndarray)) and pd.api.types.is_numeric_dtype(value.dtype):
                return np.where(np.isfinite(value), value, np.nan)
            return value
        except NotFoundError as ex:
            raise StataError(str(ex), 111) from ex
        except (ExpressionError, KeyError) as ex:
            raise StataError(str(ex), 198) from ex

    def _tick(self):
        self._steps += 1
        if self._cancel.is_set():
            raise StataError("execution interrupted", 1)
        if self._steps > 100000:
            raise StataError("execution step limit (100000) exceeded", 901)

    def _condition(self, expression):
        value = truth(self.evaluate(expression))
        if np.isscalar(value):
            return bool(value)
        array = np.asarray(value)
        if not array.size:
            raise StataError("no observations", 2000)
        return bool(array.flat[0])

    # ------------------------------------------------------------------
    # Statement execution
    # ------------------------------------------------------------------
    def _execute(self, statements):
        self._depth += 1
        if self._depth > 60:
            self._depth -= 1
            raise StataError("execution nesting limit", 901)
        index = 0
        try:
            while index < len(statements):
                statement = statements[index]
                try:
                    self._tick()
                    raw = statement.text.strip()
                    text = self.expand(raw).strip()
                    if not text:
                        index += 1
                        continue
                    command = text.split()[0].lower()
                    if command.startswith("!"):
                        # Stata's shell escape: the rest of the line is
                        # handed to the operating system.
                        self._shell_escape(text[1:].strip())
                        index += 1
                        continue
                    command = ALIASES.get(command, command)
                    if command in {"forvalues", "foreach", "while", "if"} and text.endswith("{"):
                        index = self._block(statements, index, command)
                        continue
                    if command == "if" and not text.endswith("{"):
                        # Stata's single-line form: ``if exp command``.  A
                        # following ``else`` on its own line continues the
                        # same conditional.
                        taken = self._inline_if(text)
                        index += 1
                        if index < len(statements):
                            following = statements[index].text.strip()
                            if following.startswith("else"):
                                index = self._inline_else(statements, index, taken)
                                continue
                        continue
                    if command == "else":
                        index = self._block(statements, index, "else")
                        continue
                    if command == "program" and re.match(r"prog(?:ram)?\s+(?:define\s+)?", text) \
                            and not text.startswith("program drop"):
                        index = self._define_program(statements, index)
                        continue
                    if text in {"mata", "mata:"}:
                        index = self._mata_block(statements, index)
                        continue
                    self.command(text)
                except (StataError, MacroError, SyntaxError_, ValueError, OSError,
                        KeyError, TypeError, IndexError) as ex:
                    if not isinstance(ex, StataError):
                        ex = StataError(str(ex), 198)
                    if ex.line is None:
                        ex.line = statement.line
                    raise ex
                index += 1
        finally:
            self._depth -= 1

    def _inline_if(self, text) -> bool:
        """Run Stata's single-line ``if exp command`` form.

        The condition ends where the command begins.  Stata looks for the
        first token that starts a command, so ``if `x' == 1 local y 2``
        splits into the condition ```x' == 1`` and the command
        ``local y 2``.  Returns whether the condition was true.
        """
        body = text[2:].strip()
        condition, command = self._split_condition(body)
        if not condition:
            raise StataError("if requires a condition")
        if self._condition(self.expand(condition)):
            if command:
                self.command(command)
            return True
        return False

    def _inline_else(self, statements, index, taken) -> int:
        """Run the ``else`` that follows a single-line ``if``."""
        text = statements[index].text.strip()
        if text.startswith("else if"):
            body = text[len("else if"):].strip()
            condition, command = self._split_condition(body)
            if not condition:
                raise StataError("if requires a condition")
            if self._condition(self.expand(condition)):
                if command:
                    self.command(command)
                return index + 1
            return index + 1
        if taken:
            return index + 1
        command = text[len("else"):].strip()
        if command:
            self.command(command)
        return index + 1

    #: Commands that may follow an ``if`` on the same line.
    _IF_COMMANDS = {
        "local", "global", "display", "di", "dis", "disp", "generate", "gen",
        "g", "replace", "repl", "egen", "drop", "keep", "rename", "ren",
        "label", "lab", "scalar", "return", "ereturn", "eret", "matrix",
        "mat", "summarize", "su", "sum", "summ", "count", "cou", "list", "l",
        "tabulate", "tab", "describe", "des", "desc", "correlate", "corr",
        "regress", "reg", "predict", "logit", "probit", "poisson", "use",
        "save", "tempfile", "tempname", "tempvar", "args", "gettoken",
        "marksample", "mark", "markout", "quietly", "capture", "noisily",
        "set", "sort", "gsort", "bysort", "by", "assert", "forvalues",
        "foreach", "while", "program", "exit", "log", "file", "syntax",
        "preserve", "restore", "destring", "tostring", "encode", "decode",
        "recode", "contract", "expand", "collapse", "reshape", "merge",
        "append", "import", "export", "format", "recast", "estimates", "test",
        "graph", "twoway", "histogram", "hist", "scatter", "line", "bar",
        "box", "dot", "kdensity", "mata", "ssc", "net", "which", "adopath",
        "sysdir", "search", "findit", "cd", "pwd", "mkdir", "dir", "ls",
        "confirm", "about", "help", "sleep", "timer", "erase", "type",
        "shell", "more", "macro", "total", "mean", "centile", "tabstat",
        "codebook", "inspect", "mata:", "note", "notes", "dropvars",
    }

    def _split_condition(self, body: str) -> tuple[str, str]:
        """Split ``if`` text into its condition and the command that follows.

        The command begins at the first word that names a command, except
        where that word is part of the expression: ``if tab == 1`` keeps
        ``tab`` as a variable because it is followed by an operator.
        """
        words = self._expression_words(body)
        for position, (word, start, end) in enumerate(words):
            if word.lower() not in self._IF_COMMANDS:
                continue
            following = words[position + 1][0] if position + 1 < len(words) else ""
            if following in {"==", "!=", "<", ">", "<=", ">=", "&", "|", "+", "-",
                             "*", "/", "^", "==="}:
                # The word is part of the expression, not a command.
                continue
            if position == 0 and not body[:start].strip():
                # The condition would be empty; treat the word as part of it.
                continue
            return body[:start].strip(), body[start:].strip()
        return body.strip(), ""

    def _expression_words(self, body: str) -> list[tuple[str, int, int]]:
        """Return the (word, start, end) triples of a command's argument list.

        Quoted strings and bracketed groups are skipped so that their
        contents never look like a command name.
        """
        words: list[tuple[str, int, int]] = []
        depth, quote, compound, index = 0, False, False, 0
        while index < len(body):
            char = body[index]
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
            if body.startswith('`"', index):
                compound = True
                index += 2
                continue
            if char == '"':
                quote = True
                index += 1
                continue
            if char in "([{":
                depth += 1
                index += 1
                continue
            if char in ")]}":
                depth -= 1
                index += 1
                continue
            if depth == 0 and (char.isalpha() or char == "_"):
                match = re.match(r"[A-Za-z_]\w*", body[index:])
                if match:
                    words.append((match[0], index, index + match.end()))
                    index += match.end()
                    continue
            index += 1
        return words

    def _block(self, statements, index, command):
        """Execute a braced block: loops, if/else and program bodies.

        The block header is expanded lazily so that a ``while`` condition
        sees the current value of a loop macro on every iteration.
        """
        depth, cursor = 1, index + 1
        while cursor < len(statements):
            value = statements[cursor].text.strip()
            if value.endswith("{"):
                depth += 1
            elif value == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth:
            raise StataError("unclosed brace")
        body = statements[index + 1:cursor]
        raw_header = statements[index].text.strip()[:-1].strip()
        cursor += 1
        if command == "forvalues":
            self._forvalues(self.expand(raw_header), body)
        elif command == "foreach":
            self._foreach(self.expand(raw_header), body)
        elif command == "while":
            condition = raw_header[5:].strip()
            while self._condition(self.expand(condition)):
                self._tick()
                self._execute(body)
        elif command == "if":
            cursor = self._if_chain(statements, index, cursor, body, raw_header)
        elif command == "else":
            # An ``else`` block is consumed by the ``if`` handler above.
            return cursor
        return cursor

    def _if_chain(self, statements, index, cursor, body, raw_header):
        """Run an ``if`` block and any ``else if``/``else`` alternatives."""
        if self._condition(self.expand(raw_header[2:].strip())):
            self._execute(body)
            return self._skip_alternatives(statements, cursor)
        # The condition failed: look for the next alternative.
        while cursor < len(statements):
            text = statements[cursor].text.strip()
            if not text.startswith("else"):
                break
            if text.startswith("else if"):
                depth, end = 1, cursor + 1
                while end < len(statements):
                    value = statements[end].text.strip()
                    if value.endswith("{"):
                        depth += 1
                    elif value == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    end += 1
                if depth:
                    raise StataError("unclosed brace")
                alternative = statements[cursor + 1:end]
                condition = text[len("else if"):].strip()[:-1].strip()
                if self._condition(self.expand(condition)):
                    self._execute(alternative)
                    return self._skip_alternatives(statements, end + 1)
                cursor = end + 1
                continue
            # A plain ``else`` block.
            if not text.endswith("{"):
                # Stata's single-line form: ``else command``.
                self.command(text[len("else"):].strip())
                return cursor + 1
            depth, end = 1, cursor + 1
            while end < len(statements):
                value = statements[end].text.strip()
                if value.endswith("{"):
                    depth += 1
                elif value == "}":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            if depth:
                raise StataError("unclosed brace")
            self._execute(statements[cursor + 1:end])
            return end + 1
        return cursor

    def _skip_alternatives(self, statements, cursor):
        """Skip ``else``/``else if`` blocks that follow a taken branch."""
        while cursor < len(statements):
            text = statements[cursor].text.strip()
            if not text.startswith("else"):
                break
            if not text.endswith("{"):
                # Stata's single-line form: ``else command``.
                cursor += 1
                continue
            depth, end = 1, cursor + 1
            while end < len(statements):
                value = statements[end].text.strip()
                if value.endswith("{"):
                    depth += 1
                elif value == "}":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            if depth:
                raise StataError("unclosed brace")
            cursor = end + 1
        return cursor

    def _forvalues(self, header, body):
        match = re.fullmatch(
            r"\w+\s+(\w+)\s*=\s*(-?\d+)\s*(?:/\s*(-?\d+)|\(\s*(-?\d+)\s*\)\s*(-?\d+))", header)
        if not match:
            raise StataError("unsupported forvalues range")
        name = match[1]
        start = int(match[2])
        step = int(match[4]) if match[4] else 1
        end = int(match[5] or match[3])
        if step == 0:
            raise StataError("zero loop increment")
        for value in range(start, end + (1 if step > 0 else -1), step):
            self._tick()
            self.locals[name] = str(value)
            self._execute(body)

    def _foreach(self, header, body):
        match = re.fullmatch(
            r"foreach\s+(\w+)\s+(in|of\s+(?:varlist|numlist|local|global|newvarlist))\s+(.*)",
            header, re.S)
        if not match:
            raise StataError("unsupported foreach syntax")
        name, source, values = match[1], match[2], match[3].strip()
        if source == "of local":
            values = str(self.locals.get(values, ""))
        elif source == "of global":
            values = str(self.globals.get(values, ""))
        elif source == "of varlist":
            values = " ".join(self.varlist(values))
        elif source == "of newvarlist":
            values = " ".join(self.varlist(values))
        elif source == "of numlist":
            values = self._numlist(values)
        for value in tokens(values):
            self._tick()
            self.locals[name] = unquote(value)
            self._execute(body)

    def _numlist(self, text: str) -> str:
        text = text.strip()
        match = re.fullmatch(r"(-?\d+)\s*/\s*(-?\d+)", text)
        if match:
            return " ".join(str(value) for value in range(int(match[1]), int(match[2]) + 1))
        match = re.fullmatch(r"(-?\d+)\s*\(\s*(-?\d+)\s*\)\s*(-?\d+)", text)
        if match:
            start, step, end = int(match[1]), int(match[2]), int(match[3])
            if step == 0:
                raise StataError("zero numlist increment")
            return " ".join(str(value) for value in
                            range(start, end + (1 if step > 0 else -1), step))
        return " ".join(tokens(text))

    def _define_program(self, statements, index):
        text = statements[index].text.strip()
        parts = split_outside(text)
        head = tokens(parts[0])
        name = head[2] if len(head) > 2 and head[1] == "define" else head[1]
        option_text = ",".join(parts[1:])
        options = self.options(option_text, PROGRAM_OPTIONS)
        cursor = index + 1
        while cursor < len(statements) and statements[cursor].text.strip() != "end":
            cursor += 1
        if cursor == len(statements):
            raise StataError("program missing end")
        self.programs[name] = statements[index + 1:cursor]
        self.program_options[name] = options
        return cursor + 1

    def _mata_block(self, statements, index):
        cursor = index + 1
        while cursor < len(statements) and statements[cursor].text.strip() != "end":
            cursor += 1
        if cursor == len(statements):
            raise StataError("Mata block missing end")
        try:
            self.mata.run("\n".join(self.expand(x.text) for x in statements[index + 1:cursor]))
        except MataError as ex:
            raise StataError(str(ex), 199) from ex
        return cursor + 1

    # ------------------------------------------------------------------
    # Options and variable lists
    # ------------------------------------------------------------------
    def options(self, text, allowed):
        output = {}
        for match in re.finditer(r"([A-Za-z_]\w*)(?:\(([^()]*)\))?|\S+", text):
            if not match[1]:
                raise StataError(f"invalid option {match[0]}")
            name = match[1].lower()
            if name not in allowed:
                raise StataError(f"option {name} not allowed", 198)
            output[name] = match[2] if match[2] is not None else True
        return output

    def varlist(self, text, default=True):
        if not text.strip():
            return list(self.data.columns) if default else []
        result = []
        for token in text.split():
            if "-" in token and not any(c in token for c in "*?") and not token.startswith("-"):
                first, last = token.split("-", 1)
                columns = list(self.data.columns)
                if first not in columns or last not in columns:
                    raise StataError(f"variable {token} not found", 111)
                names = columns[columns.index(first):columns.index(last) + 1]
            else:
                names = [str(c) for c in self.data if fnmatch.fnmatchcase(str(c), token)]
            if not names:
                raise StataError(f"variable {token} not found", 111)
            for name in names:
                if name not in result:
                    result.append(name)
        return result

    def qualifiers(self, text):
        """Split ``if``/``in`` qualifiers from a command's argument list."""
        depth, quote, compound, index = 0, False, False, 0
        positions = []
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
                for word in (" if ", " in "):
                    if text.startswith(word, index):
                        positions.append((index, word.strip()))
            index += 1
        mask = pd.Series(True, index=self.data.index)
        base = text[:positions[0][0]].strip() if positions else text.strip()
        for k, (position, kind) in enumerate(positions):
            end = positions[k + 1][0] if k + 1 < len(positions) else None
            value = text[position + len(kind) + 2:end if end is not None else None].strip()
            if kind == "if":
                mask &= pd.Series(truth(self.evaluate(value)), index=self.data.index).astype(bool)
            else:
                match = re.fullmatch(r"(-?\d+|f|l)(?:/(-?\d+|f|l))?", value)
                if not match:
                    raise StataError("unsupported in range")
                def position_of(token):
                    if token == "f":
                        return 1
                    if token == "l":
                        return len(self.data)
                    return int(token) if int(token) > 0 else len(self.data) + int(token) + 1
                low, high = position_of(match[1]), position_of(match[2] or match[1])
                rows = np.arange(1, len(self.data) + 1)
                mask &= (rows >= low) & (rows <= high)
        return base, mask

    def _new_name(self, name):
        if not re.fullmatch(r"[A-Za-z_]\w{0,31}", name):
            raise StataError("invalid variable name")
        if name in self.data:
            raise StataError(f"variable {name} already defined", 110)

    # ------------------------------------------------------------------
    # Data I/O primitives
    # ------------------------------------------------------------------
    def _read(self, path):
        import pyreadstat
        data, meta = pyreadstat.read_dta(str(path), apply_value_formats=False,
                                          user_missing=True, disable_datetime_conversion=True)
        if any(meta.missing_user_values.values()):
            raise StataError("extended missing values (.a–.z) are not implemented; dataset not loaded", 199)
        self.variable_labels = {n: v for n, v in zip(meta.column_names, meta.column_labels) if v}
        self.dataset_label = meta.file_label or ""
        self.value_labels = meta.value_labels
        self.value_label_names = meta.variable_to_label
        self.formats = meta.original_variable_types
        return data

    def _write(self, path, version=118):
        value_labels = {n: self.value_labels[label]
                        for n, label in self.value_label_names.items()
                        if n in self.data and label in self.value_labels}
        self.data.to_stata(path, write_index=False, version=version,
                           data_label=self.dataset_label,
                           variable_labels={n: v for n, v in self.variable_labels.items()
                                            if n in self.data},
                           value_labels=value_labels)

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------
    def command(self, text):
        self._tick()
        words = text.split(maxsplit=1)
        command = ALIASES.get(words[0].lower(), words[0].lower())
        arg = words[1].strip() if len(words) > 1 else ""
        if command in {"capture", "quietly", "noisily"}:
            if not arg:
                raise StataError("prefix requires command")
            old_quiet = self._quiet
            if command in {"capture", "quietly"}:
                self._quiet += 1
            if command == "noisily":
                self._quiet = 0
            try:
                self.command(arg)
                self.rc = 0
            except (StataError, MacroError, SyntaxError_, ValueError, OSError,
                    KeyError, TypeError, IndexError) as ex:
                if command != "capture":
                    raise
                self.rc = getattr(ex, "rc", 198)
            finally:
                self._quiet = old_quiet
            return
        previous_rc = self.rc
        if command not in {"local", "global", "display", "scalar", "return", "ereturn"}:
            self.rc = 0
        if command in {"by", "bysort"}:
            self._by(arg, sort=command == "bysort")
            return
        if command == "clear":
            if arg not in {"", "all"}:
                raise StataError("unsupported clear target")
            self.data = pd.DataFrame()
            self.variable_labels = {}
            self.value_labels = {}
            self.value_label_names = {}
            self.formats = {}
            self.dataset_label = ""
            if arg == "all":
                self.scalars.clear()
                self.matrices.clear()
                self.programs.clear()
                self.program_options.clear()
                self.estimates.clear()
                self.returns.clear()
                self.mata.variables.clear()
                self.coefficients.clear()
                self.stored_estimates.clear()
                self._fit = None
                self._model_columns = []
            return
        if command == "set":
            self._set(arg)
            return
        if command == "version":
            match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*:\s*(.*))?", arg)
            if not match or float(match[1]) > 19.5:
                raise StataError("unsupported version request", 9)
            if match[2]:
                self.command(match[2])
            return
        if command in {"local", "global"}:
            self._macro(command, arg)
            return
        if command in {"tempfile", "tempname", "tempvar"}:
            self._temp_names(command, arg)
            return
        if command in {"marksample", "mark"}:
            self._marksample(arg)
            return
        if command == "markout":
            self._markout(arg)
            return
        if command == "args":
            for index, name in enumerate(arg.split(), 1):
                self.locals[name] = self.locals.get(str(index), "")
            return
        if command == "macro":
            self._macro_command(arg)
            return
        if command == "gettoken":
            self._gettoken(arg)
            return
        if command == "syntax":
            self._syntax_command(arg)
            return
        if command in {"generate", "replace", "egen"}:
            self._generate(command, arg)
            return
        if command in {"keep", "drop"}:
            self._keep_drop(command, arg)
            return
        if command == "rename":
            pair = tokens(arg)
            if len(pair) != 2:
                raise StataError("rename requires old and new names")
            self.varlist(pair[0])
            self._new_name(pair[1])
            self.data.rename(columns={pair[0]: pair[1]}, inplace=True)
            for mapping in (self.variable_labels, self.value_label_names, self.formats):
                if pair[0] in mapping:
                    mapping[pair[1]] = mapping.pop(pair[0])
            return
        if command == "order":
            parts = split_outside(arg)
            names = self.varlist(parts[0])
            opt = self.options(",".join(parts[1:]), {"first", "last", "before", "after"})
            remaining = [c for c in self.data if c not in names]
            if "before" in opt or "after" in opt:
                target = opt.get("before", opt.get("after"))
                position = remaining.index(target) + (1 if "after" in opt else 0)
                remaining[position:position] = names
            else:
                remaining = remaining + names if "last" in opt else names + remaining
            self.data = self.data[remaining]
            return
        if command in {"sort", "gsort"}:
            parts = split_outside(arg)
            self.options(",".join(parts[1:]), {"stable"})
            names = parts[0].split()
            ascending = [not x.startswith("-") for x in names]
            names = [x.lstrip("+-") for x in names]
            self.varlist(" ".join(names))
            self.data = self.data.sort_values(names, ascending=ascending, kind="stable",
                                               na_position="last").reset_index(drop=True)
            return
        if command in {"summarize", "count", "describe", "list", "tabulate", "correlate",
                       "codebook", "inspect", "tabstat", "mean", "total", "centile"}:
            self._describe(command, arg)
            return
        if command == "xtset":
            self._xtset(arg)
            return
        if command in {"xtsum", "xtdescribe"}:
            self._xtsum(arg)
            return
        if command == "display":
            self._display(arg)
            self.rc = 0
            return
        if command == "assert":
            base, mask = self.qualifiers(arg)
            value = pd.Series(truth(self.evaluate(base)), index=self.data.index)
            if not value.loc[mask].all():
                raise StataError("assertion is false", 9)
            return
        if command in {"use", "save", "saveold", "import", "export", "append", "merge",
                       "reshape", "collapse", "expand", "contract", "recode", "egen"}:
            self._data_command(command, arg)
            return
        if command == "preserve":
            if arg:
                raise StataError("preserve takes no arguments")
            if self._preserved is not None:
                raise StataError("already preserved", 621)
            self._preserved = copy.deepcopy((self.data, self.variable_labels, self.value_labels,
                                             self.value_label_names, self.formats, self.dataset_label))
            return
        if command == "restore":
            opt = self.options(arg.lstrip(", "), {"not", "preserve"})
            if self._preserved is None:
                raise StataError("nothing to restore", 622)
            if "not" not in opt:
                (self.data, self.variable_labels, self.value_labels, self.value_label_names,
                 self.formats, self.dataset_label) = copy.deepcopy(self._preserved)
            if "preserve" not in opt:
                self._preserved = None
            return
        if command in {"regress", "logit", "logistic", "probit", "poisson", "predict", "test",
                       "estimates", "ttest", "margins"}:
            self._statistics(command, arg)
            return
        if command in {"label", "format", "destring", "tostring", "encode", "decode", "recast"}:
            self._metadata(command, arg)
            return
        if command == "scalar":
            self._scalar(arg)
            return
        if command == "matrix":
            self._matrix(arg)
            return
        if command == "return":
            self._return(arg)
            return
        if command == "ereturn":
            self._ereturn(arg)
            return
        if command in {"do", "run", "include"}:
            self._do(command, arg)
            return
        if command == "program":
            self._program_command(arg)
            return
        if command == "mata:":
            try:
                self.mata.run(arg)
            except MataError as ex:
                raise StataError(str(ex), 199) from ex
            return
        if command in {"scatter", "line", "histogram", "hist", "twoway", "graph", "kdensity",
                       "bar", "box", "dot"}:
            self._graph(command, arg)
            return
        if command in {"ssc", "net", "which", "adopath", "sysdir", "search", "findit"}:
            self._package(command, arg)
            return
        if command in {"cd", "pwd", "mkdir", "dir", "ls", "confirm", "log", "about", "help",
                       "exit", "sleep", "timer", "erase", "type", "file", "shell", "more"}:
            self._system(command, arg)
            return
        if command in self.programs:
            self._call_program(command, arg)
            return
        ado = self.packages.find(command)
        if ado:
            self._execute(lex(ado.read_text(encoding="utf-8-sig")))
            if command in self.programs:
                self._call_program(command, arg)
                return
            raise StataError(f"{command}.ado did not define {command}", 199)
        raise StataError(f"command {command} is not implemented in OpenStat", 199)

    def _shell_escape(self, command: str) -> None:
        """Run a command in the operating system, as Stata's ``!`` does.

        Stata waits for the command and reports a non-zero exit status as
        an error; the command's own output goes to the operating system's
        standard streams.
        """
        if not command:
            return
        try:
            completed = subprocess.run(command, shell=True, cwd=self.cwd,
                                       capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as error:
            raise StataError(f"shell command failed: {error}", 199) from error
        if completed.stdout:
            self.emit(completed.stdout.rstrip("\n"))
        if completed.stderr:
            self.emit(completed.stderr.rstrip("\n"))
        if completed.returncode:
            raise StataError(f"shell command returned {completed.returncode}", 199)

    # ------------------------------------------------------------------
    # Macros
    # ------------------------------------------------------------------
    def _macro(self, command, arg):
        match = re.fullmatch(r"(\w+)\s*(.*)", arg, re.S)
        if not match:
            raise StataError("macro name required")
        name, value = match[1], match[2].strip()
        store = self.locals if command == "local" else self.globals
        if value.startswith(":"):
            value = self._extended_macro(value[1:].strip())
        elif value.startswith("="):
            evaluated = self.evaluate(value[1:])
            value = self._format(evaluated)
        else:
            value = unquote(value)
        store[name] = value
        self.rc = 0

    def _extended_macro(self, text):
        """Evaluate ``: word count ...`` style extended macro functions."""
        text = text.strip()
        match = re.match(r"([A-Za-z_]\w*)\s*(.*)", text, re.S)
        if not match:
            raise StataError("extended macro function required", 199)
        name, rest = match[1].lower(), match[2].strip()
        arguments = self._macro_arguments(rest)
        try:
            return apply_macro_function(name, arguments)
        except MacroError as ex:
            raise StataError(str(ex), 199) from ex

    def _macro_arguments(self, text):
        """Split the arguments of an extended macro function."""
        arguments, current, depth, quote, compound = [], "", 0, False, False
        index = 0
        while index < len(text):
            char = text[index]
            if compound:
                current += char
                if char == '"' and not quote:
                    compound = False
                index += 1
                continue
            if quote:
                current += char
                if char == '"':
                    quote = False
                index += 1
                continue
            if text.startswith('`"', index):
                compound = True
                current += '`"'
                index += 2
                continue
            if char == '"':
                quote = True
                current += char
                index += 1
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            if depth == 0 and char == ",":
                arguments.append(current.strip())
                current = ""
                index += 1
                continue
            current += char
            index += 1
        if current.strip():
            arguments.append(current.strip())
        return [unquote(value) for value in arguments]

    def _macro_command(self, arg):
        if arg == "list":
            self.emit(str({"local": self.locals, "global": self.globals}))
            return
        if arg.startswith("drop "):
            for name in arg[5:].split():
                if name == "_all":
                    self.globals.clear()
                    self.locals.clear()
                else:
                    self.globals.pop(name, None)
                    self.locals.pop(name, None)
            return
        raise StataError("unsupported macro subcommand", 199)

    def _gettoken(self, arg):
        """Implement ``gettoken``."""
        match = re.fullmatch(r"(\w+)\s+(\w+)\s*:\s*(.*?)(?:,\s*(.*))?$", arg, re.S)
        if not match:
            raise StataError("gettoken syntax: gettoken name1 name2 : string [, options]")
        first, second, source, options = match[1], match[2], match[3].strip(), (match[4] or "").strip()
        parse_chars = ""
        if options:
            option_match = re.search(r'parse\s*\(\s*"([^"]*)"\s*\)', options)
            if option_match:
                parse_chars = option_match[1]
            if re.search(r"\bmatch\s*\(", options) and not parse_chars:
                raise StataError("gettoken match() is not implemented", 199)
        if source == "0":
            source = self.locals.get("0", "")
        source = self.expand(source)
        if parse_chars:
            for char in parse_chars:
                source = source.replace(char, " ")
            parts = source.split()
            first_value = parts[0] if parts else ""
            rest_value = " ".join(parts[1:])
        else:
            parts = source.split(None, 1)
            first_value = parts[0] if parts else ""
            rest_value = parts[1] if len(parts) > 1 else ""
        self.locals[first] = first_value
        self.locals[second] = rest_value.strip()

    def _syntax_command(self, arg):
        """Implement ``syntax`` inside a program."""
        if not self.programs:
            raise StataError("syntax may only be used within a program", 198)
        spec = arg.strip()
        if not spec:
            raise StataError("syntax requires a specification", 198)
        try:
            results = SyntaxCommand(self, spec).run(self.locals.get("0", ""))
        except SyntaxError_ as ex:
            raise StataError(str(ex), 197) from ex
        for name, value in results.items():
            self.locals[name] = value
        self.locals["_syntax_spec"] = spec
        self._syntax_varlist = results.get("varlist", "")
        self._syntax_if = results.get("if", "")
        self._syntax_in = results.get("in", "")

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def _scalar(self, arg):
        if arg.startswith("list"):
            self.emit(str(self.scalars))
            return
        if arg.startswith("drop "):
            for name in arg[5:].split():
                self.scalars.pop(name, None)
            return
        match = re.fullmatch(r"(\w+)\s*=\s*(.*)", arg)
        if not match:
            raise StataError("scalar assignment required")
        self.scalars[match[1]] = self.evaluate(match[2])
        self.rc = 0

    def _return(self, arg):
        if arg == "list":
            self.emit(str(self.returns))
            return
        if arg == "clear":
            self.returns.clear()
            return
        match = re.fullmatch(r"(scalar|local)\s+(\w+)\s*(?:=\s*)?(.*)", arg)
        if not match:
            raise StataError("unsupported return syntax")
        if match[1] == "scalar":
            self.returns[match[2]] = self.evaluate(match[3])
        else:
            self.returns[match[2]] = unquote(match[3])
        return

    def _ereturn(self, arg):
        if arg == "list":
            self.emit(str(self.estimates))
            return
        match = re.fullmatch(r"(scalar|local)\s+(\w+)\s*(?:=\s*)?(.*)", arg)
        if not match:
            raise StataError("unsupported ereturn syntax")
        if match[1] == "scalar":
            self.estimates[match[2]] = self.evaluate(match[3])
        else:
            self.estimates[match[2]] = unquote(match[3])
        return

    # ------------------------------------------------------------------
    # Programs
    # ------------------------------------------------------------------
    def _program_command(self, arg):
        if arg.startswith("drop "):
            for name in arg[5:].split():
                if name == "_all":
                    self.programs.clear()
                    self.program_options.clear()
                elif name in self.programs:
                    self.programs.pop(name)
                    self.program_options.pop(name, None)
                else:
                    raise StataError(f"program {name} not found", 111)
            return
        if arg == "list":
            self.emit("\n".join(self.programs))
            return
        raise StataError("program definition requires do-file block")

    def _call_program(self, name, arg):
        old = self.locals
        arguments = tokens(arg)
        self.locals = {str(i): unquote(value) for i, value in enumerate(arguments, 1)}
        self.locals["0"] = arg
        try:
            self._execute(self.programs[name])
        except _ExitFlow as flow:
            if flow.code:
                raise StataError("program exited", flow.code)
        finally:
            self.locals = old

    def _do(self, command, arg):
        arguments = tokens(arg)
        if not arguments:
            raise StataError("do-file required")
        path = self.path(arguments[0], ".do")
        old = self.locals
        if command != "include":
            self.locals = {str(i): unquote(value) for i, value in enumerate(arguments[1:], 1)}
        try:
            self._execute(lex(path.read_text(encoding="utf-8-sig")))
        except _ExitFlow as flow:
            if flow.code:
                raise StataError("do-file exited", flow.code)
        finally:
            self.locals = old

    # ------------------------------------------------------------------
    # Settings, system and packages
    # ------------------------------------------------------------------
    def _set(self, arg):
        parts = arg.split(maxsplit=1)
        if len(parts) != 2:
            raise StataError("set requires name and value")
        name, value = parts
        if name == "obs":
            count = int(self.evaluate(value))
            if count < len(self.data):
                raise StataError("observation number out of range", 2001)
            self.data = self.data.reindex(range(count))
            return
        if name == "seed":
            self.rng = np.random.default_rng(int(value))
            return
        if name in {"more", "varabbrev", "compress", "sort", "trace", "quiet"}:
            if name == "varabbrev" and value not in {"off", "on"}:
                raise StataError("invalid varabbrev")
            return
        if name in {"linesize", "rmsg", "width", "length", "numlist", "processes"}:
            if name in {"linesize", "width", "length", "numlist", "processes"} and not value.isdigit():
                raise StataError(f"invalid {name}")
            if name == "rmsg" and value not in {"on", "off"}:
                raise StataError("invalid rmsg")
            return
        if name == "memory":
            return
        raise StataError(f"set {name} {value} not implemented", 199)

    @staticmethod
    def _format(value, fmt=None):
        if isinstance(value, str):
            return value
        if isinstance(value, (pd.Series, np.ndarray)):
            # Stata's display shows the first element of a series.
            if len(value) == 0:
                return "."
            value = value.iloc[0] if isinstance(value, pd.Series) else value[0]
        if fmt:
            return format_value(value, fmt)
        from .formats import format_for_display
        return format_for_display(value)

    #: Display directives that Stata accepts but OpenStat renders plainly.
    _DISPLAY_STYLES = {"text", "txt", "result", "res", "error", "err", "input", "inp"}

    def _display(self, arg):
        """Implement ``display`` with Stata's directive grammar.

        A display command is a sequence of directives: strings, formatted
        expressions and layout directives such as ``_col()`` and
        ``_newline()``.  The ``as`` and ``in`` modifiers change the style of
        the directives that follow them.
        """
        parts: list[str] = []
        pending = arg.strip()
        fmt = None
        while pending:
            directive, pending = self._display_directive(pending)
            if directive is None:
                break
            kind, value = directive
            if kind == "string":
                parts.append(value)
            elif kind == "value":
                # Stata evaluates each directive as it reaches it, so a
                # failing expression leaves the earlier output in place and
                # appends the error text to it.
                try:
                    parts.append(self._format(self.evaluate(value), fmt))
                except StataError as error:
                    self.emit("".join(parts) + str(error))
                    error.reported = True
                    raise
                except NotFoundError as error:
                    self.emit("".join(parts) + str(error))
                    failure = StataError(str(error), 111)
                    failure.reported = True
                    raise failure from error
                except ExpressionError as error:
                    self.emit("".join(parts) + str(error))
                    failure = StataError(str(error), 198)
                    failure.reported = True
                    raise failure from error
            elif kind == "format":
                fmt = value
            elif kind == "newline":
                parts.append("\n" * int(value))
            elif kind == "column":
                parts.append(" " * int(value))
            elif kind == "skip":
                parts.append(" " * int(value))
            elif kind == "tab":
                parts.append("\t")
            elif kind == "char":
                parts.append(chr(int(value)))
            elif kind == "continue":
                pass
        self.emit("".join(parts))

    def _split_expression(self, pending: str) -> tuple[str, str]:
        """Return the expression that begins a display directive.

        The expression runs until the next display directive, which starts
        with a double quote, a percent sign or an underscore.  The second
        element of the result is the text that follows it.
        """
        depth, quote, compound, index = 0, False, False, 0
        while index < len(pending):
            char = pending[index]
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
            if char == '"':
                if depth == 0:
                    # A quote at the top level ends the expression; it
                    # starts the next directive.
                    break
                quote = True
                index += 1
                continue
            if pending.startswith('`"', index):
                compound = True
                index += 2
                continue
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif depth == 0 and char == "%":
                break
            elif depth == 0 and char == "_" and re.match(r"_(?:newline|column|col|skip|char|continue|tab)\b",
                                                        pending[index:]):
                break
            index += 1
        return pending[:index].strip(), pending[index:].lstrip()

    def _display_directive(self, pending: str):
        """Split off one display directive, returning (kind, value, rest)."""
        # Style modifiers apply to the directives that follow them.
        match = re.match(r"as\s+(\w+)\s+", pending)
        if match:
            if match[1].lower() not in self._DISPLAY_STYLES:
                raise StataError(f"unknown display style {match[1]}")
            return self._display_directive(pending[match.end():])
        match = re.match(r"in\s+(smcl|_asis)\s+", pending)
        if match:
            return self._display_directive(pending[match.end():])
        match = re.match(r"in\s+(text|txt|result|res|error|err|input|inp)\s+", pending)
        if match:
            return self._display_directive(pending[match.end():])
        if pending.startswith('"'):
            match = re.match(r'"([^"\n]*)"', pending)
            if not match:
                raise StataError("unclosed string")
            return ("string", match[1]), pending[match.end():].strip()
        if pending.startswith('`"'):
            match = re.match(r'`"([^"]*)"' + "'", pending)
            if not match:
                raise StataError("unclosed compound string")
            return ("string", match[1]), pending[match.end():].strip()
        if pending.startswith("%"):
            match = re.match(r"%[-+]?\d*(?:\.\d+)?[gfe](c?)|%[-+]?\d*(?:\.\d+)?s|%"
                             r"t[cdwmqhyb]|%tc.*|%td.*", pending)
            if not match:
                raise StataError("unsupported display format")
            return ("format", match[0]), pending[match.end():].strip()
        for pattern, kind, group in (
            (r"_newline\((\d*)\)", "newline", 1),
            (r"_newline\b", "newline", 0),
            (r"_column\((\d+)\)", "column", 1),
            (r"_col\((\d+)\)", "column", 1),
            (r"_skip\((\d+)\)", "skip", 1),
            (r"_char\((\d+)\)", "char", 1),
            (r"_continue\b", "continue", 0),
            (r"_tab\b", "tab", 0),
        ):
            match = re.match(pattern, pending)
            if match:
                value = match[group] if group else "1"
                return (kind, value), pending[match.end():].strip()
        if pending.startswith("_n"):
            return ("newline", "1"), pending[2:].strip()
        # The rest is an expression, which ends where the next directive
        # begins.
        expression, remainder = self._split_expression(pending)
        if not expression:
            return None, ""
        return ("value", expression), remainder

    def _keep_drop(self, command, arg):
        if arg == "_all":
            if command == "drop":
                self.data = self.data.iloc[0:0].copy()
            return
        base, mask = self.qualifiers(" " + arg if arg.startswith(("if ", "in ")) else arg)
        if not base:
            self.data = self.data.loc[mask if command == "keep" else ~mask].reset_index(drop=True)
        else:
            variables = self.varlist(base)
            self.data = self.data[variables] if command == "keep" else self.data.drop(columns=variables)
        return

    def _by(self, arg, sort=False):
        parts = split_outside(arg, ":")
        if len(parts) != 2:
            raise StataError("by requires varlist: command")
        group_spec, body = parts
        match = re.fullmatch(r"([^()]+)(?:\(([^()]+)\))?", group_spec)
        if not match:
            raise StataError("invalid by variables")
        names = self.varlist(match[1])
        sort_names = names + self.varlist(match[2] or "", default=False)
        allowed = {"egen", "generate", "replace", "summarize", "count", "assert", "tabulate",
                   "list", "total", "mean"}
        body_command = ALIASES.get(body.split()[0], body.split()[0])
        if body_command not in allowed:
            raise StataError(f"by prefix not implemented for {body_command}", 199)
        original = self.data
        sorted_data = original.sort_values(sort_names, kind="stable", na_position="last").reset_index(drop=True)
        if not sort and not original[sort_names].reset_index(drop=True).equals(sorted_data[sort_names]):
            raise StataError("not sorted", 5)
        pieces = []
        try:
            for _, frame in sorted_data.groupby(names, dropna=False, sort=False):
                self._tick()
                self.data = frame.reset_index(drop=True).copy()
                self.command(body)
                pieces.append(self.data)
            self.data = pd.concat(pieces, ignore_index=True) if pieces else sorted_data
        except Exception:
            self.data = original
            raise

    # ------------------------------------------------------------------
    # Descriptive statistics
    # ------------------------------------------------------------------
    def _describe(self, command, arg):
        allowed = {"detail", "meanonly", "separator", "varlist", "fweights", "iweights",
                   "aweights", "pweights"} if command in {"summarize", "tabstat", "mean", "total"} \
            else {"simple", "short", "full", "varlist", "number", "fweights"} \
            if command == "describe" \
            else {"noobs", "clean", "nolabel", "separator", "noheader", "absvar"} \
            if command == "list" \
            else {"missing", "nolabel", "row", "column", "nolevens", "noquantiles", "chi2"} \
            if command == "tabulate" \
            else {"print", "nodots", "compact", "nogap", "nototal"} if command == "codebook" else set()
        parts = split_outside(arg)
        opt = self.options(",".join(parts[1:]), allowed)
        base, mask = self.qualifiers(" " + parts[0] if parts[0].startswith(("if ", "in ")) else parts[0])
        if command == "count":
            if base:
                raise StataError("count takes only if/in qualifiers")
            self.returns = {"N": int(mask.sum())}
            self.emit(str(self.returns["N"]))
            return
        names = self.varlist(base)
        frame = self.data.loc[mask, names]
        if command == "summarize":
            self._summarize(frame, names, opt)
            return
        if command in {"describe", "codebook", "inspect"}:
            self.returns = {"N": len(self.data), "k": len(self.data.columns)}
            self.emit(f"Observations: {len(self.data):,}\nVariables: {len(self.data.columns):,}\n{self.dataset_label}")
            for name in names:
                self.emit(f"{name:<24} {str(self.data[name].dtype):<12} {self.variable_labels.get(name, '')}")
            return
        if command == "list":
            if len(frame) > 1000:
                frame = frame.head(1000)
                self.emit("Showing first 1000 observations.")
            self.emit(frame.to_string(index="noobs" not in opt))
            return
        if command == "tabulate":
            self._tabulate(frame, names, opt)
            return
        if command == "correlate":
            numeric = frame.select_dtypes(include="number").dropna()
            if len(numeric.columns) != len(names):
                raise StataError("numeric variables required", 109)
            result = numeric.corr()
            self.matrices["r(C)"] = result.to_numpy()
            self.returns = {"N": len(numeric)}
            if len(names) == 2:
                self.returns["rho"] = result.iloc[0, 1]
            self.emit(result.to_string())
            return
        if command in {"tabstat", "mean", "total", "centile"}:
            self._tabstat(frame, names, opt, command)
            return

    def _summarize(self, frame, names, opt):
        self.returns = {"N": 0}
        self.emit("Variable            Obs          Mean       Std. dev.           Min           Max")
        for name in names:
            if not pd.api.types.is_numeric_dtype(frame[name]):
                continue
            values = frame[name].dropna().astype(float)
            self.returns = {
                "N": len(values), "sum_w": len(values), "mean": values.mean(),
                "Var": values.var(ddof=1), "sd": values.std(ddof=1),
                "min": values.min(), "max": values.max(), "sum": values.sum(),
            }
            if "detail" in opt:
                for p in (1, 5, 10, 25, 50, 75, 90, 95, 99):
                    n = len(values)
                    ordered = np.sort(values.to_numpy())
                    quantile = n * p / 100
                    if n == 0:
                        self.returns[f"p{p}"] = np.nan
                    elif quantile.is_integer() and 0 < quantile < n:
                        self.returns[f"p{p}"] = (ordered[int(quantile) - 1] + ordered[int(quantile)]) / 2
                    else:
                        self.returns[f"p{p}"] = ordered[min(int(quantile), n - 1)]
                from scipy.stats import skew, kurtosis
                self.returns["skewness"] = skew(values, bias=True)
                self.returns["kurtosis"] = kurtosis(values, bias=True, fisher=False)
            r = self.returns
            self.emit(f"{name:<18} {r['N']:>6} {r['mean']:>13.7g} {r['sd']:>13.7g} "
                      f"{r['min']:>13.7g} {r['max']:>13.7g}")

    def _tabulate(self, frame, names, opt):
        if "missing" not in opt:
            frame = frame.replace("", np.nan)
        if len(names) == 1:
            counts = frame[names[0]].value_counts(dropna="missing" not in opt, sort=False).sort_index()
            self.emit(counts.to_string())
            self.returns = {"N": int(counts.sum()), "r": len(counts)}
        elif len(names) == 2:
            table = pd.crosstab(frame[names[0]], frame[names[1]], dropna="missing" not in opt)
            self.emit(table.to_string())
            self.returns = {"N": int(table.to_numpy().sum()), "r": len(table), "c": len(table.columns)}
        else:
            raise StataError("tabulate supports one or two variables")

    def _tabstat(self, frame, names, opt, command):
        for name in names:
            if not pd.api.types.is_numeric_dtype(frame[name]):
                raise StataError("numeric variables required", 109)
            values = frame[name].dropna().astype(float)
            self.returns = {"N": len(values), "mean": values.mean(), "sd": values.std(ddof=1),
                            "min": values.min(), "max": values.max(), "sum": values.sum()}
            self.emit(f"{name:<18} {self.returns['N']:>6} {self.returns['mean']:>13.7g} "
                      f"{self.returns['sd']:>13.7g} {self.returns['min']:>13.7g} "
                      f"{self.returns['max']:>13.7g}")

    # ------------------------------------------------------------------
    # System commands
    # ------------------------------------------------------------------
    def _system(self, command, arg):
        if command == "cd":
            path = self.path(arg)
            if not path.is_dir():
                raise StataError("directory not found", 170)
            self.cwd = path.resolve()
            self.emit(str(self.cwd))
            return
        if command == "pwd":
            if arg:
                raise StataError("pwd takes no arguments")
            self.emit(str(self.cwd))
            return
        if command == "mkdir":
            self.path(arg).mkdir(parents=True, exist_ok=True)
            return
        if command in {"dir", "ls"}:
            self.emit("\n".join(p.name for p in sorted(self.cwd.glob(unquote(arg) or "*"))))
            return
        if command == "confirm":
            parts = tokens(arg)
            if len(parts) < 2:
                raise StataError("confirm requires type and name")
            if parts[0] == "file":
                if not self.path(parts[1]).is_file():
                    raise StataError("file not found", 601)
            elif parts[0] == "variable":
                self.varlist(" ".join(parts[1:]))
            elif parts[:2] == ["new", "variable"]:
                for name in parts[2:]:
                    self._new_name(name)
            elif parts[0] in {"numeric", "string"} and parts[1] == "variable":
                for name in self.varlist(" ".join(parts[2:])):
                    numeric = pd.api.types.is_numeric_dtype(self.data[name])
                    if numeric != (parts[0] == "numeric"):
                        raise StataError("type mismatch", 109)
            else:
                raise StataError("confirm target not implemented", 199)
            return
        if command == "log":
            self._log_command(arg)
            return
        if command == "about":
            self.emit("OpenStat 0.1.0 | MIT | Independent experimental runtime\n"
                      "Stata-style subset; arbitrary do-file and Mata compatibility not established.")
            return
        if command == "help":
            self.emit("OpenStat implements a documented subset of do-file syntax.\n"
                      "See docs/compatibility.md for commands, limits and verification.\n"
                      "Unsupported commands/options stop with an explicit return code.")
            if arg:
                self.emit(f"Requested command: {arg}")
            return
        if command == "exit":
            raise _ExitFlow(int(arg or "0"))
        if command == "erase":
            path = self.path(arg)
            if not path.exists():
                raise StataError("file not found", 601)
            path.unlink()
            return
        if command == "type":
            path = self.path(arg)
            if not path.is_file():
                raise StataError("file not found", 601)
            self.emit(path.read_text(encoding="utf-8", errors="replace").rstrip("\n"))
            return
        if command == "sleep":
            return
        return

    def _log_command(self, arg):
        if arg == "close":
            if self._log:
                self._log.close()
                self._log = None
            else:
                raise StataError("no log open", 606)
            return
        parts = split_outside(arg)
        words = tokens(parts[0])
        opt = self.options(",".join(parts[1:]), {"replace", "append", "text", "smcl"})
        if len(words) != 2 or words[0] != "using":
            raise StataError("only text log using/close implemented", 199)
        if self._log:
            raise StataError("log already open", 604)
        path = self.path(words[1], ".log")
        if path.exists() and not ("replace" in opt or "append" in opt):
            raise StataError("log exists", 602)
        self._log = path.open("a" if "append" in opt else "w", encoding="utf-8")
        return

    # ------------------------------------------------------------------
    # Packages
    # ------------------------------------------------------------------
    def _package(self, command, arg):
        if command == "sysdir":
            words = tokens(arg)
            if len(words) != 3 or words[:2] != ["set", "PLUS"]:
                raise StataError("only sysdir set PLUS is implemented", 199)
            self.packages = PackageManager(self.path(words[2]))
            return
        if command == "adopath":
            if arg:
                raise StataError("adopath edits unsupported; use sysdir set PLUS", 199)
            self.emit(str(self.packages.root))
            return
        if command == "which":
            if " " in arg:
                raise StataError("which requires one command")
            path = self.packages.find(arg)
            if path:
                self.emit(str(path))
                return
            if arg in self.programs or arg in {"regress", "summarize", "generate", "replace",
                                               "use", "save", "ssc", "mata", "graph", "display"}:
                self.emit(f"{arg}: OpenStat built-in subset")
                return
            raise StataError(f"command {arg} not found", 111)
        if command in {"search", "findit"}:
            raise StataError(f"{command} is not implemented", 199)
        parts = split_outside(arg)
        words = tokens(parts[0])
        opt = self.options(",".join(parts[1:]), {"replace", "from", "updating", "all"})
        if command == "ssc" and words and words[0] == "dir":
            for entry in self.packages.installed():
                self.emit(entry["name"] + " (execution compatibility unverified)")
            return
        if len(words) != 2 or words[0] != "install":
            raise StataError(f"{command} supports install only", 199)
        if command == "net" and "from" not in opt:
            raise StataError("net install requires from(URL)")
        try:
            manifest = self.packages.install(words[1],
                                             unquote(opt["from"]) if "from" in opt else None,
                                             "replace" in opt)
        except (PackageError, OSError) as ex:
            raise StataError(f"package install failed: {ex}", 601) from ex
        self.emit(f"installed {manifest['name']}: {len(manifest['files'])} files; "
                  "execution compatibility unverified")
