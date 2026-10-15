"""Restricted AST evaluator for the implemented Stata expression subset.

Missing numeric data are NaN in memory, ordered above finite numbers in
comparisons as in Stata. No eval(), attribute access or arbitrary Python calls.
"""
from __future__ import annotations

import ast
import operator
import re

import numpy as np
import pandas as pd
from scipy import special, stats


class ExpressionError(ValueError):
    pass


def _missing(value):
    return pd.isna(value)


def _ordered(value):
    if isinstance(value, (str, bytes)):
        return value
    try:
        return np.where(pd.isna(value), np.inf, value)
    except TypeError:
        return value


def truth(value):
    if isinstance(value, pd.Series):
        return value.fillna(1).astype(bool)
    if isinstance(value, np.ndarray):
        return np.where(pd.isna(value), True, value).astype(bool)
    return True if pd.isna(value) else bool(value)


class Evaluator:
    def __init__(self, engine):
        self.engine = engine

    def running_sum(self,value):
        series=pd.Series(value,index=self.engine.data.index)
        mask=getattr(self.engine,"_assignment_mask",None)
        if mask is not None:series=series.where(mask,0)
        return series.fillna(0).cumsum().to_numpy()

    @staticmethod
    def element(value,row,column):
        array=np.atleast_2d(value)
        if row!=int(row) or column!=int(column):return np.nan
        row,column=int(row)-1,int(column)-1
        return array[row,column] if 0<=row<array.shape[0] and 0<=column<array.shape[1] else np.nan

    def evaluate(self, expression: str):
        expression = expression.strip()
        # Protect all string literals before translating operators and names.
        strings = []
        def protect(m):
            strings.append(m.group())
            return f"__string_{len(strings)-1}"
        text = re.sub(r'"[^"\n]*"', protect, expression)
        text = re.sub(r'\b(r|e|c)\(([A-Za-z_][\w]*)\)', r'__result("\1","\2")', text)
        text = re.sub(r'_(b|se)\[([\w.]+)\]', r'__coef("\1","\2")', text)
        text = text.replace("~=" , "!=").replace("!=" , " != ")
        text = text.replace("^", "**").replace("&", " and ").replace("|", " or ")
        text = re.sub(r'!(?!=)|~(?!=)', "~", text)
        text = re.sub(r'(?<![\w.])\.(?![\w.])', "__missing", text)
        text = re.sub(r'(?<![<>=!])=(?!=)', "==", text)
        try:
            tree = ast.parse(text.strip(), mode="eval")
        except SyntaxError as error:
            raise ExpressionError(f"unsupported expression: {expression}") from error
        self.strings = strings
        with np.errstate(all="ignore"):
            return self.visit(tree.body)

    def visit(self, node):
        e = self.engine
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            name = node.id
            if name.startswith("__string_"):
                return self.strings[int(name.rsplit("_", 1)[1])][1:-1]
            if name == "__missing":
                return np.nan
            if name == "_n":
                if getattr(e,"_observation",None) is not None:return e._observation+1
                return pd.Series(np.arange(1, len(e.data)+1), index=e.data.index)
            if name == "_N":
                return len(e.data)
            if name == "_rc":
                return e.rc
            if name == "_pi":
                return np.pi
            if name in e.data:
                if getattr(e,"_observation",None) is not None:return e.data[name].iloc[e._observation]
                return e.data[name]
            if name in e.scalars:
                return e.scalars[name]
            if name in e.matrices:
                return e.matrices[name]
            raise ExpressionError(f"{name} not found")
        if isinstance(node, ast.UnaryOp):
            value = self.visit(node.operand)
            if isinstance(node.op, ast.USub):
                return -value
            if isinstance(node.op, ast.UAdd):
                return +value
            if isinstance(node.op, (ast.Not, ast.Invert)):
                return np.logical_not(truth(value))
        if isinstance(node, ast.BinOp):
            left, right = self.visit(node.left), self.visit(node.right)
            ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                   ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod}
            if type(node.op) in ops:
                try:
                    return ops[type(node.op)](left, right)
                except ZeroDivisionError:
                    return np.nan
        if isinstance(node, ast.BoolOp):
            values = [truth(self.visit(x)) for x in node.values]
            result = values[0]
            for value in values[1:]:
                result = np.logical_and(result, value) if isinstance(node.op, ast.And) else np.logical_or(result, value)
            return result
        if isinstance(node, ast.Compare):
            if len(node.ops)>1:
                raise ExpressionError("chained comparisons are unsupported; combine explicit comparisons with &")
            left, result = self.visit(node.left), True
            for op, comp in zip(node.ops, node.comparators):
                right = self.visit(comp)
                # Stata's single supported missing '.' compares equal to '.'.
                a, b = _ordered(left), _ordered(right)
                ops = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}
                if type(op) not in ops:
                    raise ExpressionError("unsupported comparison")
                result = np.logical_and(result, ops[type(op)](a, b))
                left = right
            return result
        if isinstance(node, ast.Subscript):
            array = e.data[node.value.id] if isinstance(node.value,ast.Name) and node.value.id in e.data else self.visit(node.value)
            index = self.visit(node.slice)
            if isinstance(array, pd.Series):
                if np.isscalar(index):
                    return array.iloc[int(index)-1] if 1 <= int(index) <= len(array) else np.nan
                positions = np.asarray(index, dtype=int)-1
                return pd.Series([array.iloc[i] if 0 <= i < len(array) else np.nan for i in positions], index=e.data.index)
            if isinstance(index, tuple):
                return array[tuple(int(i)-1 for i in index)]
            return array[int(index)-1]
        if isinstance(node, ast.Tuple):
            return tuple(self.visit(x) for x in node.elts)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [self.visit(x) for x in node.args]
            name = node.func.id
            if name == "__result":
                group, key = args
                groups = {"r": e.returns, "e": e.estimates, "c": {"N": len(e.data), "k": len(e.data.columns), "pwd": str(e.cwd), "version": 19.5}}
                if key not in groups[group]:
                    raise ExpressionError(f"{group}({key}) not found")
                return groups[group][key]
            if name == "__coef":
                return e.coefficients[args[1]][0 if args[0] == "b" else 1]
            funcs = {
                "abs": np.abs, "sqrt": np.sqrt, "exp": np.exp, "ln": np.log, "log": np.log,
                "log10": np.log10, "sin": np.sin, "cos": np.cos, "tan": np.tan,
                "asin": np.arcsin, "acos": np.arccos, "atan": np.arctan,
                "floor": np.floor, "ceil": np.ceil, "int": np.trunc, "sign": np.sign,
                "mod": np.mod, "min": np.fmin, "max": np.fmax,
                "round": lambda x, unit=1: np.floor(x/unit + .5)*unit,
                "missing": lambda *a: np.logical_or.reduce([pd.isna(x) | (np.asarray(x, dtype=object) == "") for x in a]),
                "cond": lambda c,a,b: np.where(truth(c), a, b),
                "inrange": lambda x,a,b: np.logical_not(pd.isna(x)) & (np.asarray(x) >= np.where(pd.isna(a),-np.inf,a)) & (np.asarray(x) <= np.where(pd.isna(b),np.inf,b)),
                "inlist": lambda x,*v: np.logical_or.reduce([np.asarray(x) == z for z in v]),
                "normal": special.ndtr, "normalden": stats.norm.pdf, "invnormal": special.ndtri,
                "ttail": lambda df,x:stats.t.sf(x,df), "invttail": lambda df,p:stats.t.isf(p,df), "chi2tail": lambda df,x:stats.chi2.sf(x,df),
                "invlogit": special.expit, "logit": special.logit,
                "runiform": lambda *a: e.rng.uniform(*(a or [0, 1]), size=len(e.data)),
                "rnormal": lambda mu=0,sd=1: e.rng.normal(mu, sd, size=len(e.data)),
                "rpoisson": lambda mu: e.rng.poisson(mu, size=len(e.data)),
                "sum": self.running_sum,
                "real": lambda x: pd.to_numeric(x, errors="coerce"),
                "string": lambda x,*fmt: pd.Series(x).astype(str) if not np.isscalar(x) else str(x),
                "strlen": lambda x: pd.Series(x).str.len() if not isinstance(x,str) else len(x),
                "lower": lambda x: x.str.lower() if isinstance(x,pd.Series) else x.lower(),
                "upper": lambda x: x.str.upper() if isinstance(x,pd.Series) else x.upper(),
                "trim": lambda x: x.str.strip() if isinstance(x,pd.Series) else x.strip(),
                "substr": lambda x,a,b: x.str.slice(a-1,a-1+b) if isinstance(x,pd.Series) else x[a-1:a-1+b],
                "word": lambda x,n: str(x).split()[int(n)-1] if 0<int(n)<=len(str(x).split()) else "",
                "rowsof": lambda x: np.atleast_2d(x).shape[0], "colsof": lambda x: np.atleast_2d(x).shape[1],
                "el": self.element,
            }
            if name not in funcs:
                raise ExpressionError(f"function {name}() not implemented")
            return funcs[name](*args)
        raise ExpressionError(f"unsupported expression construct: {type(node).__name__}")
