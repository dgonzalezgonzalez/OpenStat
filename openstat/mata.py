"""Interpreted Mata source language with explicit parsing and bounded execution.

No Python eval/exec, native-plugin loader, or compiled .mlib decoder is used.
"""
from __future__ import annotations

import os
import re
import math
from dataclasses import dataclass, field

import numpy as np

from .parser import quoted_end, unquote


class MataError(ValueError):
    pass


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


_ELEMENTS = {"real", "numeric", "complex", "string", "transmorphic"}
_SHAPES = {"scalar", "vector", "rowvector", "colvector", "matrix"}
_ORDER = ["=", "?", "\\", "::", ",", "..", ":|", "|", ":&", "&",
          ":==", "==", ":>=", ">=", ":<=", "<=", ":<", "<", ":>", ">",
          ":!=", "!=", ":+", "+", ":-", "-", ":*", "*", "#", ":/", "/",
          ":^", "^"]
_PREC = {op: index + 1 for index, op in enumerate(_ORDER)}
_PREC.update({"&&": _PREC["&"], "||": _PREC["|"]})
_OPERATORS = sorted(set(_ORDER + ["++", "--", "&&", "||", "!", "'", "[|", "|]",
    "(", ")", "[", "]", "{", "}", ";", ":"]), key=len, reverse=True)


def _lex(source):
    result, i, nesting = [], 0, 0
    while i < len(source):
        char = source[i]
        if char in " \t\r":
            i += 1
            continue
        if source.startswith("//", i):
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
            continue
        if source.startswith("/*", i):
            depth, i = 1, i + 2
            while i < len(source) and depth:
                if source.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif source.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                raise MataError("unclosed Mata comment")
            continue
        if char == "\n":
            if not nesting and result and result[-1][1] not in _PREC:
                result.append(("symbol", "\n"))
            i += 1
            continue
        if char == '"' or source.startswith('`"', i):
            try:
                end = quoted_end(source, i)
            except ValueError as exc:
                raise MataError(str(exc)) from exc
            result.append(("literal", unquote(source[i:end])))
            i = end
            continue
        number = re.match(r"(?:\d+(?:\.(?!\.)\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:i)?", source[i:])
        if number:
            value = number[0]
            result.append(("literal", complex(value[:-1] + "j") if value.endswith("i") else float(value)))
            i += len(value)
            continue
        identifier = re.match(r"[A-Za-z_]\w*", source[i:])
        if identifier:
            result.append(("identifier", identifier[0]))
            i += len(identifier[0])
            continue
        operator = next((op for op in _OPERATORS if source.startswith(op, i)), None)
        if operator:
            result.append(("symbol", operator))
            nesting += int(operator in {"(", "[", "[|"}) - int(operator in {")", "]", "|]"})
            if nesting < 0:
                raise MataError("unbalanced Mata delimiter")
            i += len(operator)
            continue
        if char == ".":
            if i + 1 < len(source) and source[i + 1].isalpha():
                raise MataError("extended Mata missing literals are not implemented")
            result.append(("literal", np.nan))
            i += 1
            continue
        raise MataError(f"unsupported Mata token: {char}")
    if nesting:
        raise MataError("unbalanced Mata delimiter")
    result.append(("eof", ""))
    return result


