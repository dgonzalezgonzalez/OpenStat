"""Restricted AST evaluator for the implemented Stata expression subset.

Missing numeric data are NaN in memory, ordered above finite numbers in
comparisons as in Stata. No eval(), attribute access or arbitrary Python calls.
"""
from __future__ import annotations

import ast
import operator
import re
import platform
from datetime import datetime
import unicodedata

import numpy as np
import pandas as pd
from scipy import special, stats
from .parser import quoted_end,unquote


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
    def string_map(value,function):
        if isinstance(value,str):return function(value)
        if np.isscalar(value):return function('' if pd.isna(value) else str(value))
        return pd.Series(value).fillna('').map(function)

    def regex_replace(self,value,pattern,replacement,unicode=False,all_matches=False):
        if unicode:
            import regex as regex_engine
        else:regex_engine=re
        return self.string_map(value,lambda text:regex_engine.sub(pattern,replacement,text,count=0 if all_matches else 1))

    def regex_match(self,value,pattern,unicode=False):
        if unicode:
            import regex as regex_engine
        else:regex_engine=re
        if isinstance(value,str):
            match=regex_engine.search(pattern,value);self.engine._regex_matches=match;return bool(match)
        matches=pd.Series(value).fillna('').map(lambda text:regex_engine.search(pattern,str(text)))
        self.engine._regex_matches=matches;return matches.map(bool).astype(float)

    def regex_group(self,index):
        matches=getattr(self.engine,'_regex_matches',None)
        def group(match):
            if match is None:return ''
            try:return match.group(int(index)) or ''
            except IndexError:return ''
        return matches.map(group) if isinstance(matches,pd.Series) else group(matches)

    def formatted_string(self,value,fmt=None):
        if fmt is None:fmt='%9.0g'
        if not isinstance(fmt,str):raise ExpressionError('string() format must be a string')
        def render(number):return self.engine._metadata_numeric_format(number,fmt).strip()
        if np.isscalar(value):return render(value)
        return pd.Series(value).map(render)

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
        chunks=[];index=0
        while index<len(expression):
            if expression[index]=='"' or expression.startswith('`"',index):
                end=quoted_end(expression,index)
                if end is None:raise ExpressionError('unclosed string')
                strings.append(unquote(expression[index:end]));chunks.append(f'__string_{len(strings)-1}');index=end
            else:chunks.append(expression[index]);index+=1
        text=''.join(chunks)
        text = re.sub(r'\b(r|e|c)\(([A-Za-z_][\w]*)\)', r'__result("\1","\2")', text)
        coefficients=[]
        def protect_coefficient(match):
            coefficients.append((match[1],match[2]))
            return f'__coefficient_{len(coefficients)-1}'
        text = re.sub(r'_(b|se)\[([\w.#]+)\]', protect_coefficient, text)
        text = re.sub(r'(?<![\w.])([LFDS]\d*\.(?:[LFDS]\d*\.)*[A-Za-z_]\w*)',r'__ts("\1")',text,flags=re.I)
        for index,(kind,name) in enumerate(coefficients):text=text.replace(f'__coefficient_{index}',f'__coef("{kind}","{name}")')
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
                return self.strings[int(name.rsplit("_", 1)[1])]
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
                if getattr(e,"_observation",None) is not None:
                    value=e.data[name].iloc[e._observation]
                    return float(value) if isinstance(value,(np.integer,np.floating)) else value
                value=e.data[name]
                return value.astype(float) if pd.api.types.is_numeric_dtype(value.dtype) else value
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
                    value=array.iloc[int(index)-1] if 1 <= int(index) <= len(array) else np.nan
                    return float(value) if isinstance(value,(np.integer,np.floating)) else value
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
                if group=='e' and key=='sample':
                    if not hasattr(e,'_sample_mask') or len(e._sample_mask)!=len(e.data):raise ExpressionError('estimation sample is unavailable')
                    return e._sample_mask.astype(float)
                constants={"N":len(e.data),"k":len(e.data.columns),"pwd":str(e.cwd),"version":19.5,"stata_version":19.5,
                           "os":'Windows' if platform.system()=='Windows' else platform.system(),"machine_type":'PC (64-bit x86-64)' if platform.system()=='Windows' else platform.machine(),
                           "current_date":datetime.now().strftime('%d %b %Y').lstrip('0'),"current_time":datetime.now().strftime('%H:%M:%S'),
                           "username":__import__('getpass').getuser(),"filename":getattr(e,'_data_filename',''),"maxvar":32767,"max_N_theory":2**40-1,"processors":__import__('os').cpu_count() or 1}
                groups = {"r": e.returns, "e": e.estimates, "c": constants}
                if key not in groups[group]:
                    raise ExpressionError(f"{group}({key}) not found")
                return groups[group][key]
            if name == "__coef":
                return e.coefficients[args[1]][0 if args[0] == "b" else 1]
            if name=='__ts':
                from .design import time_series
                return time_series(e,args[0])
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
                "string": self.formatted_string,
                "strlen": lambda x:self.string_map(x,lambda text:len(text.encode('utf-8'))),
                "length": lambda x:self.string_map(x,lambda text:len(text.encode('utf-8'))),
                "ustrlen": lambda x:self.string_map(x,len),
                "abbrev": lambda x,n:self.string_map(x,lambda text:text if len(text)<=int(n) else text[:max(0,int(n)-2)]+'~'+text[-1:]),
                "lower": lambda x: x.str.lower() if isinstance(x,pd.Series) else x.lower(),
                "upper": lambda x: x.str.upper() if isinstance(x,pd.Series) else x.upper(),
                "strlower": lambda x:self.string_map(x,str.lower),"strupper":lambda x:self.string_map(x,str.upper),
                "ustrlower": lambda x:self.string_map(x,str.lower),"ustrupper":lambda x:self.string_map(x,str.upper),
                "ustrnormalize":lambda x,mode:self.string_map(x,lambda text:unicodedata.normalize(str(mode).upper(),text)),
                "trim": lambda x: x.str.strip() if isinstance(x,pd.Series) else x.strip(),
                "strtrim":lambda x:self.string_map(x,lambda text:text.strip(' ')),"ustrtrim":lambda x:self.string_map(x,str.strip),
                "itrim":lambda x:self.string_map(x,lambda text:re.sub(' +',' ',text)),
                "strpos":lambda x,sub:self.string_map(x,lambda text:text.find(sub)+1),
                "ustrpos":lambda x,sub:self.string_map(x,lambda text:text.find(sub)+1),
                "subinstr":lambda x,old,new,count:self.string_map(x,lambda text:text.replace(old,new,-1 if pd.isna(count) else int(count))),
                "regexm":self.regex_match,"regexs":self.regex_group,
                "regexr":lambda x,p,r:self.regex_replace(x,p,r),
                "ustrregexm":lambda x,p:self.regex_match(x,p,unicode=True),
                "ustrregexra":lambda x,p,r:self.regex_replace(x,p,r,unicode=True,all_matches=True),
                "ustrregexrf":lambda x,p,r:self.regex_replace(x,p,r,unicode=True),
                "char":lambda number:chr(int(number)),"uchar":lambda number:chr(int(number)),
                "substr": lambda x,a,b: x.str.slice(a-1,a-1+b) if isinstance(x,pd.Series) else x[a-1:a-1+b],
                "word": lambda x,n: str(x).split()[int(n)-1] if 0<int(n)<=len(str(x).split()) else "",
                "wordcount":lambda x:self.string_map(x,lambda text:len(text.split())),
                "rowsof": lambda x: np.atleast_2d(x).shape[0], "colsof": lambda x: np.atleast_2d(x).shape[1],
                "el": self.element,
            }
            if name not in funcs:
                raise ExpressionError(f"function {name}() not implemented")
            return funcs[name](*args)
        raise ExpressionError(f"unsupported expression construct: {type(node).__name__}")
