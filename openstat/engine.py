"""Independent Stata-style interpreter. Unsupported syntax fails explicitly.

This runtime does not wrap Stata or include StataCorp code. Only the
documented subset is implemented. Numerical work uses NumPy, SciPy and
statsmodels.
"""
from __future__ import annotations

import copy
import fnmatch
import json
import math
import os
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .expression import (Evaluator, ExpressionError, NotFoundError, UndefinedCValue,
                         truth)
from .cclass import build_cclass
from .duplicates import DuplicatesCommands
from .errors import StataError
from .extended_data import ExtendedDataCommands
from .output import OutputCommands
from .panel import PanelCommands
from .programming import ProgrammingCommands
from .rng import RandomGenerator
from .formats import format_value, is_text_format
from .macros import MacroError, apply_macro_function, expand_macros
from .mata import Mata, MataError
from .packages import PackageError, PackageManager
from .parser import (Statement, lex, parse_options, quoted_end, split_outside,
                     tokens, unquote)
from .statistics import StatisticalCommands
from .graphics import GraphicsCommands
from .data_commands import IOCommands
from .generation import GenerationCommands
from .metadata import MetadataCommands
from .program_support import ProgramSupportCommands
from .storage import DEFAULT_FORMAT, DEFAULT_TYPE, READSTAT_TYPE
from .syntax import SyntaxCommand, SyntaxError_


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
    "tl": "twoway", "tw": "twoway", "gr": "graph",
    "marksample": "marksample", "mark": "mark", "tempvar": "tempvar",
    "tempname": "tempname", "tempfile": "tempfile", "args": "args",
    "gettoken": "gettoken", "syntax": "syntax", "ereturn": "ereturn",
    "forv": "forvalues", "forva": "forvalues", "forval": "forvalues",
    "forea": "foreach", "foreac": "foreach",
}

# Options that may follow ``program name``.  Real ado files use all of these.
PROGRAM_OPTIONS = {
    "rclass", "eclass", "sclass", "sortpreserve", "bysable", "byable",
    "allowable", "nobreak", "range", "recast", "noprogram", "maybeprefix",
    "prefix", "add", "quiet",
}