class _Parser:
    def __init__(self, source):
        self.tokens, self.pos = _lex(source), 0

    @property
    def token(self):
        return self.tokens[self.pos][1]

    def take(self, value=None):
        token = self.tokens[self.pos]
        if value is not None and token[1] != value:
            raise MataError(f"expected Mata {value}, found {token[1]}")
        self.pos += 1
        return token

    def separators(self, newlines_only=False):
        while self.token in ({"\n"} if newlines_only else {"\n", ";"}):
            self.take()

    def identifier(self):
        kind, value = self.take()
        if kind != "identifier":
            raise MataError("Mata identifier required")
        return value

    def expression(self, minimum=0, stop=frozenset()):
        kind, token = self.take()
        if kind == "literal":
            left = ("literal", token)
        elif token in {"+", "-", "!", "++", "--"}:
            precedence = _PREC["/"] + 1 if token in {"+", "-"} else 100
            left = ("unary", token, self.expression(precedence, stop))
        elif token == "(":
            if self.token == "void" and self.tokens[self.pos + 1][1] == ")":
                self.take("void")
                self.take(")")
                left = ("void", self.expression(0, stop))
            else:
                left = self.expression(stop={")"})
                self.take(")")
        elif kind == "identifier":
            left = ("variable", token)
        else:
            raise MataError(f"invalid Mata expression near {token}")
        while self.token not in stop:
            token = self.token
            if token == "(" and left[0] == "variable":
                self.take()
                args = []
                while self.token != ")":
                    args.append(self.expression(stop={",", ")"}))
                    if self.token != ",":
                        break
                    self.take(",")
                self.take(")")
                left = ("call", left[1], args)
                continue
            if token in {"[", "[|"}:
                self.take()
                if token == "[|":
                    index = self.expression(stop={"|]"})
                    self.take("|]")
                    left = ("range_subscript", left, index)
                    continue
                indices = []
                while True:
                    indices.append(("literal", np.nan) if self.token in {",", "]"} else self.expression(stop={",", "]"}))
                    if self.token != ",":
                        break
                    self.take(",")
                self.take("]")
                if len(indices) > 2:
                    raise MataError("Mata subscript requires one or two indices")
                left = ("subscript", left, indices)
                continue
            if token in {"'", "++", "--"}:
                self.take()
                left = ("postfix", token, left)
                continue
            implicit = left[0:2] == ("postfix", "'") and (self.tokens[self.pos][0] == "identifier" or token == "(")
            operator = "*" if implicit else token
            precedence = _PREC.get(operator, -1)
            if precedence < minimum:
                break
            if not implicit:
                self.take()
            if operator == "?":
                yes = self.expression(stop={":"})
                self.take(":")
                left = ("conditional", left, yes, self.expression(precedence, stop))
            else:
                right = self.expression(precedence if operator in {"=", "^", ":^"} else precedence + 1, stop)
                left = ("assign", left, right) if operator == "=" else ("binary", operator, left, right)
        return left

    def type_spec(self):
        element, shape = "transmorphic", "matrix"
        if self.token in _ELEMENTS:
            element = self.take()[1]
        if self.token in _SHAPES:
            shape = self.take()[1]
        return element, shape

    def statement(self):
        self.separators(newlines_only=True)
        token = self.token
        if token == ";":
            self.take()
            return ("block", [])
        if token == "{":
            self.take()
            statements = self.program("}")
            self.take("}")
            return ("block", statements)
        if token in {"if", "while", "for"}:
            self.take()
            self.take("(")
            if token == "for":
                init = self.expression(stop={";"}) if self.token != ";" else None
                self.take(";")
                condition = self.expression(stop={";"}) if self.token != ";" else ("literal", 1)
                self.take(";")
                increment = self.expression(stop={")"}) if self.token != ")" else None
            else:
                condition = self.expression(stop={")"})
            self.take(")")
            body = self.statement()
            if token == "for":
                return ("for", init, condition, increment, body)
            if token == "while":
                return ("while", condition, body)
            self.separators()
            alternative = None
            if self.token == "else":
                self.take()
                alternative = self.statement()
            return ("if", condition, body, alternative)
        if token == "do":
            self.take()
            body = self.statement()
            self.separators()
            self.take("while")
            self.take("(")
            condition = self.expression(stop={")"})
            self.take(")")
            return ("do", condition, body)
        if token in {"return", "break", "continue"}:
            self.take()
            value = None
            if token == "return" and self.token == "(":
                self.take()
                value = self.expression(stop={")"})
                self.take(")")
            return (token, value)
        if token in _ELEMENTS | _SHAPES | {"function", "void", "external"}:
            external = token == "external"
            if external:
                self.take()
            if self.token == "void":
                self.take()
                spec = ("void", "matrix")
            else:
                spec = self.type_spec()
            explicit_function = self.token == "function"
            if explicit_function:
                self.take()
            name = self.identifier()
            if self.token == "(":
                self.take()
                parameters, optional = [], False
                while self.token != ")":
                    if self.token == "|":
                        self.take()
                        optional = True
                    parameter_type = self.type_spec()
                    parameters.append((self.identifier(), parameter_type, optional))
                    if self.token != ",":
                        break
                    self.take(",")
                self.take(")")
                return ("function", name, spec, parameters, self.statement())
            if explicit_function or spec[0] == "void":
                raise MataError("Mata function definition requires arguments")
            names = [name]
            while self.token == ",":
                self.take()
                names.append(self.identifier())
            return ("declaration", spec, names, external)
        if token == "version":
            self.take()
            version = self.take()
            if version[0] != "literal" or not np.isscalar(version[1]):
                raise MataError("invalid Mata version directive")
            return ("version", version[1])
        return ("expression", self.expression(stop={"\n", ";", "}"}))

    def program(self, end=""):
        statements = []
        self.separators()
        while self.token != end:
            if self.tokens[self.pos][0] == "eof":
                raise MataError("unclosed Mata block")
            statements.append(self.statement())
            if self.token not in {"\n", ";", "}", ""}:
                # A closing brace separates a block from the next statement.
                if statements[-1][0] not in {"function", "if", "for", "while", "do", "block"}:
                    raise MataError(f"unexpected Mata token: {self.token}")
            self.separators()
        return statements


