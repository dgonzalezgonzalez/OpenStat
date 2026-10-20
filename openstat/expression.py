"""Stata expression evaluator.

The evaluator walks the AST produced by :mod:`openstat.parser_expr`; no
Python expression is ever evaluated.  Missing numeric data are NaN in
memory and sort above every finite number, matching Stata.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from scipy import special, stats

from .formats import format_value
from .parser_expr import (Binary, Call, Literal, Name, StringLiteral, Subscript,
                          Unary, parse_expression)


class ExpressionError(ValueError):
    """A malformed expression or an unusable function argument."""


class NotFoundError(ExpressionError):
    """A name that Stata cannot resolve: reported as ``r(111)``."""


def _missing(value):
    return pd.isna(value)


def _ordered(value):
    """Order missing values above every finite number, as Stata does."""
    if isinstance(value, (str, bytes)):
        return value
    try:
        return np.where(pd.isna(value), np.inf, value)
    except TypeError:
        return value


def truth(value):
    """Stata treats missing as true in a logical context."""
    if isinstance(value, pd.Series):
        return value.fillna(1).astype(bool)
    if isinstance(value, np.ndarray):
        return np.where(pd.isna(value), True, value).astype(bool)
    return True if pd.isna(value) else bool(value)


def _as_text(value) -> str:
    if isinstance(value, pd.Series):
        return value.astype(str)
    return str(value)


class Evaluator:
    def __init__(self, engine):
        self.engine = engine

    # ------------------------------------------------------------------
    # Running helpers
    # ------------------------------------------------------------------
    def running_sum(self, value):
        """Stata's ``sum(x)``: the running total, missing values as zero.

        Observations excluded by an ``if``/``in`` qualifier contribute
        nothing to the running total and keep a missing result, exactly as
        Stata reports ("(n missing values generated)").
        """
        series = pd.Series(value, index=self.engine.data.index)
        mask = getattr(self.engine, "_assignment_mask", None)
        if mask is None:
            return series.fillna(0).cumsum()
        return series.where(mask, 0).fillna(0).cumsum().where(mask)

    @staticmethod
    def element(value, row, column):
        array = np.atleast_2d(value)
        if row != int(row) or column != int(column):
            return np.nan
        row, column = int(row) - 1, int(column) - 1
        if 0 <= row < array.shape[0] and 0 <= column < array.shape[1]:
            return array[row, column]
        return np.nan

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def evaluate(self, expression: str):
        text = expression.strip()
        if not text:
            raise ExpressionError("empty expression")
        text = self._rewrite(text)
        try:
            tree = parse_expression(text)
        except ExpressionError:
            raise
        except Exception as error:  # pragma: no cover - defensive
            raise ExpressionError(f"unsupported expression: {expression}") from error
        with np.errstate(all="ignore"):
            return self.visit(tree)

    def _rewrite(self, text: str) -> str:
        """Translate Stata-only spellings into the parser's vocabulary."""
        strings: list[str] = []

        def protect(match):
            strings.append(match[0])
            return f"__string_{len(strings) - 1}"

        text = re.sub(r'"[^"\n]*"', protect, text)
        text = re.sub(r"\b(r|e|c)\(([A-Za-z_][\w]*)\)", r'__result("\1","\2")', text)
        text = re.sub(r"_(b|se)\[([\w.]+)\]", r'__coef("\1","\2")', text)
        text = text.replace("~=", "!=")
        text = text.replace("&&", "&").replace("||", "|")
        text = text.replace("^", "**") if False else text
        text = re.sub(r"(?<![\w.])\.(?![\w.])", "__missing", text)
        self.strings = strings
        return text

    # ------------------------------------------------------------------
    # Tree walking
    # ------------------------------------------------------------------
    def visit(self, node):
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, Name):
            return self.resolve_name(node.identifier)
        if isinstance(node, Unary):
            return self.unary(node)
        if isinstance(node, Binary):
            return self.binary(node)
        if isinstance(node, Subscript):
            return self.subscript(node)
        if isinstance(node, Call):
            return self.call(node)
        raise ExpressionError(f"unsupported expression construct: {type(node).__name__}")

    def resolve_name(self, name):
        e = self.engine
        if name.startswith("__string_"):
            return self.strings[int(name.rsplit("_", 1)[1])][1:-1]
        if name == "__missing":
            return np.nan
        if name == "_n":
            if getattr(e, "_observation", None) is not None:
                return e._observation + 1
            return pd.Series(np.arange(1, len(e.data) + 1), index=e.data.index)
        if name == "_N":
            return len(e.data)
        if name == "_rc":
            return e.rc
        if name == "_pi":
            return np.pi
        if name == "_e":
            return getattr(e, "_observation", None) is not None
        if name == "_b" or name == "_se":
            raise ExpressionError(f"{name} requires a coefficient subscript")
        if name in e.data:
            if getattr(e, "_observation", None) is not None:
                return e.data[name].iloc[e._observation]
            return e.data[name]
        if name in e.scalars:
            return e.scalars[name]
        if name in e.matrices:
            return e.matrices[name]
        raise NotFoundError(f"{name} not found")

    def unary(self, node):
        value = self.visit(node.operand)
        if node.operator == "-":
            return -value
        if node.operator == "+":
            return +value
        if node.operator in {"!", "~"}:
            return np.logical_not(truth(value))
        raise ExpressionError("unsupported unary operator")

    def binary(self, node):
        operator = node.operator
        left, right = self.visit(node.left), self.visit(node.right)
        if operator == "&":
            return np.logical_and(truth(left), truth(right))
        if operator == "|":
            return np.logical_or(truth(left), truth(right))
        if operator == "==":
            return self.compare(operator, left, right)
        if operator == "!=":
            return self.compare(operator, left, right)
        if operator in {"<", ">", "<=", ">="}:
            return self.compare(operator, left, right)
        if operator == "+":
            if isinstance(left, str) or isinstance(right, str):
                return _as_text(left) + _as_text(right)
            return left + right
        if operator == "-":
            return left - right
        if operator == "*":
            return left * right
        if operator == "/":
            try:
                return np.true_divide(left, right)
            except (ZeroDivisionError, TypeError):
                return np.nan
        if operator == "^":
            return np.power(left, right)
        raise ExpressionError(f"unsupported operator {operator}")

    def compare(self, operator, left, right):
        a, b = _ordered(left), _ordered(right)
        if operator == "==":
            return np.equal(a, b)
        if operator == "!=":
            return np.not_equal(a, b)
        if operator == "<":
            return np.less(a, b)
        if operator == "<=":
            return np.less_equal(a, b)
        if operator == ">":
            return np.greater(a, b)
        if operator == ">=":
            return np.greater_equal(a, b)
        raise ExpressionError("unsupported comparison")

    def subscript(self, node):
        e = self.engine
        if isinstance(node.target, Name) and node.target.identifier in e.data:
            array = e.data[node.target.identifier]
        else:
            array = self.visit(node.target)
        index = self.visit(node.indices[0]) if node.indices else None
        if isinstance(array, pd.Series):
            if np.isscalar(index):
                try:
                    position = int(index)
                except (TypeError, ValueError):
                    return np.nan
                return array.iloc[position - 1] if 1 <= position <= len(array) else np.nan
            positions = np.asarray(index, dtype=int) - 1
            return pd.Series([array.iloc[i] if 0 <= i < len(array) else np.nan
                              for i in positions], index=e.data.index)
        if isinstance(index, tuple):
            return array[tuple(int(i) - 1 for i in index)]
        if len(node.indices) > 1:
            extra = [int(self.visit(item)) - 1 for item in node.indices[1:]]
            return array[(int(index) - 1,) + tuple(extra)]
        return array[int(index) - 1]

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------
    def call(self, node):
        e = self.engine
        name = node.function
        args = [self.visit(argument) for argument in node.arguments]
        if name == "__result":
            return self.result(*args)
        if name == "__coef":
            return self.coefficient(*args)
        function = self.functions().get(name)
        if function is None:
            raise ExpressionError(f"function {name}() not implemented")
        return function(*args)

    def result(self, group, key):
        e = self.engine
        groups = {
            "r": e.returns,
            "e": e.estimates,
            "c": {"N": len(e.data), "k": len(e.data.columns), "pwd": str(e.cwd),
                  "version": 19.5, "SE": 1, "rc": e.rc},
        }
        if group not in groups:
            raise ExpressionError(f"unknown result class {group}()")
        if key not in groups[group]:
            raise NotFoundError(f"{group}({key}) not found")
        return groups[group][key]

    def coefficient(self, kind, name):
        e = self.engine
        if name not in e.coefficients:
            raise NotFoundError(f"_b[{name}] not found")
        index = 0 if kind == "b" else 1
        return e.coefficients[name][index]

    def functions(self):
        e = self.engine
        return {
            # Numeric
            "abs": np.abs, "sqrt": np.sqrt, "exp": np.exp, "ln": np.log, "log": np.log,
            "log10": np.log10, "sin": np.sin, "cos": np.cos, "tan": np.tan,
            "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
            "atan2": np.arctan2, "sinh": np.sinh, "cosh": np.cosh, "tanh": np.tanh,
            "floor": np.floor, "ceil": np.ceil, "int": np.trunc, "sign": np.sign,
            "mod": np.mod, "min": self.minimum, "max": self.maximum,
            "mi": self.missing,
            "round": lambda x, unit=1: np.floor(x / unit + .5) * unit,
            "trunc": np.trunc,
            "cond": self.conditional,
            "sum": self.running_sum,
            "missing": self.missing,
            "inrange": self.inrange,
            "inlist": self.inlist,
            "normal": special.ndtr, "normalden": stats.norm.pdf, "invnormal": special.ndtri,
            "ttail": lambda df, x: stats.t.sf(x, df), "invttail": lambda df, p: stats.t.isf(p, df),
            "chi2tail": lambda df, x: stats.chi2.sf(x, df),
            "invchi2tail": lambda df, p: stats.chi2.isf(p, df),
            "Ftail": lambda df1, df2, x: stats.f.sf(x, df1, df2),
            "invFtail": lambda df1, df2, p: stats.f.isf(p, df1, df2),
            "invlogit": special.expit, "logit": special.logit,
            "runiform": lambda *a: e.rng.uniform(*(a or [0, 1]), size=len(e.data)),
            "rnormal": lambda mu=0, sd=1: e.rng.normal(mu, sd, size=len(e.data)),
            "rpoisson": lambda mu: e.rng.poisson(mu, size=len(e.data)),
            "rbinomial": lambda n, p: e.rng.binomial(n, p, size=len(e.data)),
            "runiformint": lambda a, b: e.rng.integers(a, b + 1, size=len(e.data)),
            "rgamma": lambda a, b: e.rng.gamma(a, b, size=len(e.data)),
            "rbeta": lambda a, b: e.rng.beta(a, b, size=len(e.data)),
            "rexp": lambda a: e.rng.exponential(a, size=len(e.data)),
            "invbinomial": lambda n, p: stats.binom.ppf(p, n),
            "binomial": lambda n, p: stats.binom.pmf(n, p),
            "binomialp": lambda n, p, x: stats.binom.sf(x - 1, n, p),
            "poissonp": lambda mu, x: stats.poisson.sf(x - 1, mu),
            "nbinomialp": lambda n, p, x: stats.nbinom.sf(x - 1, n, p),
            "chi2p": lambda df, x: stats.chi2.sf(x, df),
            "tp": lambda df, t: 2 * stats.t.sf(abs(t), df),
            "Fp": lambda df1, df2, x: stats.f.sf(x, df1, df2),
            "ttail2": lambda df, x: 2 * stats.t.sf(abs(x), df),
            "corr": lambda x, y: np.corrcoef(np.asarray(x, float), np.asarray(y, float))[0, 1],
            "cov": lambda x, y: np.cov(np.asarray(x, float), np.asarray(y, float))[0, 1],
            "var": lambda x: np.var(np.asarray(x, float), ddof=1),
            "variance": lambda x: np.var(np.asarray(x, float), ddof=1),
            "sd": lambda x: np.std(np.asarray(x, float), ddof=1),
            "mean": lambda x: np.mean(np.asarray(x, float)),
            "skewness": lambda x: stats.skew(np.asarray(x, float), bias=True),
            "kurtosis": lambda x: stats.kurtosis(np.asarray(x, float), bias=True, fisher=False),
            # Text
            "real": lambda x: pd.to_numeric(x, errors="coerce"),
            "string": self.string_function,
            "strlen": self.strlen,
            "lower": lambda x: x.str.lower() if isinstance(x, pd.Series) else str(x).lower(),
            "upper": lambda x: x.str.upper() if isinstance(x, pd.Series) else str(x).upper(),
            "proper": lambda x: x.str.title() if isinstance(x, pd.Series) else str(x).title(),
            "trim": lambda x: x.str.strip() if isinstance(x, pd.Series) else str(x).strip(),
            "ltrim": lambda x: x.str.lstrip() if isinstance(x, pd.Series) else str(x).lstrip(),
            "rtrim": lambda x: x.str.rstrip() if isinstance(x, pd.Series) else str(x).rstrip(),
            "substr": self.substr,
            "word": self.word,
            "wordcount": lambda x: len(str(x).split()),
            "strpos": lambda x, y: str(x).find(str(y)) + 1,
            "subinstr": lambda x, a, b: str(x).replace(str(a), str(b)),
            "regexm": lambda x, y: int(bool(re.search(y, str(x)))),
            "regexr": lambda x, y: re.sub(y, "", str(x)),
            "regexs": lambda x, y: (re.search(y, str(x)) or [None])[0] or "",
            "abbrev": lambda x, n: str(x)[:max(int(n), 1)],
            "plural": lambda n, s: s if int(n) == 1 else s + "s",
            "pluralize": lambda n, s: s if int(n) == 1 else s + "s",
            "reverse": lambda x: str(x)[::-1],
            "itrim": lambda x: " ".join(str(x).split()),
            "ascii": lambda x: ord(str(x)[0]) if str(x) else 0,
            "char": lambda x: chr(int(x)),
            "ustrlen": lambda x: len(str(x)),
            "ustrpos": lambda x, y: str(x).find(str(y)) + 1,
            "usubstr": self.substr,
            "uupper": lambda x: x.str.upper() if isinstance(x, pd.Series) else str(x).upper(),
            "ulower": lambda x: x.str.lower() if isinstance(x, pd.Series) else str(x).lower(),
            "utrim": lambda x: x.str.strip() if isinstance(x, pd.Series) else str(x).strip(),
            # Matrices and data
            "rowsof": lambda x: np.atleast_2d(x).shape[0],
            "colsof": lambda x: np.atleast_2d(x).shape[1],
            "el": self.element,
            "J": lambda r, c, x: np.full((int(r), int(c)), x),
        }

    @staticmethod
    def minimum(*args):
        """Stata's ``min()``: the smaller of two values, missing excepted.

        ``min(x, .)`` ignores the missing value, while ``max(x, .)``
        returns it, because Stata orders missing above every number.
        """
        if len(args) == 1:
            raise ExpressionError("invalid syntax")
        return np.fmin(*args) if len(args) == 2 else np.fmin(np.fmin(args[0], args[1]),
                                                            *args[2:])

    @staticmethod
    def maximum(*args):
        """Stata's ``max()``: the larger of two values, missing excepted."""
        if len(args) == 1:
            raise ExpressionError("invalid syntax")
        return np.fmax(*args) if len(args) == 2 else np.fmax(np.fmax(args[0], args[1]),
                                                            *args[2:])

    @staticmethod
    def conditional(condition, *branches):
        """Stata's ``cond()``.

        With two branches a missing condition selects the second one.
        With three branches a true condition selects the first, a false
        one the second and a missing one the third.
        """
        if len(branches) == 2:
            return np.where(truth(condition), branches[0], branches[1])
        if len(branches) == 3:
            missing = pd.isna(condition)
            return np.where(missing, branches[2], np.where(truth(condition),
                                                            branches[0], branches[1]))
        raise ExpressionError("invalid syntax")

    def missing(self, *args):
        if not args:
            raise ExpressionError("missing() requires arguments")
        result = pd.isna(args[0])
        for value in args[1:]:
            result = result | pd.isna(value) | (np.asarray(value, dtype=object) == "")
        return result

    def inrange(self, x, low, high):
        low = np.where(pd.isna(low), -np.inf, low)
        high = np.where(pd.isna(high), np.inf, high)
        present = np.logical_not(pd.isna(x))
        return present & (np.asarray(x) >= low) & (np.asarray(x) <= high)

    def inlist(self, x, *values):
        if not values:
            return False
        result = np.asarray(x) == values[0]
        for value in values[1:]:
            result = result | (np.asarray(x) == value)
        return result

    def string_function(self, x, fmt="%12.0g"):
        if isinstance(x, pd.Series):
            return x.map(lambda value: format_value(value, fmt))
        return format_value(x, fmt)

    def strlen(self, x):
        if isinstance(x, pd.Series):
            return x.astype(str).str.len()
        return len(str(x))

    def substr(self, x, start, length):
        start = int(start)
        length = int(length)
        if isinstance(x, pd.Series):
            return x.astype(str).str.slice(max(start - 1, 0), max(start - 1, 0) + max(length, 0))
        text = str(x)
        return text[max(start - 1, 0):max(start - 1, 0) + max(length, 0)]

    def word(self, x, index):
        words = str(x).split()
        position = int(index)
        if 1 <= position <= len(words):
            return words[position - 1]
        return ""
