"""An explicitly bounded, interpreted Mata matrix-expression subset."""
from __future__ import annotations

import ast
import re

import numpy as np

from .parser import split_outside


class MataError(ValueError):
    pass


class Mata:
    def __init__(self, engine):
        self.engine = engine
        self.variables = {}

    @staticmethod
    def invsym(value):
        value=np.asarray(value,dtype=float)
        if value.ndim!=2 or value.shape[0]!=value.shape[1] or not np.allclose(value,value.T):
            raise MataError("invsym() requires a symmetric square matrix")
        eigenvalues=np.linalg.eigvalsh(value)
        if not len(eigenvalues) or eigenvalues.min()<=np.finfo(float).eps*max(1.,np.linalg.norm(value))*value.shape[0]:
            raise MataError("singular or non-positive-definite invsym() is not implemented")
        return np.linalg.inv(value)

    def evaluate(self, text):
        text = text.strip()
        # Matrix literals use commas for columns and backslashes for rows.
        if text.startswith("(") and text.endswith(")"):
            inner = text[1:-1]
            rows = split_outside(inner, "\\")
            if len(rows) > 1 or len(split_outside(inner)) > 1:
                return np.block([[np.atleast_2d(self.evaluate(c)) for c in split_outside(row)] for row in rows])
        text = re.sub(r'\b([A-Za-z_]\w*)\s*\x27', r'transpose(\1)', text)
        if any(op in text for op in (":*",":/",":^")):
            raise MataError("Mata elementwise colon operators not implemented")
        text = text.replace("^", "**")
        try:
            return self.visit(ast.parse(text, mode="eval").body)
        except (SyntaxError, TypeError, KeyError, IndexError, np.linalg.LinAlgError) as ex:
            raise MataError(f"unsupported or invalid Mata expression: {text}") from ex

    def visit(self, n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            if n.id not in self.variables:
                raise MataError(f"Mata variable {n.id} not found")
            return self.variables[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op,(ast.USub,ast.UAdd)):
            return -self.visit(n.operand) if isinstance(n.op, ast.USub) else self.visit(n.operand)
        if isinstance(n, ast.Tuple):
            return tuple(self.visit(x) for x in n.elts)
        if isinstance(n, ast.Subscript):
            indices = self.visit(n.slice)
            a = np.atleast_2d(self.visit(n.value))
            if isinstance(indices,tuple):
                if len(indices)!=2 or any(not np.isscalar(i) or i!=int(i) or int(i)<1 or int(i)>size for i,size in zip(indices,a.shape)):
                    raise MataError("Mata subscript out of range")
                return a[tuple(int(i)-1 for i in indices)]
            if min(a.shape)>1:raise MataError("matrix requires row and column subscripts")
            if not np.isscalar(indices) or int(indices)!=indices or not 1<=int(indices)<=a.size:raise MataError("Mata subscript out of range")
            return a.flatten()[int(indices)-1]
        if isinstance(n, ast.BinOp):
            a,b = self.visit(n.left),self.visit(n.right)
            if isinstance(n.op,(ast.Add,ast.Sub)):
                if np.size(a)>1 and np.size(b)>1 and np.shape(a)!=np.shape(b):raise MataError("Mata matrices not conformable")
                return a+b if isinstance(n.op,ast.Add) else a-b
            if isinstance(n.op,ast.Mult):
                if np.size(a)==1:return np.asarray(a).item()*b
                if np.size(b)==1:return a*np.asarray(b).item()
                return np.matmul(a,b) if np.ndim(a)>0 and np.ndim(b)>0 else a*b
            if isinstance(n.op,ast.Div):
                if np.size(b)!=1:raise MataError("Mata division requires a scalar denominator")
                return a/np.asarray(b).item()
            if isinstance(n.op,ast.Pow):
                if np.size(a)!=1 or np.size(b)!=1:raise MataError("Mata exponentiation requires scalar operands")
                return a**b
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            args=[self.visit(a) for a in n.args]
            name=n.func.id
            functions = {
                "I":lambda k:np.eye(int(k)), "J":lambda r,c,x:np.full((int(r),int(c)),x),
                "rows":lambda a:np.atleast_2d(a).shape[0],"cols":lambda a:np.atleast_2d(a).shape[1],
                "transpose":np.transpose,"invsym":self.invsym,"luinv":np.linalg.inv,
                "det":np.linalg.det,"trace":np.trace,"diagonal":lambda a:np.diag(a).reshape(-1,1),
                "diag":lambda a:np.diag(np.asarray(a).flatten()),"sqrt":np.sqrt,"exp":np.exp,"ln":np.log,
                "sum":np.sum,"mean":lambda a:np.mean(a,axis=0,keepdims=True),
                "colsum":lambda a:np.sum(a,axis=0,keepdims=True),"rowsum":lambda a:np.sum(a,axis=1,keepdims=True),
                "cross":lambda a,b:np.asarray(a).T@np.asarray(b),
                "st_matrix":self.st_matrix,"st_numscalar":self.st_numscalar,
                "st_data":lambda *a:self.st_data(*a),
            }
            if name not in functions:
                raise MataError(f"Mata function {name}() not implemented")
            return functions[name](*args)
        raise MataError(f"unsupported Mata construct: {type(n).__name__}")

    def st_matrix(self,name,value=None):
        if value is None: return self.engine.matrices[name].copy()
        self.engine.matrices[name]=np.atleast_2d(value).copy()

    def st_numscalar(self,name,value=None):
        if value is None:return self.engine.scalars[name]
        self.engine.scalars[name]=float(value)

    def st_data(self, obs, varlist):
        if not (isinstance(obs,float) and np.isnan(obs)):
            raise MataError("st_data() supports all observations (.) only")
        return self.engine.data[varlist.split()].to_numpy(dtype=float)

    def run(self, source):
        for line in source.splitlines():
            for statement in split_outside(line,";"):
                statement=statement.strip()
                if not statement or statement.startswith("//"):continue
                if statement.startswith(("real ","string ","void ","struct ","class ","pointer ")):
                    raise MataError("Mata declarations and functions not implemented")
                statement=re.sub(r'(?<!\w)\.(?!\w)',"float_missing",statement)
                self.variables["float_missing"]=np.nan
                match=re.fullmatch(r'([A-Za-z_]\w*)\s*=\s*(.*)',statement)
                if match:
                    self.variables[match[1]]=self.evaluate(match[2])
                else:
                    result=self.evaluate(statement)
                    if result is not None:self.engine.emit(str(result))