@dataclass
class _Scope:
    values: dict
    types: dict = field(default_factory=dict)
    references: dict = field(default_factory=dict)


class Mata:
    def __init__(self, engine):
        self.engine = engine
        self.variables = {}
        self.functions = {}
        self._global_types = {}
        self._scopes = []
        self._counts = []
        self.max_steps = int(os.environ.get("OPENSTAT_MATA_MAX_STEPS", 1_000_000))
        self.max_cells = int(os.environ.get("OPENSTAT_MATA_MAX_CELLS", 10_000_000))
        self._steps = 0

    def _tick(self):
        self._steps += 1
        if self._steps > self.max_steps:
            raise MataError("Mata execution step limit exceeded")
        if getattr(self.engine, "_cancel", None) is not None and self.engine._cancel.is_set():
            raise MataError("Mata execution cancelled")

    @property
    def _scope(self):
        return self._scopes[-1] if self._scopes else _Scope(self.variables, self._global_types)

    def _get(self, name, scope=None):
        scope = scope or self._scope
        if name in scope.references:
            return self._get(scope.references[name][1], scope.references[name][0])
        if name not in scope.values:
            raise MataError(f"Mata variable {name} not found")
        return scope.values[name]

    def _set(self, name, value, scope=None):
        scope = scope or self._scope
        if name in scope.types:
            self._check_type(value, scope.types[name])
        if name in scope.references:
            self._set(scope.references[name][1], value, scope.references[name][0])
        else:
            scope.values[name] = value.copy() if isinstance(value, np.ndarray) else value

    @staticmethod
    def _check_type(value, spec):
        array = np.atleast_2d(value)
        element, shape = spec
        if element == "void":
            if array.size:
                raise MataError("Mata void function returned a value")
            return
        numeric = array.dtype.kind in "biufc"
        if (element in {"real", "numeric", "complex"} and not numeric
                or element == "real" and np.iscomplexobj(array)
                or element == "string" and array.dtype.kind not in "US"):
            raise MataError("Mata declaration type mismatch")
        rows, cols = array.shape
        if (shape == "scalar" and array.size != 1 or shape == "rowvector" and rows != 1
                or shape == "colvector" and cols != 1 or shape == "vector" and min(rows, cols) != 1):
            raise MataError("Mata declaration shape mismatch")

    @staticmethod
    def _empty(spec):
        element, shape = spec
        dtype, default = (str, "") if element == "string" else (complex if element == "complex" else float, np.nan)
        if shape == "scalar":
            return default
        dimensions = (1, 0) if shape in {"vector", "rowvector"} else (0, 1) if shape == "colvector" else (0, 0)
        return np.full(dimensions, default, dtype=dtype)

    @staticmethod
    def _scalar(value):
        if np.size(value) != 1:
            raise MataError("Mata scalar required")
        return np.asarray(value).item()

    def _truth(self, value):
        return self._scalar(value) != 0

    @staticmethod
    def invsym(value):
        value = np.atleast_2d(np.asarray(value, dtype=float))
        if value.ndim != 2 or value.shape[0] != value.shape[1] or not np.allclose(value, value.T):
            raise MataError("invsym() requires a symmetric square matrix")
        eigenvalues = np.linalg.eigvalsh(value)
        if not len(eigenvalues) or eigenvalues.min() <= np.finfo(float).eps * max(1., np.linalg.norm(value)) * value.shape[0]:
            raise MataError("singular or non-positive-definite invsym() is not implemented")
        return np.linalg.inv(value)

    def _binary(self, op, a, b):
        aa, bb = np.atleast_2d(a), np.atleast_2d(b)
        if op in {",", "\\"}:
            if (aa.dtype.kind in "US") != (bb.dtype.kind in "US"):
                raise MataError("Mata matrix element types differ")
            self._allocation(aa.size + bb.size)
            try:
                return np.concatenate((aa, bb), axis=1 if op == "," else 0)
            except ValueError as exc:
                raise MataError("Mata matrices not conformable") from exc
        if op in {"..", "::"}:
            start, end = float(self._scalar(a)), float(self._scalar(b))
            if np.isnan(start) or np.isnan(end):
                return np.nan
            count = int(abs(end - start)) + 1
            if count > self.max_steps:
                raise MataError("Mata range exceeds execution limit")
            vector = start + np.arange(count) * (1 if end >= start else -1)
            return vector.reshape(1, -1) if op == ".." else vector.reshape(-1, 1)
        if op == "#":
            self._allocation(aa.size * bb.size)
            return np.kron(aa, bb)
        if op in {"&", "&&", "|", "||"}:
            return float(self._truth(a) and self._truth(b)) if op in {"&", "&&"} else float(self._truth(a) or self._truth(b))
        colon = op.startswith(":")
        base = op[1:] if colon else op
        if colon:
            conformable = (aa.shape == bb.shape or aa.size == 1 or bb.size == 1
                or aa.shape[0] == bb.shape[0] and 1 in {aa.shape[1], bb.shape[1]}
                or aa.shape[1] == bb.shape[1] and 1 in {aa.shape[0], bb.shape[0]})
            if not conformable:
                raise MataError("Mata matrices not conformable")
        elif base in {"==", "!="}:
            same = aa.shape == bb.shape and (aa.dtype.kind in "US") == (bb.dtype.kind in "US")
            equal = same and np.array_equal(aa, bb, equal_nan=aa.dtype.kind not in "US")
            return float(equal if base == "==" else not equal)
        elif base in {"+", "-", ">", ">=", "<", "<="} and aa.shape != bb.shape:
            raise MataError("Mata matrices not conformable")
        elif base == "*":
            if aa.size == 1:
                return self._scalar(a) * b
            if bb.size == 1:
                return a * self._scalar(b)
            self._allocation(aa.shape[0] * bb.shape[1])
            try:
                return aa @ bb
            except ValueError as exc:
                raise MataError("Mata matrices not conformable") from exc
        elif base == "/" and bb.size != 1:
            raise MataError("Mata division requires a scalar denominator")
        elif base == "^" and (aa.size != 1 or bb.size != 1):
            raise MataError("Mata exponentiation requires scalar operands")
        operations = {"+": np.add, "-": np.subtract, "*": np.multiply, "/": np.divide, "^": np.power,
            "==": np.equal, "!=": np.not_equal, ">": np.greater, ">=": np.greater_equal,
            "<": np.less, "<=": np.less_equal, "&": np.logical_and, "|": np.logical_or}
        if base not in operations:
            raise MataError(f"unsupported Mata operator {op}")
        left, right = (aa, bb) if colon else (a, b)
        # Missing values compare greater than real numbers. Arithmetic keeps NaN.
        if base in {"==", "!=", ">", ">=", "<", "<="} and aa.dtype.kind in "biuf" and bb.dtype.kind in "biuf":
            left, right = np.where(np.isnan(left), np.inf, left), np.where(np.isnan(right), np.inf, right)
        with np.errstate(all="ignore"):
            result = operations[base](left, right)
        if base in {">", ">=", "<", "<="} and not colon:
            return float(np.all(result))
        if np.asarray(result).dtype.kind in "fc":
            result = np.where(np.isfinite(result), result, np.nan)
        return result.astype(float) if isinstance(result, np.ndarray) and result.dtype.kind == "b" else result

    def _indices(self, value, size):
        array = np.atleast_2d(value)
        if array.size == 1 and np.isnan(self._scalar(array)):
            return np.arange(size)
        if min(array.shape) > 1 or not np.all(np.isfinite(array)) or np.any(array != np.floor(array)) or np.any(array < 1) or np.any(array > size):
            raise MataError("Mata subscript out of range")
        return array.astype(int).ravel() - 1

    def _subscripts(self, node, array):
        if node[0] == "range_subscript":
            limits = np.atleast_2d(self._eval(node[2]))
            if limits.shape in {(1, 1), (1, 2)}:
                values = [x for x in limits.ravel()]
            elif limits.shape == (2, 2):
                values = []
                for column, size in enumerate(array.shape):
                    start, end = limits[:, column]
                    end = size if np.isnan(end) else end
                    if not np.isfinite(start) or end < start:
                        raise MataError("Mata subscript out of range")
                    values.append(np.arange(start, end + 1).reshape(1, -1))
            elif limits.shape == (2, 1) and min(array.shape) == 1:
                start, end = limits.ravel()
                end = array.size if np.isnan(end) else end
                if not np.isfinite(start) or end < start:
                    raise MataError("Mata subscript out of range")
                values = [np.arange(start, end + 1).reshape(1, -1)]
            else:
                raise MataError("invalid Mata range subscript")
        else:
            values = [self._eval(index) for index in node[2]]
        if len(values) == 1:
            if min(array.shape) > 1:
                raise MataError("matrix requires row and column subscripts")
            index = self._indices(values[0], array.size)
            return (np.array([0]), index) if array.shape[0] == 1 else (index, np.array([0]))
        return tuple(self._indices(value, size) for value, size in zip(values, array.shape))

    def _assign(self, node, value):
        if node[0] == "variable":
            self._set(node[1], value)
        elif node[0] in {"subscript", "range_subscript"} and node[1][0] == "variable":
            name = node[1][1]
            array = np.atleast_2d(self._get(name)).copy()
            rows, columns = self._subscripts(node, array)
            replacement = np.atleast_2d(value)
            if replacement.size != 1 and replacement.shape != (len(rows), len(columns)):
                raise MataError("Mata matrices not conformable")
            if (array.dtype.kind in "US") != (replacement.dtype.kind in "US"):
                raise MataError("Mata matrix element types differ")
            array = array.astype(np.result_type(array.dtype, replacement.dtype))
            array[np.ix_(rows, columns)] = replacement
            self._set(name, array)
        else:
            raise MataError("invalid Mata assignment target")
        return value

    def _eval(self, node):
        self._tick()
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "variable":
            return self._get(node[1])
        if kind == "assign":
            return self._assign(node[1], self._eval(node[2]))
        if kind == "conditional":
            return self._eval(node[2] if self._truth(self._eval(node[1])) else node[3])
        if kind == "void":
            self._eval(node[1])
            return None
        if kind == "binary":
            return self._binary(node[1], self._eval(node[2]), self._eval(node[3]))
        if kind in {"unary", "postfix"}:
            op, operand = node[1], node[2]
            value = self._eval(operand)
            if op in {"++", "--"}:
                updated = self._scalar(value) + (1 if op == "++" else -1)
                self._assign(operand, updated)
                return value if kind == "postfix" else updated
            if op == "'":
                return np.atleast_2d(value).conj().T
            if op == "!":
                return np.logical_not(value).astype(float)
            return -value if op == "-" else value
        if kind in {"subscript", "range_subscript"}:
            array = np.atleast_2d(self._eval(node[1]))
            rows, columns = self._subscripts(node, array)
            result = array[np.ix_(rows, columns)]
            return result.item() if result.size == 1 else result.copy()
        if kind == "call":
            return self._call(node[1], node[2])
        raise MataError(f"unsupported Mata expression: {kind}")

    def _call(self, name, nodes):
        if name not in self.functions:
            return self._builtin(name, [self._eval(node) for node in nodes])
        if len(self._scopes) >= 64:
            raise MataError("Mata function recursion limit exceeded")
        spec, parameters, body = self.functions[name]
        self._validate_calls(body)
        minimum = sum(not optional for _, _, optional in parameters)
        if not minimum <= len(nodes) <= len(parameters):
            raise MataError(f"Mata function {name}() argument count mismatch")
        caller = self._scope
        arguments = [self._eval(node) for node in nodes]
        frame = _Scope({})
        for index, (parameter, parameter_type, optional) in enumerate(parameters):
            value = arguments[index] if index < len(arguments) else self._empty(parameter_type)
            self._check_type(value, parameter_type)
            frame.values[parameter] = value
            frame.types[parameter] = parameter_type
            if index < len(nodes):
                actual = nodes[index][1] if nodes[index][0] == "assign" else nodes[index]
                if actual[0] == "variable":
                    frame.references[parameter] = (caller, actual[1])
        self._scopes.append(frame)
        self._counts.append(len(nodes))
        try:
            try:
                self._execute(body)
                value = np.empty((0, 0))
            except _Return as returned:
                value = returned.value
            except (_Break, _Continue) as exc:
                raise MataError("Mata break or continue outside a loop") from exc
            self._check_type(value, spec)
            return value
        finally:
            self._counts.pop()
            self._scopes.pop()

    def _execute(self, statement):
        self._tick()
        kind = statement[0]
        if kind == "block":
            for child in statement[1]:
                self._execute(child)
        elif kind == "function":
            if self._scopes:
                raise MataError("nested Mata function definitions are not supported")
            self.functions[statement[1]] = statement[2:]
        elif kind == "declaration":
            if not self._scopes:
                raise MataError("Mata declarations require a function body")
            spec, names, external = statement[1:]
            for name in names:
                if external:
                    if name not in self.variables:
                        self.variables[name] = self._empty(spec)
                    self._check_type(self.variables[name], spec)
                    self._scope.references[name] = (_Scope(self.variables, self._global_types), name)
                else:
                    self._scope.values[name] = self._empty(spec)
                self._scope.types[name] = spec
        elif kind == "expression":
            self._validate_calls(statement[1])
            value = self._eval(statement[1])
            if statement[1][0] not in {"assign", "void"} and value is not None and np.size(value):
                self.engine.emit(str(value))
        elif kind == "if":
            selected = statement[2] if self._truth(self._eval(statement[1])) else statement[3]
            if selected is not None:
                self._execute(selected)
        elif kind in {"while", "for", "do"}:
            if kind == "for":
                if statement[1] is not None:
                    self._eval(statement[1])
                condition, increment, body = statement[2:]
            else:
                condition, body = statement[1:]
                increment = None
            first = True
            while kind == "do" and first or self._truth(self._eval(condition)):
                first = False
                self._tick()
                try:
                    self._execute(body)
                except _Continue:
                    pass
                except _Break:
                    break
                if increment is not None:
                    self._eval(increment)
        elif kind == "return":
            if not self._scopes:
                raise MataError("Mata return outside a function")
            raise _Return(self._eval(statement[1]) if statement[1] is not None else np.empty((0, 0)))
        elif kind == "break":
            raise _Break()
        elif kind == "continue":
            raise _Continue()
        elif kind == "version":
            # Syntax is accepted; historical numerical behavior is unverified.
            if not 9 <= statement[1] <= 19.5:
                raise MataError("unsupported Mata version")
        else:
            raise MataError(f"unsupported Mata statement: {kind}")

    def _validate_calls(self, node):
        """Link call names before executing a source expression/function body."""
        if not isinstance(node, (tuple, list)):
            return
        structured = bool(node) and isinstance(node[0], str)
        if structured and node[0] == "function":
            return
        if structured and node[0] == "call" and node[1] not in self.functions:
            self._builtin(node[1], [], validate_only=True)
        for child in node[1:] if structured else node:
            self._validate_calls(child)

    def _integer(self, value):
        value = self._scalar(value)
        if not np.isfinite(value) or value != int(value) or value < 0:
            raise MataError("Mata dimension requires a nonnegative integer")
        return int(value)

    def _allocation(self, cells):
        if cells > self.max_cells:
            raise MataError("Mata matrix exceeds allocation limit")

    def _identity(self, value):
        size = self._integer(value)
        self._allocation(size * size)
        return np.eye(size)

    def _fill(self, rows, columns, value):
        rows, columns = self._integer(rows), self._integer(columns)
        self._allocation(rows * columns)
        return np.full((rows, columns), self._scalar(value))

    def _builtin(self, name, args, validate_only=False):
        matrix = lambda value: np.atleast_2d(value)
        functions = {
            "I": self._identity,
            "J": self._fill,
            "rows": lambda a: matrix(a).shape[0], "cols": lambda a: matrix(a).shape[1],
            "length": lambda a: matrix(a).size,
            "transpose": lambda a: matrix(a).conj().T, "transposeonly": lambda a: matrix(a).T,
            "conj": np.conj, "invsym": self.invsym, "luinv": lambda a: np.linalg.inv(matrix(a)),
            "lusolve": np.linalg.solve, "qrsolve": lambda a, b: np.linalg.lstsq(a, b, rcond=None)[0],
            "cholesky": np.linalg.cholesky, "det": lambda a: np.linalg.det(matrix(a)),
            "trace": lambda a: np.trace(matrix(a)), "rank": lambda a: np.linalg.matrix_rank(matrix(a)),
            "diagonal": lambda a: np.diag(matrix(a)).reshape(-1, 1),
            "diag": lambda a: np.diag(np.asarray(a).ravel()),
            "sqrt": np.sqrt, "exp": np.exp, "ln": np.log, "log": np.log,
            "log10": np.log10, "abs": np.abs, "sign": np.sign, "floor": np.floor,
            "ceil": np.ceil, "trunc": np.trunc, "sin": np.sin, "cos": np.cos,
            "tan": np.tan, "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
            "sum": lambda a, missing=0: (np.sum if self._truth(missing) else np.nansum)(a),
            "min": lambda a: np.nanmin(a) if np.size(a) else np.nan,
            "max": lambda a: np.nanmax(a) if np.size(a) else np.nan,
            "mean": lambda *a: self._moments(*a)[0],
            "variance": lambda *a: self._moments(*a)[1],
            "meanvariance": lambda *a: np.vstack(self._moments(*a)),
            "colsum": lambda a, missing=0: (np.sum if self._truth(missing) else np.nansum)(matrix(a), axis=0, keepdims=True),
            "rowsum": lambda a, missing=0: (np.sum if self._truth(missing) else np.nansum)(matrix(a), axis=1, keepdims=True),
            "colmin": lambda a: np.min(matrix(a), axis=0, keepdims=True),
            "rowmin": lambda a: np.min(matrix(a), axis=1, keepdims=True),
            "colmax": lambda a: np.max(matrix(a), axis=0, keepdims=True),
            "rowmax": lambda a: np.max(matrix(a), axis=1, keepdims=True),
            "cross": self._cross,
            "vec": lambda a: matrix(a).reshape(-1, 1, order="F"),
            "rowshape": lambda a, n: matrix(a).reshape(self._integer(n), -1),
            "colshape": lambda a, n: matrix(a).reshape(-1, self._integer(n)),
            "select": self._select,
            "selectindex": self._selectindex,
            "any": lambda a: float(np.any(a)), "all": lambda a: float(np.all(a)),
            "missing": lambda a: float(np.count_nonzero(np.isnan(a))),
            "nonmissing": lambda a: float(np.count_nonzero(~np.isnan(a))),
            "rowmissing": lambda a: np.sum(np.isnan(matrix(a)), axis=1, keepdims=True),
            "colmissing": lambda a: np.sum(np.isnan(matrix(a)), axis=0, keepdims=True),
            "rownonmissing": lambda a: np.sum(~np.isnan(matrix(a)), axis=1, keepdims=True),
            "colnonmissing": lambda a: np.sum(~np.isnan(matrix(a)), axis=0, keepdims=True),
            "hasmissing": lambda a: float(np.any(np.isnan(a))),
            "st_matrix": self.st_matrix, "st_numscalar": self.st_numscalar,
            "st_data": self.st_data, "st_store": self.st_store,
            "st_nobs": lambda: len(self.engine.data), "st_nvar": lambda: len(self.engine.data.columns),
            "st_local": lambda *a: self._macro(self.engine.locals, *a),
            "st_global": lambda *a: self._macro(self.engine.globals, *a),
            "tokens": lambda a: np.asarray(str(a).split()).reshape(1, -1),
            "invtokens": lambda a, sep=" ": str(sep).join(np.asarray(a).ravel()),
            "strlen": lambda a: np.vectorize(len, otypes=[float])(a),
            "strupper": lambda a: np.char.upper(a), "strlower": lambda a: np.char.lower(a),
            "strtrim": lambda a: np.char.strip(a),
            "args": lambda: self._counts[-1] if self._counts else 0,
        }
        if name not in functions:
            raise MataError(f"Mata function {name}() not implemented")
        if validate_only:
            return
        with np.errstate(all="ignore"):
            value = functions[name](*args)
        if isinstance(value, (np.ndarray, np.generic, float, complex)) and np.asarray(value).dtype.kind in "fc":
            value = np.where(np.isfinite(value), value, np.nan)
        return value

    def _moments(self, value, weight=1):
        data = np.atleast_2d(np.asarray(value, dtype=float))
        weights = np.atleast_2d(np.asarray(weight, dtype=float))
        if weights.size == 1:
            weights = np.full((len(data), 1), weights.item())
        if weights.shape != (len(data), 1):
            raise MataError("Mata matrices not conformable")
        keep = ~np.isnan(data).any(axis=1) & ~np.isnan(weights).ravel()
        data, weights = data[keep], weights[keep, 0]
        if np.any(weights < 0):
            raise MataError("Mata weights must be nonnegative")
        total = math.fsum(weights)
        if not len(data) or not total:
            return np.full((1, np.atleast_2d(value).shape[1]), np.nan), np.full((np.atleast_2d(value).shape[1],) * 2, np.nan)
        means = np.asarray([math.fsum(data[:, j] * weights) / total for j in range(data.shape[1])]).reshape(1, -1)
        centered = data - means
        covariance = centered.T @ (centered * weights[:, None]) / (total - 1) if total > 1 else np.full((data.shape[1],) * 2, np.nan)
        return means, covariance

    def _cross(self, *args):
        if len(args) == 2:
            left, right = args
            weight, left_constant, right_constant = 1, 0, 0
        elif len(args) == 3:
            left, weight, right = args
            left_constant, right_constant = 0, 0
        elif len(args) == 4:
            left, left_constant, right, right_constant = args
            weight = 1
        elif len(args) == 5:
            left, left_constant, weight, right, right_constant = args
        else:
            raise MataError("Mata cross() argument count mismatch")
        left, right, weights = np.atleast_2d(left), np.atleast_2d(right), np.atleast_2d(weight)
        rows = max(len(left), len(right), weights.size if min(weights.shape) == 1 else len(weights))
        if left.size == 1:
            left = np.ones((rows, 1))
        if right.size == 1:
            right = np.ones((rows, 1))
        if weights.size == 1:
            weights = np.full((rows, 1), weights.item())
        else:
            weights = weights.reshape(-1, 1)
        if len(left) != rows or len(right) != rows or len(weights) != rows:
            raise MataError("Mata matrices not conformable")
        if self._truth(left_constant):
            left = np.column_stack((left, np.ones(rows)))
        if self._truth(right_constant):
            right = np.column_stack((right, np.ones(rows)))
        keep = ~np.isnan(left).any(axis=1) & ~np.isnan(right).any(axis=1) & ~np.isnan(weights).ravel()
        return left[keep].T @ (right[keep] * weights[keep])

    def _select(self, value, selection):
        array, selector = np.atleast_2d(value), np.atleast_2d(selection)
        if min(selector.shape) != 1:
            raise MataError("Mata selection requires a vector")
        mask = selector.ravel() != 0
        if selector.shape[1] == 1 and len(mask) == array.shape[0]:
            return array[mask, :]
        if selector.shape[0] == 1 and len(mask) == array.shape[1]:
            return array[:, mask]
        raise MataError("Mata matrices not conformable")

    @staticmethod
    def _selectindex(selection):
        array = np.atleast_2d(selection)
        if min(array.shape) != 1:
            raise MataError("Mata selection requires a vector")
        result = np.flatnonzero(array.ravel()) + 1
        return result.reshape(1, -1) if array.shape[0] == 1 else result.reshape(-1, 1)

    @staticmethod
    def _macro(mapping, name, value=None):
        if value is None:
            return mapping.get(name, "")
        mapping[name] = str(value)

    def st_matrix(self, name, value=None):
        if value is None:
            return self.engine.matrices[name].copy()
        self.engine.matrices[name] = np.atleast_2d(value).copy()

    def st_numscalar(self, name, value=None):
        if value is None:
            return self.engine.scalars[name]
        self.engine.scalars[name] = float(self._scalar(value))

    def st_data(self, obs, varlist):
        variables = str(varlist).split() if isinstance(varlist, str) else [str(x) for x in np.asarray(varlist).ravel()]
        indices = self._indices(obs, len(self.engine.data))
        return self.engine.data.iloc[indices][variables].to_numpy(dtype=float)

    def st_store(self, obs, varlist, value):
        variables = str(varlist).split() if isinstance(varlist, str) else [str(x) for x in np.asarray(varlist).ravel()]
        indices = self._indices(obs, len(self.engine.data))
        columns = [self.engine.data.columns.get_loc(name) for name in variables]
        value = np.atleast_2d(value)
        if value.size != 1 and value.shape != (len(indices), len(columns)):
            raise MataError("Mata matrices not conformable")
        self.engine.data.iloc[indices, columns] = value

    def evaluate(self, text):
        parser = _Parser(text)
        self._steps = 0
        try:
            node = parser.expression(stop={"\n", ";"})
            parser.separators()
            if parser.token != "":
                raise MataError(f"unexpected Mata token: {parser.token}")
            self._validate_calls(node)
            return self._eval(node)
        except (TypeError, KeyError, IndexError, ValueError, OverflowError, np.linalg.LinAlgError) as exc:
            if isinstance(exc, MataError):
                raise
            raise MataError(f"invalid Mata expression: {exc}") from exc
        except RecursionError as exc:
            raise MataError("Mata parser nesting limit exceeded") from exc

    def run(self, source):
        parser = _Parser(source)
        self._steps = 0
        try:
            statements = parser.program()
            for statement in statements:
                self._execute(statement)
        except (_Break, _Continue) as exc:
            raise MataError("Mata break or continue outside a loop") from exc
        except (TypeError, KeyError, IndexError, ValueError, OverflowError, np.linalg.LinAlgError) as exc:
            if isinstance(exc, MataError):
                raise
            raise MataError(f"invalid Mata statement: {exc}") from exc
        except RecursionError as exc:
            raise MataError("Mata parser nesting limit exceeded") from exc