class Engine(IOCommands, MetadataCommands, StatisticalCommands, GraphicsCommands,
            GenerationCommands, ProgramSupportCommands, PanelCommands,
            DuplicatesCommands, ProgrammingCommands, ExtendedDataCommands,
            OutputCommands):
    def __init__(self, cwd: Path | str | None = None, ado_dir: Path | str | None = None):
        self.cwd = Path(cwd or Path.cwd()).resolve()
        self.data = pd.DataFrame()
        self.variable_labels = {}
        self.value_labels = {}
        self.value_label_names = {}
        self.formats = {}
        self.storage_types = {}
        self.default_type = DEFAULT_TYPE
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
        self._hidden_variables: set = set()
        self._hidden_markers: list = []
        self._loop_depth = 0
        self._loop_break = False
        self._loop_continue = False
        self._log = None
        self.rc = 0
        self.last_command = ""
        self.last_rc = 0
        self.last_filename = ""
        self.mode = "batch"
        self.rng = RandomGenerator()
        self.cclass = build_cclass(self)
        self.packages = PackageManager(Path(ado_dir) if ado_dir else None)
        self.packages.cwd = self.cwd
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
            source = self._source_text(path)
        except (OSError, UnicodeError) as error:
            return RunResult(str(error), 601)
        old = self.locals
        self.locals = {}
        try:
            return self.run(source)
        finally:
            self.locals = old

    @staticmethod
    def _source_text(path) -> str:
        """Read a do-file, accepting Stata's legacy Windows encoding.

        UTF-8 (with or without BOM) is preferred; files saved by older Stata
        versions on Windows are cp1252, so fall back rather than failing.
        """
        data = Path(path).read_bytes()
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("latin-1")

    def path(self, value, extension=None):
        path = Path(unquote(value))
        if extension and not path.suffix:
            path = path.with_suffix(extension)
        return path if path.is_absolute() else self.cwd / path

    def expand(self, text):
        # `` `: ...' `` is an extended macro function in *macro position*:
        # Stata evaluates it and substitutes the text just like a local.
        text = self._extended_references(text)
        # ``c()`` reports the state of the data as it is *now*, so the table
        # is rebuilt before every expansion: `` `c(k)' `` must see the
        # variables a previous ``generate`` added.
        self.cclass = build_cclass(self)
        return expand_macros(text, self.locals, self.globals,
                             returns=self.returns, eclass=self.estimates,
                             cclass=self.cclass)

    def _extended_references(self, text):
        """Substitute `` `: word count ...' `` references with their text."""
        if "`:" not in text:
            return text
        scratch = "__openstat_inline_macro"
        saved = self.locals.get(scratch)
        had = scratch in self.locals
        output: list[str] = []
        index = 0
        while index < len(text):
            if text.startswith("`: ", index) or text.startswith("`:", index):
                end = self._extended_reference_end(text, index)
                if end < 0:
                    output.append(text[index:])
                    break
                request = self.expand(text[index + 2:end].strip())
                try:
                    self._programming_extended_macro("local", f"{scratch}: {request}")
                    output.append(str(self.locals.get(scratch, "")))
                finally:
                    if had:
                        self.locals[scratch] = saved
                    else:
                        self.locals.pop(scratch, None)
                index = end + 1
                continue
            output.append(text[index])
            index += 1
        return "".join(output)

    @staticmethod
    def _extended_reference_end(text: str, start: int) -> int:
        """Return the ``'`` that closes the `` `: ...' `` at ``start``."""
        depth, index = 0, start + 2
        while index < len(text):
            char = text[index]
            if char == "`":
                depth += 1
            elif char == "'":
                if depth:
                    depth -= 1
                else:
                    return index
            index += 1
        return -1

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
        except UndefinedCValue as ex:
            raise StataError(str(ex), 133) from ex
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
                    text = self.expand(statement.text.strip()).strip()
                    if not text:
                        index += 1
                        continue
                    index = self._statement(statements, index, text)
                    if self._loop_break or self._loop_continue:
                        return
                except (StataError, MacroError, SyntaxError_, ValueError, OSError,
                        KeyError, TypeError, IndexError) as ex:
                    if not isinstance(ex, StataError):
                        ex = StataError(str(ex), 198)
                    if ex.line is None:
                        ex.line = statement.line
                    raise ex
        finally:
            self._depth -= 1

    #: Prefixes that change how the rest of a statement runs.
    _PREFIXES = ("quietly", "capture", "noisily")

    def _statement(self, statements, index, text):
        """Run one lexed statement; return the index of the next one."""
        prefixes, body = self._peel_prefixes(text)
        head = body.split(maxsplit=1)
        # ``continue, break`` puts the comma right after the word, so the
        # command token has to shed it before the alias lookup.
        word = head[0].lower().rstrip(",") if head else ""
        command = ALIASES.get(word, word)
        if command.startswith("!"):
            # Stata's shell escape: the rest of the line goes to the OS.
            self._shell_escape(body[1:].strip())
            return index + 1
        self.last_command = command
        self.cclass = build_cclass(self)
        if body.endswith("{"):
            if command in {"forvalues", "foreach", "while", "if", "else"} or prefixes:
                return self._block(statements, index, command, prefixes)
            raise StataError(f"command {command} is not implemented in OpenStat", 199)
        if prefixes:
            if command == "program" and re.match(r"prog(?:ram)?\s+(?:define\s+)?", body) \
                    and not body.startswith("program drop"):
                self._apply_prefixes(prefixes, lambda: None)
                return self._define_program(statements, index, body)
            self._apply_prefixes(prefixes, lambda: self.command(body))
            return index + 1
        if command == "continue":
            rest = body[len("continue"):].strip()
            if "break" in [word.lower().strip(",") for word in tokens(rest)]:
                self._loop_break = True
            else:
                self._loop_continue = True
            return index + 1
        if command == "break":
            self._loop_break = True
            return index + 1
        if command == "if":
            taken = self._inline_if(body)
            cursor = index + 1
            if cursor < len(statements) and statements[cursor].text.strip().startswith("else"):
                cursor = self._else_chain(statements, cursor, taken)
            return cursor
        if command == "else":
            return self._else_chain(statements, index, False)
        if command == "program" and re.match(r"prog(?:ram)?\s+(?:define\s+)?", body) \
                and not body.startswith("program drop"):
            return self._define_program(statements, index, body)
        if body in {"mata", "mata:"}:
            return self._mata_block(statements, index)
        self.command(body)
        return index + 1

    def _peel_prefixes(self, text):
        """Split stacked ``quietly``/``capture``/``noisily`` prefixes off."""
        prefixes, body = [], text
        while True:
            head = body.split(maxsplit=1)
            if len(head) < 2:
                break
            word = ALIASES.get(head[0].lower(), head[0].lower())
            if word not in self._PREFIXES:
                break
            prefixes.append(word)
            body = head[1].strip()
        return prefixes, body

    def _apply_prefixes(self, prefixes, action):
        """Run ``action`` under Stata's prefix semantics.

        ``quietly`` suppresses output, ``capture`` suppresses output *and*
        turns an error into ``r(###)`` with ``_rc`` set, and ``noisily``
        restores output inside a quiet context.
        """
        if not prefixes:
            action()
            return
        saved_quiet = self._quiet
        capture = "capture" in prefixes
        if capture or "quietly" in prefixes:
            self._quiet += 1
        if "noisily" in prefixes:
            self._quiet = 0
        try:
            action()
            self.rc = 0
        except (StataError, MacroError, SyntaxError_, ValueError, OSError,
                KeyError, TypeError, IndexError) as ex:
            if not capture:
                raise
            self.rc = getattr(ex, "rc", 198)
        finally:
            self._quiet = saved_quiet
            if capture:
                self.locals["_rc"] = str(self.rc)

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
                # Route through the statement dispatcher so flow-of-control
                # commands (``continue``, ``break``) and prefixes behave as
                # they do on their own line.
                self._statement([Statement(command, None)], 0, command)
            return True
        return False

    def _else_chain(self, statements, cursor, taken):
        """Consume ``else``/``else if`` alternatives after an ``if``.

        A skipped alternative is consumed without expanding or evaluating its
        condition, so ``else if unknown_name`` cannot fail once a branch has
        been taken.
        """
        while cursor < len(statements):
            text = statements[cursor].text.strip()
            if not text.startswith("else"):
                break
            if text.endswith("{"):
                end = self._block_end(statements, cursor)
                if not taken:
                    self._execute(statements[cursor + 1:end])
                    taken = True
                cursor = end + 1
                continue
            if text.startswith("else if"):
                body = text[len("else if"):].strip()
                cursor += 1
                if taken:
                    continue
                condition, command = self._split_condition(body)
                if not condition:
                    raise StataError("if requires a condition")
                if self._condition(self.expand(condition)):
                    taken = True
                    if command:
                        self.command(command)
                continue
            cursor += 1
            if taken:
                continue
            command = text[len("else"):].strip()
            if command:
                self.command(command)
            taken = True
        return cursor

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
        "error", "continue", "break", "do", "run", "include", "numlist",
        "return", "estimates", "adopath", "more", "discard", "putexcel",
    }

    def _split_condition(self, body: str) -> tuple[str, str]:
        """Split ``if`` text into its condition and the command that follows.

        The command begins at the first word that names a command.  A word
        that is a variable, a macro or a function call belongs to the
        expression, so ``if x == 1`` and ``if missing(x) local y 2`` keep
        their leading identifiers.
        """
        words = self._expression_words(body)
        for position, (word, start, end) in enumerate(words):
            following = words[position + 1][0] if position + 1 < len(words) else ""
            if following in {"==", "!=", "<", ">", "<=", ">=", "&", "|", "+", "-",
                             "*", "/", "^", "==="}:
                # The word is part of the expression, not a command.
                continue
            if not self._starts_command(word, body, end):
                continue
            return body[:start].strip(), body[start:].strip()
        return body.strip(), ""

    def _starts_command(self, word, body, end):
        """Whether ``word`` begins a command rather than continuing an expression."""
        lowered = word.lower()
        if lowered in self._IF_COMMANDS:
            return True
        if end < len(body) and body[end] in "([":
            return False
        if lowered in self.locals or lowered in self.globals or lowered in self.scalars:
            return False
        if any(lowered == str(name).lower() for name in self.data.columns):
            return False
        return not lowered.startswith("_")

    def _expression_words(self, body: str) -> list[tuple[str, int, int]]:
        """Return the (word, start, end) triples of a command's argument list.

        Quoted strings and bracketed groups are skipped so that their
        contents never look like a command name.
        """
        words: list[tuple[str, int, int]] = []
        depth, index = 0, 0
        while index < len(body):
            end = quoted_end(body, index)
            if end is not None:
                index = end
                continue
            char = body[index]
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

    def _block_end(self, statements, index):
        """Return the index of the ``}`` that closes the block at ``index``."""
        depth, cursor = 1, index + 1
        while cursor < len(statements):
            value = statements[cursor].text.strip()
            if value.endswith("{"):
                depth += 1
            elif value == "}":
                depth -= 1
                if depth == 0:
                    return cursor
            cursor += 1
        raise StataError("unclosed brace")

    def _block(self, statements, index, command, prefixes=()):
        """Execute a braced block: loops, if/else, prefixed and bare blocks.

        The block header is expanded lazily so that a ``while`` condition
        sees the current value of a loop macro on every iteration.
        """
        end = self._block_end(statements, index)
        body = statements[index + 1:end]
        raw_header = statements[index].text.strip()[:-1].strip()
        header_prefixes, header_body = self._peel_prefixes(raw_header)
        prefixes = tuple(prefixes) + tuple(header_prefixes)
        cursor = end + 1
        if command == "forvalues":
            self._apply_prefixes(prefixes, lambda: self._forvalues(header_body, body))
        elif command == "foreach":
            self._apply_prefixes(prefixes, lambda: self._foreach(header_body, body))
        elif command == "while":
            condition = header_body[len("while"):].strip()

            def loop():
                self._loop_depth += 1
                try:
                    while self._condition(self.expand(condition)):
                        self._tick()
                        self._loop_continue = False
                        self._execute(body)
                        if self._loop_break:
                            break
                finally:
                    self._loop_break = self._loop_continue = False
                    self._loop_depth -= 1

            self._apply_prefixes(prefixes, loop)
        elif command == "if":
            cursor = self._if_chain(statements, index, cursor, body,
                                    header_body[2:].strip(), prefixes)
        elif command == "else":
            # An ``else`` block is consumed by the ``if`` handler.
            return cursor
        else:
            self._apply_prefixes(prefixes, lambda: self._execute(body))
        return cursor

    def _if_chain(self, statements, index, cursor, body, condition, prefixes=()):
        """Run an ``if`` block and any ``else if``/``else`` alternatives."""
        if self._condition(self.expand(condition)):
            self._apply_prefixes(prefixes, lambda: self._execute(body))
            return self._skip_alternatives(statements, cursor)
        while cursor < len(statements):
            text = statements[cursor].text.strip()
            if not text.startswith("else"):
                break
            if text.endswith("{"):
                end = self._block_end(statements, cursor)
                alternative = statements[cursor + 1:end]
                cursor = end + 1
                if text.startswith("else if"):
                    alternative_condition = text[len("else if"):-1].strip()
                    if self._condition(self.expand(alternative_condition)):
                        self._apply_prefixes(prefixes, lambda: self._execute(alternative))
                        return self._skip_alternatives(statements, cursor)
                    continue
                self._apply_prefixes(prefixes, lambda: self._execute(alternative))
                return cursor
            # A single-line ``else command``.
            cursor += 1
            self._apply_prefixes(
                prefixes, lambda: self.command(text[len("else"):].strip()))
            return cursor
        return cursor

    def _skip_alternatives(self, statements, cursor):
        """Skip ``else``/``else if`` blocks that follow a taken branch."""
        while cursor < len(statements):
            text = statements[cursor].text.strip()
            if not text.startswith("else"):
                break
            if not text.endswith("{"):
                cursor += 1
                continue
            cursor = self._block_end(statements, cursor) + 1
        return cursor

    def _forvalues(self, header, body):
        match = re.fullmatch(r"\w+\s+(\w+)\s*=\s*(.+)", header, re.S)
        if not match:
            raise StataError("invalid forvalues", 198)
        name, spec = match[1], match[2].strip()
        values = self._numlist(spec, maximum=160000).split()
        self._loop_depth += 1
        try:
            for value in values:
                self._tick()
                self.locals[name] = value
                self._loop_continue = False
                self._execute(body)
                if self._loop_break:
                    break
        finally:
            self._loop_break = self._loop_continue = False
            self._loop_depth -= 1

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
        elif source in {"of varlist", "of newvarlist"}:
            values = " ".join(self.varlist(values))
        elif source == "of numlist":
            values = self._numlist(values)
        self._loop_depth += 1
        try:
            for value in tokens(values):
                self._tick()
                self.locals[name] = unquote(value)
                self._loop_continue = False
                self._execute(body)
                if self._loop_break:
                    break
        finally:
            self._loop_break = self._loop_continue = False
            self._loop_depth -= 1

    def _numlist(self, text: str, maximum=1600) -> str:
        """Expand a Stata ``numlist`` and return it as space-separated text."""
        from .programming import numlist
        return " ".join(numlist(text.strip(), maximum=maximum))

    def _define_program(self, statements, index, text):
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
        """Read a comma-separated option list using the shared parser.

        Option values may contain commas, quotes and nested parentheses, so
        this delegates to :func:`openstat.parser.parse_options` instead of
        re-deriving the grammar here.
        """
        cleaned = text.strip()
        if cleaned.startswith(","):
            cleaned = cleaned[1:]
        output = {}
        try:
            parsed = parse_options(cleaned)
        except ValueError as error:
            raise StataError(str(error), 198) from error
        for name, value in parsed:
            if name not in allowed:
                raise StataError(f"option {name} not allowed", 198)
            if name in output:
                raise StataError(f"option {name} specified more than once", 198)
            output[name] = value
        return output

    def varlist(self, text, default=True):
        hidden = getattr(self, "_hidden_variables", ())
        if not text.strip():
            if not default:
                return []
            return [str(column) for column in self.data.columns if column not in hidden]
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
            elif depth == 0 and (index == 0 or text[index - 1].isspace()):
                for word in ("if", "in"):
                    end = index + len(word)
                    if text.startswith(word, index) and (end == len(text) or text[end].isspace()):
                        positions.append((index, word))
            index += 1
        mask = pd.Series(True, index=self.data.index)
        base = text[:positions[0][0]].strip() if positions else text.strip()
        for k, (position, kind) in enumerate(positions):
            end = positions[k + 1][0] if k + 1 < len(positions) else None
            value = text[position + len(kind):end if end is not None else None].strip()
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
        self.storage_types = {
            name: READSTAT_TYPE.get(kind, kind)
            for name, kind in meta.readstat_variable_types.items()
        }
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
        head = words[0]
        arg = words[1].strip() if len(words) > 1 else ""
        if head.endswith(","):
            # ``exit, clear`` and friends: the comma is a delimiter, not part
            # of the command name.
            head = head.rstrip(",")
            arg = ("," + arg) if arg else ","
        command = ALIASES.get(head.lower(), head.lower())
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
        if self._mixin_command(command, arg):
            return
        if command == "clear":
            if arg not in {"", "all"}:
                raise StataError("unsupported clear target")
            self.data = pd.DataFrame()
            self.variable_labels = {}
            self.value_labels = {}
            self.value_label_names = {}
            self.formats = {}
            self.storage_types = {}
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
        if command == "duplicates":
            self._duplicates(arg)
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
                                             self.value_label_names, self.formats,
                                             self.storage_types, self.dataset_label))
            return
        if command == "restore":
            opt = self.options(arg.lstrip(", "), {"not", "preserve"})
            if self._preserved is None:
                raise StataError("nothing to restore", 622)
            if "not" not in opt:
                (self.data, self.variable_labels, self.value_labels, self.value_label_names,
                 self.formats, self.storage_types, self.dataset_label) = copy.deepcopy(self._preserved)
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
                       "exit", "error", "sleep", "timer", "erase", "type", "file", "shell",
                       "more"}:
            self._system(command, arg)
            return
        if command in self.programs:
            self._call_program(command, arg)
            return
        ado = self._find_ado(command)
        if ado:
            self._execute(lex(ado.read_text(encoding="utf-8-sig")))
            if command in self.programs:
                self._call_program(command, arg)
                return
            raise StataError(f"{command}.ado did not define {command}", 199)
        raise StataError(f"command {command} is not implemented in OpenStat", 199)

    def _mixin_command(self, command, arg):
        """Let the command-group mixins claim a command before the engine does.

        Each mixin owns one family of commands and returns ``True`` when it
        handled the call.  The engine keeps its own dispatch table for
        everything else, so a new command family can be added without
        growing this method.
        """
        for handler in (self._programming, self._extended_data,
                        self._output_command, self._panel_command):
            if handler(command, arg):
                return True
        return False

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
        increment = re.fullmatch(r"\s*(\+\+|--)?\s*(\w+)\s*(\+\+|--)?\s*", arg)
        if increment and (increment[1] or increment[3]):
            name = increment[2]
            store = self.locals if command == "local" else self.globals
            try:
                current = int(str(store.get(name, "0") or "0").strip())
            except ValueError as ex:
                raise StataError(f"macro {name} is not an integer", 7) from ex
            change = 1 if (increment[1] or increment[3]) == "++" else -1
            store[name] = str(current + change)
            self.rc = 0
            return
        match = re.fullmatch(r"(\w+)\s*(.*)", arg, re.S)
        if not match:
            raise StataError("macro name required")
        name, value = match[1], match[2].strip()
        store = self.locals if command == "local" else self.globals
        if value.startswith(":"):
            self._programming_extended_macro(command, f"{name} : {value[1:].strip()}")
            self.rc = 0
            return
        elif value.startswith("="):
            evaluated = self.evaluate(value[1:])
            # Stata keeps the full precision of the value in the macro; it
            # is only the *display* of the macro that rounds.
            value = self._macro_text(evaluated)
        else:
            value = unquote(value)
        store[name] = value
        self.rc = 0

    @staticmethod
    def _macro_text(value):
        """The text Stata stores in a macro when a number is assigned to it."""
        if isinstance(value, str):
            return value
        if isinstance(value, (pd.Series, np.ndarray)):
            if len(value) == 0:
                return "."
            value = value.iloc[0] if isinstance(value, pd.Series) else value[0]
        if isinstance(value, (bool, np.bool_)):
            return "1" if value else "0"
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if math.isnan(number):
            return "."
        from .formats import format_value

        # Stata's macro text is the %18.0g rendering without the padding.
        return format_value(number, "%18.0g").strip()

    def _extended_macro(self, command, text):
        """Delegate to the extended-macro evaluator in :mod:`openstat.programming`."""
        return self._programming_extended_macro(command, text)

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
        """Delegate to the richer parser in :mod:`openstat.programming`."""
        return self._programming_gettoken(arg)

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
            raise StataError(str(ex), getattr(ex, "rc", None) or 197) from ex
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
        """Run a defined program in its own scope.

        A program sees a fresh argument scope, and the temporary variables,
        markers and hidden names it allocates disappear when it returns --
        including when it fails.  A program declared ``sortpreserve`` also
        puts the observation order back the way it found it.
        """
        options = self.program_options.get(name, {})
        saved_locals = self.locals
        saved_columns = set(self.data.columns)
        saved_hidden = set(self._hidden_variables)
        saved_markers = list(getattr(self, "_hidden_markers", []))
        order = None
        arguments = tokens(arg)
        if "sortpreserve" in options:
            order = self._hidden_name()
            self.data[order] = np.arange(len(self.data), dtype=float)
            self._hidden_variables.add(order)
        self.locals = {str(i): unquote(value) for i, value in enumerate(arguments, 1)}
        # `` `0' `` is the whole argument string, options included -- that is
        # what ``syntax`` parses -- while `` `1' ``.. hold the tokens.
        self.locals["0"] = arg.strip() if arg else ""
        try:
            self._execute(self.programs[name])
        except _ExitFlow as flow:
            if flow.code:
                raise StataError("program exited", flow.code)
        finally:
            if order is not None and order in self.data.columns:
                self.data = self.data.sort_values(order, kind="stable").reset_index(drop=True)
            created = [column for column in self.data.columns if column not in saved_columns]
            drop = [column for column in created if column in saved_hidden
                    or column in self._hidden_variables]
            if drop:
                self.data = self.data.drop(columns=drop)
            self._hidden_variables = saved_hidden
            self._hidden_markers = saved_markers
            self.locals = saved_locals

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
            self.rng.set_seed(int(value))
            return
        if name == "type":
            if value not in {"float", "double"}:
                raise StataError("set type accepts only float or double")
            self.default_type = value
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
            # A numeric format applied to a string leaves the text alone;
            # a text format pads it to the field width.
            if fmt and is_text_format(fmt):
                return format_value(value, fmt)
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
        ``_newline()``.  ``_col()`` moves the cursor to an absolute column,
        so the engine tracks how much of the current line has been written.
        The ``as`` and ``in`` modifiers change the style of the directives
        that follow them.
        """
        parts: list[str] = []
        pending = arg.strip()
        fmt = None
        column = 0
        while pending:
            try:
                directive, pending = self._display_directive(pending)
            except ValueError as error:
                raise StataError(str(error), 198) from error
            if directive is None:
                break
            kind, value = directive
            if kind == "string":
                # A format applies to the item that follows it and then
                # reverts, so ``%4s "x"`` pads but the padding does not
                # leak into later directives.
                try:
                    text = self._smcl(self._format(value, fmt) if fmt else value)
                except ValueError as error:
                    raise StataError(str(error), 120) from error
                fmt = None
                parts.append(text)
                column = len(text) if "\n" not in text else len(text.rsplit("\n", 1)[1])
            elif kind == "value":
                # Stata evaluates each directive as it reaches it, so a
                # failing expression leaves the earlier output in place and
                # appends the error text to it.
                try:
                    text = self._format(self.evaluate(value), fmt)
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
                parts.append(text)
                column += len(text)
                fmt = None
            elif kind == "format":
                fmt = value
            elif kind == "newline":
                text = "\n" * int(value)
                parts.append(text)
                column = 0
            elif kind == "column":
                pad = int(value) - 1 - column
                if pad > 0:
                    parts.append(" " * pad)
                    column += pad
            elif kind == "skip":
                parts.append(" " * int(value))
                column += int(value)
            elif kind == "tab":
                parts.append("\t")
                column += 1
            elif kind == "char":
                text = chr(int(value))
                parts.append(text)
                column += 1
            elif kind == "continue":
                pass
        self.emit("".join(parts))

    #: SMCL character directives that ``display`` renders literally.
    _SMCL_CHARS = {
        "+": "+", "-": "-", "|": "|", "T": "┬", "B": "┴", "TL": "┌", "TR": "┐",
        "BL": "└", "BR": "┘", "l": "├", "r": "┤", "t": "┬", "b": "┴",
    }

    def _smcl(self, text: str) -> str:
        """Render the SMCL directives that appear inside displayed strings."""
        text = re.sub(r"\{hline\s+(\d+)\}", lambda match: "─" * int(match[1]), text)
        text = re.sub(r"\{c\s+([^}]*)\}",
                      lambda match: self._SMCL_CHARS.get(match[1].strip(), match[1].strip()),
                      text)
        return text

    #: Characters that continue an expression rather than start a directive.
    _OPERATORS = set("+-*/^&|<>!=~,")

    def _split_expression(self, pending: str) -> tuple[str, str]:
        """Return the expression that begins a display directive.

        The expression runs until the next display directive, which starts
        with a format, a layout directive or a new string literal.  A string
        literal that follows an operator belongs to the same expression:
        ``"a" + " " + "b"`` is one value, not five directives.
        """
        depth, index = 0, 0
        while index < len(pending):
            # A string literal that does not continue an operator starts a
            # new directive, so the expression ends before it.
            if (depth == 0 and index > 0
                    and (pending.startswith('"', index) or pending.startswith('`"', index))
                    and pending[:index].rstrip()[-1:] not in self._OPERATORS):
                return pending[:index].strip(), pending[index:]
            end = quoted_end(pending, index)
            if end is not None:
                index = end
                continue
            char = pending[index]
            if char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            elif depth == 0 and index > 0:
                if char == "%":
                    return pending[:index].strip(), pending[index:]
                if char == "_" and re.match(
                        r"_(?:newline|column|col|skip|char|continue|tab)\b", pending[index:]):
                    return pending[:index].strip(), pending[index:]
                if char in '`"' and pending[:index].rstrip()[-1:] not in self._OPERATORS:
                    return pending[:index].strip(), pending[index:]
            index += 1
        return pending.strip(), ""

    def _display_directive(self, pending: str):
        """Split off one display directive, returning (kind, value), rest."""
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
        if pending.startswith('"') or pending.startswith('`"'):
            end = quoted_end(pending, 0)
            rest = pending[end:].lstrip()
            if rest[:1] not in self._OPERATORS:
                raw = pending[:end]
                inner = raw[2:-2] if raw.startswith('`"') else raw[1:-1]
                return ("string", inner), rest
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
        if body_command not in allowed and body_command not in self.programs:
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
    def _describe_variables(self, names):
        """Render ``describe`` the way Stata does.

        Stata's layout is a two-row header and one row per variable:

            Variable      Storage   Display    Value
                name         type    format    label      Variable label

        The columns sit at offsets 0, 16, 24, 35 and 46.
        """
        rule = "-" * 255
        self.emit("Contains data")
        # The count is right-justified so that it ends at column 27, and the
        # line is padded out to 46 characters.
        self.emit(f"{' Observations:':<13}{f'{len(self.data):,}':>14}".ljust(46))
        self.emit(f"{'    Variables:':<15}{f'{len(self.data.columns):,}':>13}".ljust(46))
        self.emit(rule)
        self.emit(f"{'Variable':<14}{'Storage':<10}{'Display':<11}{'Value'}")
        self.emit(f"{'    name':<17}{'type':<8}{'format':<10}{'label':<11}Variable label")
        self.emit(rule)
        for name in names:
            storage = self.storage_types.get(name) or self._infer_storage(name)
            display = self.formats.get(name) or DEFAULT_FORMAT.get(storage, "%9s")
            label = self.value_label_names.get(name, "")
            self.emit(f"{name:<16}{storage:<8}{display:<11}{label:<11}"
                      f"{self.variable_labels.get(name, '')}")
        self.emit(rule)
        self.emit("Sorted by: ")
        self.emit("     Note: Dataset has changed since last saved.")

    def _infer_storage(self, name):
        """Guess a variable's storage type when it was never recorded."""
        column = self.data[name]
        if pd.api.types.is_string_dtype(column.dtype) or column.dtype == object:
            return "str%d" % max(column.dropna().astype(str).str.len().max() or 1, 1)
        return "double"

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
            self._describe_variables(names)
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
        # ``meanonly`` computes the same returned values but prints nothing.
        quiet = "meanonly" in opt
        self.returns = {"N": 0}
        if not quiet:
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
                # The plain central moments, which are what SciPy's
                # skew(bias=True) and kurtosis(bias=True, fisher=False)
                # compute; doing it here keeps scipy out of the import path.
                deviations = values - values.mean()
                m2 = (deviations ** 2).mean()
                m3 = (deviations ** 3).mean()
                m4 = (deviations ** 4).mean()
                self.returns["skewness"] = m3 / m2 ** 1.5
                self.returns["kurtosis"] = m4 / m2 ** 2
            if quiet:
                continue
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
            self.packages.cwd = self.cwd
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
            parts = split_outside(arg)
            options = self.options(",".join(parts[1:]), {"clear", "clear_all", "nostop"}) \
                if len(parts) > 1 else {}
            code = 0
            if parts and parts[0].strip():
                code = int(self.evaluate(parts[0]))
            if "clear" in options or "clear_all" in options:
                self.data = pd.DataFrame()
                self.variable_labels = {}
                self.value_labels = {}
                self.value_label_names = {}
                self.formats = {}
                self.storage_types = {}
                self.dataset_label = ""
            raise _ExitFlow(code)
        if command == "error":
            text = split_outside(arg)[0].strip()
            code = int(self.evaluate(text)) if text else 198
            raise StataError(f"error {code}", code)
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
        # The test harness and ``cd`` can replace or rebind the package
        # manager, so the working directory is pushed on every call.
        self.packages.cwd = self.cwd
        if command == "sysdir":
            words = tokens(arg)
            if len(words) != 3 or words[:2] != ["set", "PLUS"]:
                raise StataError("only sysdir set PLUS is implemented", 199)
            self.packages = PackageManager(self.path(words[2]))
            return
        if command == "adopath":
            self._adopath(arg)
            return
        if command == "which":
            if " " in arg:
                raise StataError("which requires one command")
            path = self._find_ado(arg)
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
        source = unquote(str(opt["from"])) if "from" in opt else None
        if words and words[0] == "dir":
            for entry in self.packages.installed():
                self.emit(entry["name"] + " (execution compatibility unverified)")
            return
        if len(words) != 2:
            raise StataError(f"{command} requires an action and a package name", 199)
        action, name = words
        try:
            if action == "describe":
                manifest = self.packages.describe(name, source)
                self.emit(f"{manifest.get('name', name)}  {manifest.get('title', '')}".rstrip())
                for line in manifest.get("description", []) or []:
                    self.emit(line)
                return
            if action == "uninstall":
                self.packages.uninstall(name)
                self.emit(f"package {name} uninstalled")
                return
            if action != "install":
                raise StataError(f"{command} {action} is not implemented", 199)
            if command == "net" and source is None:
                raise StataError("net install requires from(URL)")
            manifest = self.packages.install(name, source, "replace" in opt,
                                             "all" in opt)
        except (PackageError, OSError, ValueError) as ex:
            raise StataError(f"package {action} failed: {ex}", 601) from ex
        self.emit(f"installed {manifest['name']}: {len(manifest['files'])} files; "
                  "execution compatibility unverified")

    def _adopath(self, arg):
        """Implement ``adopath``: list, ``++`` prepend and ``-`` removal."""
        text = arg.strip()
        if not text:
            for entry in getattr(self.packages, "adopath", []):
                self.emit(str(entry))
            return
        if text.startswith("++"):
            self.packages.adopath_add(unquote(text[2:].strip()), prepend=True)
            return
        if text.startswith("+"):
            self.packages.adopath_add(unquote(text[1:].strip()))
            return
        if text.startswith("-"):
            self.packages.adopath_remove(unquote(text[1:].strip()))
            return
        raise StataError("adopath accepts ++, +, - or no argument", 198)

    def _find_ado(self, name):
        """Locate ``name.ado`` on the adopath, or return ``None``."""
        path = self.packages.find_in_path(name)
        if path is None:
            path = self.packages.find(name, recursive=False)
        return Path(path) if path is not None else None
