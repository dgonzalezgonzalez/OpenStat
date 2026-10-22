"""Independent Stata-style interpreter. Unsupported syntax fails explicitly.

This runtime does not wrap Stata or include StataCorp code. Only the documented
subset is implemented. Numerical work uses NumPy, SciPy and statsmodels.
"""
from __future__ import annotations

import copy
import fnmatch
import io
import json
import re
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .expression import Evaluator, ExpressionError, truth
from .mata import Mata, MataError
from .packages import PackageError, PackageManager, download
from .parser import Statement, lex, split_outside, tokens, unquote, quoted_end, outside_positions, parse_options, read_source
from .statistics import StatisticalCommands
from .graphics import GraphicsCommands
from .data_commands import IOCommands
from .metadata import MetadataCommands
from .programming import ProgrammingCommands, expand_macros, numlist, numeric_range
from .output import OutputCommands
from .panel import PanelCommands
from .extended_data import ExtendedDataCommands


class StataError(Exception):
    def __init__(self, message, rc=198, line=None):
        super().__init__(message)
        self.rc, self.line = rc, line


class _ExitFlow(Exception):
    def __init__(self,code=0):self.code=code


class _LoopFlow(Exception):
    def __init__(self, action):self.action=action


@dataclass
class RunResult:
    output: str
    rc: int = 0
    line: int | None = None


ALIASES = {"gen":"generate","g":"generate","repl":"replace","su":"summarize","sum":"summarize",
           "summ":"summarize","reg":"regress","di":"display","dis":"display","disp":"display",
           "qui":"quietly","cap":"capture","noi":"noisily","des":"describe","desc":"describe",
           "l":"list","cou":"count","tab":"tabulate","corr":"correlate","ren":"rename",
           "mat":"matrix","loc":"local","glo":"global","prog":"program","ret":"return",
           "bys":"bysort","forv":"forvalues","forva":"forvalues","forval":"forvalues",
           "forvalue":"forvalues","fore":"foreach","forea":"foreach","foreac":"foreach",
           "est":"estimates","levelso":"levelsof","levelsof":"levelsof","token":"tokenize"}


class Engine(ProgrammingCommands, ExtendedDataCommands, OutputCommands, PanelCommands, IOCommands, MetadataCommands, StatisticalCommands, GraphicsCommands):
    def __init__(self,cwd: Path|str|None=None, ado_dir: Path|str|None=None):
        self.cwd=Path(cwd or Path.cwd()).resolve()
        self.data=pd.DataFrame()
        self.variable_labels={}
        self.value_labels={}
        self.value_label_names={}
        self.formats={}
        self._storage_types={}
        self._default_type="float"
        self.dataset_label=""
        self.locals={}
        self.globals={}
        self.scalars={}
        self.matrices={}
        self.returns={}
        self.estimates={}
        self.coefficients={}
        self.programs={}
        self._program_options={}
        self._hidden_variables=set()
        self.stored_estimates={}
        self.graphs=[]
        self._figure=None
        self._fit=None
        self._fit_design=None
        self._model_columns=[]
        self._preserved=None
        self._cancel=threading.Event()
        self._run_lock=threading.Lock()
        self._depth=0
        self._loop_depth=0
        self._temp_counter=0
        self._scope_tempvars=set()
        self._scope_tempnames=set()
        self._steps=0
        self._output=[]
        self._quiet=0
        self._log=None
        self.rc=0
        self.rng=np.random.default_rng()
        self.packages=PackageManager(Path(ado_dir) if ado_dir else None)
        self._ado_prepend=[]
        self._ado_append=[]
        self.mata=Mata(self)
        self.evaluator=Evaluator(self)

    def cancel(self):
        self._cancel.set()

    def emit(self,text):
        if not self._quiet:
            self._output.append(str(text))
            if self._log:
                self._log.write(str(text)+"\n")
                self._log.flush()

    def run(self,script: str) -> RunResult:
        if not self._run_lock.acquire(blocking=False):
            return RunResult("engine busy", 1)
        try:
            self._cancel.clear()
            self._output=[]
            self._steps=0
            try:
                statements=lex(script)
                self._execute(statements)
                self.rc=0
                return RunResult("\n".join(self._output),0)
            except _ExitFlow as flow:
                self.rc=flow.code
                return RunResult("\n".join(self._output),flow.code)
            except (StataError, ValueError, OSError, KeyError, TypeError, IndexError) as error:
                rc=getattr(error,"rc",198)
                self.rc=rc
                line=getattr(error,"line",None)
                self._quiet=0
                self.emit(f"{error}\nr({rc});"+(f" [line {line}]" if line else ""))
                return RunResult("\n".join(self._output),rc,line)
        finally:
            self._run_lock.release()

    def run_file(self,path) -> RunResult:
        path=self.path(str(path))
        try:
            source=read_source(path)
        except (OSError,UnicodeError) as error:
            return RunResult(str(error),601)
        with self._local_scope(""):
            return self.run(source)

    def path(self,value,extension=None):
        path=Path(unquote(value))
        if extension and not path.suffix:path=path.with_suffix(extension)
        return path if path.is_absolute() else self.cwd/path

    def expand(self,text):
        globals_={"S_StataVersion":"19.5",**self.globals}
        return expand_macros(text,self.locals,globals_,evaluate=self.evaluate,extended=self._inline_extended_macro,error_type=StataError)

    def _inline_extended_macro(self,expression):
        target="__openstat_inline_macro"
        sentinel=object()
        previous=self.locals.get(target,sentinel)
        try:
            self._extended_macro("local",target+": "+expression)
            return self.locals[target]
        finally:
            if previous is sentinel:self.locals.pop(target,None)
            else:self.locals[target]=previous

    def evaluate(self,text):
        try:
            value=self.evaluator.evaluate(text)
            if isinstance(value,np.ndarray) and value.ndim==0:value=value.item()
            if isinstance(value,(float,np.floating)) and not np.isfinite(value):return np.nan
            if isinstance(value,(pd.Series,np.ndarray)) and pd.api.types.is_numeric_dtype(value.dtype):
                return np.where(np.isfinite(value),value,np.nan)
            return value
        except (ExpressionError,KeyError) as ex:raise StataError(str(ex),198) from ex

    def _tick(self):
        self._steps+=1
        if self._cancel.is_set():raise StataError("execution interrupted",1)
        if self._steps>100000:raise StataError("execution step limit (100000) exceeded",901)

    def _condition(self,expression):
        value=truth(self.evaluate(expression))
        if np.isscalar(value):return bool(value)
        array=np.asarray(value)
        if not array.size:raise StataError("no observations",2000)
        return bool(array.flat[0])

    @staticmethod
    def _prefixes(text):
        prefixes=[]
        while match:=re.match(r"^(capture|cap|quietly|qui|noisily|noi)\b\s*:?[ \t]*",text,re.I):
            prefixes.append(ALIASES.get(match[1].lower(),match[1].lower()))
            text=text[match.end():].strip()
        return prefixes,text

    def _with_prefixes(self,prefixes,operation):
        if not prefixes:
            return operation()
        prefix=prefixes[0]
        oldquiet=self._quiet
        self._quiet=0 if prefix=="noisily" else self._quiet+1
        try:
            result=self._with_prefixes(prefixes[1:],operation)
            if prefix=="capture":self.rc=0
            return result
        except (StataError,ValueError,OSError,KeyError,TypeError,IndexError) as error:
            if prefix!="capture":raise
            self.rc=getattr(error,"rc",198)
        finally:
            self._quiet=oldquiet

    @staticmethod
    def _block_end(statements,start):
        depth=1
        for index in range(start+1,len(statements)):
            value=statements[index].text.strip()
            if value.endswith("{"):depth+=1
            elif value=="}":depth-=1
            if not depth:return index
        raise StataError("unclosed brace")

    def _execute(self,statements):
        self._depth+=1
        if self._depth>60:
            self._depth-=1
            raise StataError("execution nesting limit",901)
        i=0
        try:
            while i<len(statements):
                statement=statements[i]
                try:
                    self._tick()
                    text=self.expand(statement.text).strip()
                    if not text:
                        i+=1
                        continue
                    prefixes,text=self._prefixes(text)
                    command=ALIASES.get(text.split()[0].lower(),text.split()[0].lower()) if text else ""
                    if text.endswith("{") and (command in {"forvalues","foreach","while","if"} or text=="{" and prefixes):
                        j=self._block_end(statements,i)
                        body=statements[i+1:j]
                        header=text[:-1].strip()
                        if command=="if":
                            branches=[(header[2:].strip(),body)]
                            after=j+1
                            while after<len(statements) and re.match(r"^else\b",statements[after].text.strip()):
                                alternate=statements[after].text.strip()
                                if not alternate.endswith("{"):raise StataError("else requires a brace block",198,statements[after].line)
                                end=self._block_end(statements,after)
                                condition=alternate[:-1].strip()[4:].strip()
                                if condition and not condition.startswith("if "):raise StataError("invalid else syntax",198,statements[after].line)
                                if branches[-1][0] is None:raise StataError("else after unconditional else",198,statements[after].line)
                                branches.append((condition[3:].strip() if condition else None,statements[after+1:end]))
                                after=end+1
                            def choose():
                                for condition,branch in branches:
                                    if condition is None or self._condition(self.expand(condition)):
                                        self._execute(branch)
                                        break
                            self._with_prefixes(prefixes,choose)
                            i=after
                            continue
                        if command=="while":
                            # Re-expand each iteration rather than freezing a
                            # macro-dependent condition when entering the loop.
                            _,raw=self._prefixes(statement.text.strip())
                            header=raw[:-1].strip()
                        self._with_prefixes(prefixes,lambda:self._execute_loop("" if text=="{" else command,header,body))
                        i=j+1
                        continue
                    if command=="if":
                        # Inline if/else statements are common inside ado
                        # programs and have the same branch-skipping semantics
                        # as their brace-block counterparts.
                        alternatives=[]
                        after=i+1
                        while after<len(statements) and re.match(r"^else\b",statements[after].text.strip()):
                            value=statements[after].text.strip()[4:].strip()
                            if value.endswith("{"):
                                end=self._block_end(statements,after)
                                alternatives.append((value,statements[after+1:end]))
                                after=end+1
                            else:
                                alternatives.append((value,None))
                                after+=1
                        def inline_branches():
                            selected=self._inline_if(text.split(maxsplit=1)[1])
                            for alternate,branch in alternatives:
                                if selected:continue
                                alternate=self.expand(alternate)
                                if branch is not None:
                                    condition=alternate[:-1].strip()
                                    if condition and not condition.startswith("if "):raise StataError("invalid else syntax")
                                    if not condition or self._condition(condition[3:]):
                                        self._execute(branch)
                                        selected=True
                                elif alternate.startswith("if "):
                                    selected=self._inline_if(alternate[3:])
                                else:
                                    self.command(alternate)
                                    selected=True
                        self._with_prefixes(prefixes,inline_branches)
                        i=after
                        continue
                    if command=="program":
                        arg=text.split(maxsplit=1)[1] if len(text.split(maxsplit=1))>1 else ""
                        if not re.match(r"^(?:drop|list|dir)\b",arg):
                            j=i+1
                            while j<len(statements) and statements[j].text.strip()!="end":j+=1
                            if j==len(statements):raise StataError("program missing end")
                            self._with_prefixes(prefixes,lambda:self._define_program(arg,statements[i+1:j]))
                            i=j+1
                            continue
                    if text in {"mata","mata:"}:
                        j=i+1
                        while j<len(statements) and statements[j].text.strip()!="end":j+=1
                        if j==len(statements):raise StataError("Mata block missing end")
                        def mata_block():
                            try:self.mata.run("\n".join(self.expand(x.text) for x in statements[i+1:j]))
                            except MataError as ex:raise StataError(str(ex),199) from ex
                        self._with_prefixes(prefixes,mata_block)
                        i=j+1
                        continue
                    self._with_prefixes(prefixes,lambda:self.command(text))
                except (StataError,ValueError,OSError,KeyError,TypeError,IndexError) as ex:
                    if not isinstance(ex,StataError):ex=StataError(str(ex),198)
                    if ex.line is None:ex.line=statement.line
                    raise ex
                i+=1
        finally:
            self._depth-=1

    def _define_program(self,arg,body):
        parts=split_outside(arg)
        words=tokens(parts[0])
        if words and words[0] in {"define","def"}:words=words[1:]
        if len(words)!=1 or not re.fullmatch(r"[A-Za-z_]\w{0,31}",words[0]):raise StataError("invalid program name")
        options=self.options(" ".join(parts[1:]),{"rclass","eclass","sortpreserve","byable"})
        if "rclass" in options and "eclass" in options:raise StataError("only one program class allowed")
        if "byable" in options and options["byable"]!="recall":raise StataError("only byable(recall) is implemented",199)
        if words[0] in self.programs:raise StataError(f"program {words[0]} already defined",110)
        self.programs[words[0]]=body
        self._program_options[words[0]]=options

    def _execute_loop(self,command,header,body):
        if not command:
            self._execute(body)
            return
        values=None
        if command=="forvalues":
            match=re.fullmatch(r"\w+\s+(\w+)\s*=\s*(.+)",header)
            if not match:raise StataError("invalid forvalues syntax")
            name=match[1]
            values=numeric_range(match[2],integer=True)
        elif command=="foreach":
            match=re.fullmatch(r"\w+\s+(\w+)\s+(in|of\s+(?:varlist|newlist|numlist|local|global))\s*(.*)",header)
            if not match:raise StataError("invalid foreach syntax")
            name,kind,source=match[1],match[2],match[3]
            kind=" ".join(kind.split())
            if kind in {"of local","of global"}:
                if not re.fullmatch(r"\w+",source):raise StataError("foreach requires a macro name")
                source=str((self.locals if kind=="of local" else self.globals).get(source,""))
            if kind=="of varlist":values=self.varlist(source,default=False)
            elif kind=="of newlist":
                values=tokens(source)
                for value in values:self._new_name(value)
                if len(values)!=len(set(values)):raise StataError("duplicate variable name",110)
            elif kind=="of numlist":values=numlist(source)
            else:values=[unquote(value) for value in tokens(source)]
        self._loop_depth+=1
        try:
            iterator=iter(values) if values is not None else None
            while True:
                if iterator is None:
                    if not self._condition(self.expand(header.split(maxsplit=1)[1])):break
                else:
                    try:self.locals[name]=next(iterator)
                    except StopIteration:break
                self._tick()
                try:self._execute(body)
                except _LoopFlow as flow:
                    if flow.action=="break":break
        finally:
            self._loop_depth-=1

    def _inline_if(self,arg):
        # A command starts at a word boundary outside quoted strings and nested
        # expressions. Try candidates in source order, retaining the useful
        # evaluation error if no valid condition/command split is found.
        last_error=None
        for index in outside_positions(arg):
            if index and arg[index].isspace():
                following=arg[index:].lstrip()
                if not re.match(r"[A-Za-z_]\w*(?=\s|:|,|$)",following):continue
                try:condition=self._condition(arg[:index])
                except StataError as error:
                    last_error=error
                    continue
                if condition:self.command(following)
                return condition
        if last_error:raise last_error
        raise StataError("if requires an expression and command")

    def _temporary_name(self):
        while True:
            self._temp_counter+=1
            name=f"__os{self._temp_counter:06d}"
            if name not in self.data and name not in self.scalars and name not in self.matrices:
                return name

    @contextmanager
    def _local_scope(self,arg):
        old,oldvars,oldnames=self.locals,self._scope_tempvars,self._scope_tempnames
        self.locals={str(i):unquote(value) for i,value in enumerate(tokens(arg),1)}
        self.locals["0"]=arg
        self._scope_tempvars,self._scope_tempnames=set(),set()
        try:
            yield
        finally:
            names=[name for name in self._scope_tempvars if name in self.data]
            if names:self.data.drop(columns=names,inplace=True)
            for mapping in (self.variable_labels,self.value_label_names,self.formats,self._storage_types):
                for name in self._scope_tempvars:mapping.pop(name,None)
            for name in self._scope_tempnames:
                self.scalars.pop(name,None)
                self.matrices.pop(name,None)
            self.locals,self._scope_tempvars,self._scope_tempnames=old,oldvars,oldnames

    def options(self,text,allowed):
        output={}
        for name,value in parse_options(text):
            if name not in allowed:raise StataError(f"option {name} not allowed",198)
            if name in output:raise StataError(f"option {name} specified more than once",198)
            output[name]=value
        return output

    def varlist(self,text,default=True):
        if not text.strip():return [name for name in self.data.columns if name not in self._hidden_variables] if default else []
        result=[]
        for token in text.split():
            if "-" in token and not any(c in token for c in "*?"):
                first,last=token.split("-",1);cols=list(self.data.columns)
                if first not in cols or last not in cols:raise StataError(f"variable {token} not found",111)
                names=cols[cols.index(first):cols.index(last)+1]
            else:names=[str(c) for c in self.data if (c not in self._hidden_variables or str(c)==token) and fnmatch.fnmatchcase(str(c),token)]
            if not names:raise StataError(f"variable {token} not found",111)
            for name in names:
                if name not in result:result.append(name)
        return result

    def qualifiers(self,text):
        # if and in are parsed only outside quoted strings/parentheses.
        quote=False;depth=0;positions=[]
        for i,char in enumerate(text):
            if char=='"':quote=not quote
            elif not quote:
                if char in "([":depth+=1
                elif char in ")]":depth-=1
                if depth==0:
                    for word in (" if "," in "):
                        if text[i:i+len(word)]==word:positions.append((i,word.strip()))
        mask=pd.Series(True,index=self.data.index)
        base=text[:positions[0][0]].strip() if positions else text.strip()
        for k,(position,kind) in enumerate(positions):
            value=text[position+len(kind)+2:positions[k+1][0] if k+1<len(positions) else None].strip()
            if kind=="if":mask &= pd.Series(truth(self.evaluate(value)),index=self.data.index).astype(bool)
            else:
                mm=re.fullmatch(r'(-?\d+|f|l)(?:/(-?\d+|f|l))?',value)
                if not mm:raise StataError("unsupported in range")
                def pos(x):return 1 if x=="f" else len(self.data) if x=="l" else int(x) if int(x)>0 else len(self.data)+int(x)+1
                a,b=pos(mm[1]),pos(mm[2] or mm[1]);r=np.arange(1,len(self.data)+1)
                mask &= (r>=a)&(r<=b)
        return base,mask

    def _new_name(self,name):
        if not re.fullmatch(r'[A-Za-z_]\w{0,31}',name):raise StataError("invalid variable name")
        if name in self.data:raise StataError(f"variable {name} already defined",110)

    def _read(self,path):
        import pyreadstat
        data,meta=pyreadstat.read_dta(str(path),apply_value_formats=False,user_missing=True,disable_datetime_conversion=True)
        if any(meta.missing_user_values.values()):
            raise StataError("extended missing values (.a–.z) are not implemented; dataset not loaded",199)
        self.variable_labels={n:v for n,v in zip(meta.column_names,meta.column_labels) if v}
        self.dataset_label=meta.file_label or ""
        self.value_labels=meta.value_labels
        self.value_label_names=meta.variable_to_label
        self.formats=meta.original_variable_types
        storage={"int8":"byte","int16":"int","int32":"long","float":"float","double":"double"}
        self._storage_types={name:storage.get(kind,"str"+str(meta.variable_storage_width.get(name,1))) for name,kind in meta.readstat_variable_types.items()}
        for name,kind in self._storage_types.items():
            if kind=="float":data[name]=data[name].astype("float32")
        return data

    def _write(self,path,version=118):
        # pandas writer supports dta 114,117,118,119 (119 allows >32767 vars).
        value_labels={n:self.value_labels[label] for n,label in self.value_label_names.items() if n in self.data and label in self.value_labels}
        frame=self.data.drop(columns=[name for name in self._hidden_variables if name in self.data]).copy()
        long_strings=[]
        for name,kind in self._storage_types.items():
            if name not in frame:continue
            if kind in {"byte","int","long"}:
                frame[name]=pd.array(frame[name],dtype={"byte":"Int8","int":"Int16","long":"Int32"}[kind])
            elif kind in {"float","double"}:frame[name]=frame[name].astype("float32" if kind=="float" else "float64")
            elif kind=="strL":long_strings.append(name)
        options={"convert_strl":long_strings} if long_strings and version>=117 else {}
        frame.to_stata(path,write_index=False,version=version,data_label=self.dataset_label,
                      variable_labels={n:v for n,v in self.variable_labels.items() if n in frame},value_labels=value_labels,**options)

    def command(self,text):
        self._tick()
        text=text.strip()
        if not text:return
        match=re.match(r"([A-Za-z_]\w*(?::)?)(?=\s|,|$)",text)
        if not match:raise StataError(f"invalid command {text}",198)
        command=ALIASES.get(match[1].lower(),match[1].lower())
        arg=text[match.end():].strip()
        if command in {"capture","quietly","noisily"}:
            if not arg:raise StataError("prefix requires command")
            prefixes,body=self._prefixes(text)
            self._with_prefixes(prefixes,lambda:self.command(body))
            return
        # _rc must remain visible to local assignment immediately after capture.
        previous_rc=self.rc
        if command not in {"local","global","display","scalar","if"}:self.rc=0
        if command=="if":self._inline_if(arg);return
        if command in {"else","}"}:raise StataError("unexpected control delimiter")
        if command=="continue":
            if not self._loop_depth:raise StataError("continue outside loop")
            if arg not in {"",", break",",break"}:raise StataError("invalid continue syntax")
            raise _LoopFlow("break" if arg else "continue")
        if command=="error":
            if not re.fullmatch(r"\d+",arg) or not int(arg):raise StataError("error requires a nonzero return code")
            raise StataError(f"error {arg}",int(arg))
        if self._programming(command,arg):return
        if self._extended_data(command,arg):return
        if self._output_command(command,arg):return
        if self._panel_command(command,arg):return
        if command in {"by","bysort"}:
            self._by(arg,sort=command=="bysort");return
        if command=="clear":
            if arg not in {"","all"}:raise StataError("unsupported clear target")
            self.data=pd.DataFrame();self.variable_labels={};self.value_labels={};self.value_label_names={};self.formats={};self.dataset_label=""
            self._storage_types={}
            if arg=="all":
                self.scalars.clear();self.matrices.clear();self.programs.clear();self._program_options.clear();self.estimates.clear();self.returns.clear();self.mata.variables.clear()
                self.coefficients.clear();self.stored_estimates.clear();self._fit=None;self._fit_design=None;self._model_columns=[]
                for declaration in ("_panel","_panelvar","_timevar","_xtset","_tsset","_panel_variable","_time_variable","_time_delta"):
                    if hasattr(self,declaration):setattr(self,declaration,None)
            return
        if command=="set":
            self._set(arg);return
        if command=="version":
            m=re.fullmatch(r'(\d+(?:\.\d+)?)(?:\s*:\s*(.*))?',arg)
            if not m or float(m[1])>19.5:raise StataError("unsupported version request",9)
            if m[2]:self.command(m[2])
            return
        if command in {"local","global"}:
            if command=="local" and (increment:=re.fullmatch(r"(\+\+|--)(\w+)",arg)):
                current=self.locals.get(increment[2],"0")
                self.locals[increment[2]]=self._format(self.evaluate(current)+(1 if increment[1]=="++" else -1))
                self.rc=0
                return
            m=re.fullmatch(r'(\w+)\s*(.*)',arg)
            if not m:raise StataError("macro name required")
            value=m[2].strip()
            if value.startswith("="):
                evaluated=self.evaluate(value[1:]);value=self._format(evaluated)
            elif value.startswith(":"):
                mm=re.fullmatch(r':\s*word\s+count\s+(.*)',value)
                if not mm:raise StataError("extended macro function not implemented",199)
                value=str(len(tokens(mm[1])))
            else:value=unquote(value)
            (self.locals if command=="local" else self.globals)[m[1]]=value
            self.rc=0;return
        if command in {"tempfile","tempname","tempvar"}:
            for name in arg.split():
                if not re.fullmatch(r"[A-Za-z_]\w{0,30}",name):raise StataError("invalid local macro name")
                if command=="tempfile":
                    root=Path(tempfile.mkdtemp(prefix="openstat-"));self.locals[name]=str(root/(name+".dta"))
                else:
                    value=self._temporary_name();self.locals[name]=value
                    (self._scope_tempvars if command=="tempvar" else self._scope_tempnames).add(value)
            return
        if command=="args":
            for index,name in enumerate(arg.split(),1):self.locals[name]=self.locals.get(str(index),"")
            return
        if command=="macro":
            if arg=="list":self.emit(str({"local":self.locals,"global":self.globals}));return
            if arg.startswith("drop "):
                names=arg[5:].split()
                for name in names:
                    if name=="_all":self.globals.clear();self.locals.clear()
                    else:self.globals.pop(name,None);self.locals.pop(name,None)
                return
            raise StataError("unsupported macro subcommand",199)
        if command in {"generate","replace","egen"}:
            self._generate(command,arg);return
        if command in {"keep","drop"}:
            if arg=="_all":
                self.data=pd.DataFrame(index=self.data.index) if command=="drop" else self.data;return
            base,mask=self.qualifiers(" "+arg if arg.startswith(("if ","in ")) else arg)
            if not base:
                self.data=self.data.loc[mask if command=="keep" else ~mask].reset_index(drop=True)
            else:
                variables=self.varlist(base)
                self.data=self.data[variables] if command=="keep" else self.data.drop(columns=variables)
            return
        if command=="rename":
            pair=tokens(arg)
            if len(pair)!=2:raise StataError("rename requires old and new names")
            self.varlist(pair[0]);self._new_name(pair[1]);self.data.rename(columns={pair[0]:pair[1]},inplace=True)
            for mapping in (self.variable_labels,self.value_label_names,self.formats,self._storage_types):
                if pair[0] in mapping:mapping[pair[1]]=mapping.pop(pair[0])
            return
        if command=="order":
            parts=split_outside(arg);names=self.varlist(parts[0]);opt=self.options(",".join(parts[1:]),{"first","last","before","after"})
            remaining=[c for c in self.data if c not in names]
            if "before" in opt or "after" in opt:
                target=opt.get("before",opt.get("after"));p=remaining.index(target)+(1 if "after" in opt else 0);remaining[p:p]=names
            else:remaining=remaining+names if "last" in opt else names+remaining
            self.data=self.data[remaining];return
        if command in {"sort","gsort"}:
            parts=split_outside(arg);self.options(",".join(parts[1:]),{"stable"})
            names=parts[0].split();asc=[not x.startswith("-") for x in names];names=[x.lstrip("+-") for x in names]
            self.varlist(" ".join(names));self.data=self.data.sort_values(names,ascending=asc,kind="stable",na_position="last").reset_index(drop=True);return
        if command in {"summarize","count","describe","list","tabulate","correlate","codebook","inspect"}:
            self._describe(command,arg);return
        if command=="display":self._display(arg);self.rc=0;return
        if command=="assert":
            base,mask=self.qualifiers(arg)
            value=pd.Series(truth(self.evaluate(base)),index=self.data.index)
            if not value.loc[mask].all():raise StataError("assertion is false",9)
            return
        if command in {"use","save","saveold","import","export","append","merge","reshape","collapse"}:
            self._data_command(command,arg);return
        if command=="preserve":
            if arg:raise StataError("preserve takes no arguments")
            if self._preserved is not None:raise StataError("already preserved",621)
            self._preserved=copy.deepcopy((self.data,self.variable_labels,self.value_labels,self.value_label_names,self.formats,self.dataset_label,self._storage_types));return
        if command=="restore":
            opt=self.options(arg.lstrip(", "),{"not","preserve"})
            if self._preserved is None:raise StataError("nothing to restore",622)
            if "not" not in opt:self.data,self.variable_labels,self.value_labels,self.value_label_names,self.formats,self.dataset_label,self._storage_types=copy.deepcopy(self._preserved)
            if "preserve" not in opt:self._preserved=None
            return
        if command in {"regress","areg","ivregress","logit","logistic","probit","poisson","predict","test","estimates","ttest"}:
            self._statistics(command,arg);return
        if command in {"label","format","destring","tostring","encode","decode","recast"}:
            self._metadata(command,arg);return
        if command=="scalar":
            if arg.startswith("list"):
                self.emit(str(self.scalars));return
            if arg.startswith("drop "):
                for name in arg[5:].split():self.scalars.pop(name,None)
                return
            m=re.fullmatch(r'(\w+)\s*=\s*(.*)',arg)
            if not m:raise StataError("scalar assignment required")
            self.scalars[m[1]]=self.evaluate(m[2]);self.rc=0;return
        if command=="matrix":self._matrix(arg);return
        if command=="return":
            if arg=="list":self.emit(str(self.returns));return
            if arg=="clear":self.returns.clear();return
            m=re.fullmatch(r'(scalar|local)\s+(\w+)\s*(?:=\s*)?(.*)',arg)
            if not m:raise StataError("unsupported return syntax")
            self.returns[m[2]]=self.evaluate(m[3]) if m[1]=="scalar" else unquote(m[3]);return
        if command=="ereturn":
            if arg=="list":self.emit(str(self.estimates));return
            raise StataError("ereturn assignment not implemented",199)
        if command in {"do","run","include"}:
            args=tokens(arg)
            if not args:raise StataError("do-file required")
            path=self.path(args[0],".do")
            def execute_file():
                try:self._execute(lex(read_source(path)))
                except _ExitFlow as flow:
                    if flow.code:raise StataError("do-file exited",flow.code)
            if command=="include":execute_file()
            else:
                rawargs=arg[len(args[0]):].strip()
                with self._local_scope(rawargs):execute_file()
            return
        if command=="program":
            if arg.startswith("drop "):
                for name in arg[5:].split():
                    if name=="_all":self.programs.clear();self._program_options.clear()
                    elif name in self.programs:self.programs.pop(name);self._program_options.pop(name,None)
                    else:raise StataError(f"program {name} not found",111)
                return
            if arg=="list":self.emit("\n".join(self.programs));return
            raise StataError("program definition requires do-file block")
        if command=="mata:":
            try:self.mata.run(arg)
            except MataError as ex:raise StataError(str(ex),199) from ex
            return
        if command in {"scatter","line","histogram","hist","twoway","graph"}:
            self._graph(command,arg);return
        if command in {"ssc","net","which","adopath","sysdir"}:
            self._package(command,arg);return
        if command in {"cd","pwd","mkdir","dir","ls","confirm","log","about","help","exit"}:
            self._system(command,arg);return
        if command in self.programs:
            with self._local_scope(arg):
                marker=None
                if self._program_options.get(command,{}).get("sortpreserve"):
                    marker=self._temporary_name()
                    self.data[marker]=np.arange(len(self.data))
                    self._hidden_variables.add(marker)
                    self._scope_tempvars.add(marker)
                try:self._execute(self.programs[command])
                except _ExitFlow as flow:
                    if flow.code:raise StataError("program exited",flow.code)
                finally:
                    if marker:
                        if marker in self.data:self.data=self.data.sort_values(marker,kind="stable",na_position="last").reset_index(drop=True)
                        self._hidden_variables.discard(marker)
            return
        ado=self._find_ado(command)
        if ado:
            self._execute(lex(read_source(ado)))
            if command in self.programs:self.command(text);return
            raise StataError(f"{command}.ado did not define {command}",199)
        raise StataError(f"command {command} is not implemented in OpenStat",199)

    def _set(self,arg):
        parts=arg.split(maxsplit=1)
        if len(parts)!=2:raise StataError("set requires name and value")
        name,value=parts
        if name=="obs":
            count=int(self.evaluate(value))
            if count<len(self.data):raise StataError("observation number out of range",2001)
            self.data=self.data.reindex(range(count));return
        if name=="seed":self.rng=np.random.default_rng(int(value));return
        if name=="type" and value in {"float","double"}:self._default_type=value;return
        if name=="more" and value in {"off","on"}:return
        if name=="varabbrev" and value=="off":return
        if name in {"linesize","rmsg"}:
            if name=="linesize" and not value.isdigit():raise StataError("invalid linesize")
            if name=="rmsg" and value not in {"on","off"}:raise StataError("invalid rmsg")
            return
        raise StataError(f"set {name} {value} not implemented",199)

    @staticmethod
    def _format(value,fmt=None):
        if fmt:
            m=re.fullmatch(r'%(-?\d+)(?:\.(\d+))?([efgs])',fmt)
            if not m:raise StataError(f"display format {fmt} unsupported")
            width=int(m[1]);align="<" if width<0 else ">";width=abs(width)
            if m[3]=="s":return format(str(value),f"{align}{width}")
            precision=int(m[2] or "0")
            if m[3]=="g" and not precision:precision=max(1,min(16,width-2))
            if pd.isna(value):return ".".rjust(width) if align==">" else ".".ljust(width)
            return format(float(value),f"{align}{width}.{precision}{m[3]}")
        if isinstance(value,str):return value
        if not np.isscalar(value):return str(value)
        if pd.isna(value):return "."
        return format(float(value),".15g") if isinstance(value,(float,np.floating,int,np.integer)) else str(value)

    @staticmethod
    def _display_markup(text):
        text=re.sub(r"\{hline(?:\s+(\d+))?\}",lambda m:"─"*int(m[1] or 1),text)
        text=re.sub(r"\{c\s+([^{}]+)\}",lambda m:{"|":"|","+":"+","-":"-"}.get(m[1],m[1]),text)
        return re.sub(r"\{(?:txt|text|res|result|err|error|input|com|cmd|reset)\}","",text)

    def _display(self,arg):
        parts=[];pending=arg.strip();fmt=None
        while pending:
            if style:=re.match(r"(?:as|in)\s+(?:text|txt|result|res|error|err|input|inp)\b\s*",pending):
                pending=pending[style.end():]
                continue
            if control:=re.match(r"_(col|column|skip|newline)\(([^()]*)\)\s*",pending):
                number=self.evaluate(control[2])
                if not np.isscalar(number) or not np.isfinite(number) or number!=int(number) or number<0:raise StataError("invalid display control")
                number=int(number)
                if control[1] in {"col","column"}:
                    column=len("".join(parts).rsplit("\n",1)[-1])
                    parts.append(" "*max(0,number-column-1))
                elif control[1]=="skip":parts.append(" "*number)
                else:parts.append("\n"*number)
                pending=pending[control.end():]
                continue
            if newline:=re.match(r"_n(?=\s|$)\s*",pending):
                parts.append("\n");pending=pending[newline.end():]
                continue
            if pending.startswith("%"):
                match=re.match(r'%[-\d.]+[efgs]',pending)
                if not match:raise StataError("unsupported display format")
                fmt=match[0];pending=pending[match.end():].strip()
                continue
            end=quoted_end(pending,0)
            if end is not None and not re.match(r"^[+*/^<>=!&|~-]",pending[end:].lstrip()):
                value=unquote(pending[:end])
                parts.append(self._display_markup(self._format(value,fmt) if fmt and fmt.endswith("s") else value))
                pending=pending[end:].strip()
                continue
            last_error=None
            boundaries=[i for i in outside_positions(pending) if pending[i].isspace()]+[len(pending)]
            for boundary in boundaries:
                expression=pending[:boundary].strip()
                following=pending[boundary:].lstrip()
                if not expression or following and (following[0] in "+-*/^<>=!&|" or re.match(r"^(?:and|or)\b",following)):continue
                try:value=self.evaluate(expression)
                except StataError as error:
                    last_error=error
                    continue
                parts.append(self._display_markup(self._format(value,fmt)))
                pending=following
                break
            else:
                if last_error:raise last_error
                raise StataError("invalid display expression")
        self.emit("".join(parts))

    def _generate(self,command,arg):
        parts=split_outside(arg);options=self.options(",".join(parts[1:]),{"by"} if command=="egen" else set())
        base,mask=self.qualifiers(parts[0])
        m=re.fullmatch(r'(?:(byte|int|long|float|double|str\d*|strL)\s+)?([A-Za-z_]\w*)\s*=\s*(.*)',base)
        if not m:raise StataError("invalid assignment syntax")
        storage,name,expression=m.groups()
        if command=="replace":
            if storage:raise StataError("replace does not accept a storage type")
            if name not in self.data:raise StataError(f"variable {name} not found",111)
        else:self._new_name(name)
        # Stata evaluates self-referencing subscripts sequentially. A vectorized
        # snapshot would silently break carry-forward recurrences.
        recursive=command in {"generate","replace"} and re.search(r'\b'+re.escape(name)+r'\s*\[',expression)
        if recursive:
            if command=="generate":raise StataError(f"variable {name} not found",111)
            if storage not in {None,"double","float"}:raise StataError("recursive assignments require numeric floating storage",199)
            try:
                for index in self.data.index[mask]:
                    self._tick();self._observation=int(index)
                    value=self.evaluate(expression)
                    if not np.isscalar(value):raise StataError("unsupported recursive vector expression",199)
                    dtype=self.data[name].dtype
                    if dtype==np.dtype("float32"):
                        value=np.float32(value)
                        if not np.isfinite(value) or abs(value)>np.finfo(np.float32).max/2:value=np.nan
                    self.data.at[index,name]=value
            finally:self._observation=None
            return
        if command=="egen":
            mm=re.fullmatch(r'(\w+)\((.*)\)',expression)
            if not mm:raise StataError("egen function required")
            function,source=mm.groups()
            if options.get("by"):
                self._by(f"{options['by']}: egen {name} = {expression}",sort=True);return
            if function in {"rowtotal","rowmean","rowmin","rowmax","rownonmiss","rowmiss"}:
                columns=self.varlist(source);frame=self.data[columns]
                funcs={"rowtotal":lambda:frame.sum(axis=1),"rowmean":lambda:frame.mean(axis=1),
                       "rowmin":lambda:frame.min(axis=1),"rowmax":lambda:frame.max(axis=1),
                       "rownonmiss":lambda:frame.notna().sum(axis=1),"rowmiss":lambda:frame.isna().sum(axis=1)}
                value=funcs[function]()
            elif function in {"group","tag"}:
                columns=self.varlist(source)
                valid=self.data[columns].notna().all(axis=1) & self.data[columns].ne("").all(axis=1) & mask
                if function=="group":
                    codes=pd.Series(np.nan,index=self.data.index)
                    codes.loc[valid]=pd.factorize(pd.MultiIndex.from_frame(self.data.loc[valid,columns]),sort=True)[0]+1
                    value=codes
                else:
                    value=pd.Series(0,index=self.data.index)
                    value.loc[valid]=(~self.data.loc[valid].duplicated(columns)).astype(int)
            else:
                source_value=self.evaluate(source)
                values=pd.Series(source_value,index=self.data.index).loc[mask].dropna()
                if function=="count":values=values.loc[values.ne("")]
                functions={"mean":values.mean,"total":values.sum,"sum":values.sum,"min":values.min,
                           "max":values.max,"count":values.count,"sd":values.std,"median":values.median}
                if function not in functions:raise StataError(f"egen {function}() not implemented",199)
                value=functions[function]()
        else:
            self._assignment_mask=mask
            try:value=self.evaluate(expression)
            finally:self._assignment_mask=None
        value=pd.Series(value,index=self.data.index) if np.isscalar(value) else pd.Series(np.asarray(value),index=self.data.index)
        if pd.api.types.is_bool_dtype(value):value=value.astype(float)
        if storage is None and command!="replace" and pd.api.types.is_numeric_dtype(value):storage=self._default_type
        if storage and storage.startswith("str"):
            value=value.fillna("").astype(str)
            if storage[3:].isdigit():value=value.str.slice(0,int(storage[3:]))
        elif storage:
            numeric=pd.to_numeric(value,errors="raise")
            if storage in {"byte","int","long"}:
                bounds={"byte":(-127,100),"int":(-32767,32740),"long":(-2147483647,2147483620)}[storage]
                numeric=np.trunc(numeric).where(numeric.between(*bounds))
            with np.errstate(over="ignore",invalid="ignore"):
                value=numeric.astype(np.float32 if storage=="float" else np.float64)
            if storage in {"float","double"}:
                value=value.where(value.abs()<=np.finfo(np.float32 if storage=="float" else np.float64).max/2)
        if command!="replace":
            self.data[name]="" if value.dtype==object or pd.api.types.is_string_dtype(value.dtype) else pd.Series(np.nan,index=self.data.index,dtype=value.dtype)
        elif self.data[name].dtype==np.dtype("float32"):
            with np.errstate(over="ignore",invalid="ignore"):
                value=value.astype(np.float32)
            value=value.where(value.abs()<=np.finfo(np.float32).max/2)
        try:self.data.loc[mask,name]=value.loc[mask]
        except (ValueError,TypeError) as ex:raise StataError(f"type mismatch: {ex}",109) from ex
        if storage:self._storage_types[name]=storage

    def _by(self,arg,sort=False):
        parts=split_outside(arg,":")
        if len(parts)!=2:raise StataError("by requires varlist: command")
        group_spec,body=parts
        match=re.fullmatch(r'([^()]+)(?:\(([^()]+)\))?',group_spec)
        if not match:raise StataError("invalid by variables")
        names=self.varlist(match[1]);sortnames=names+self.varlist(match[2] or "",default=False)
        allowed={"egen","generate","replace","summarize","count","assert","duplicates","recode","expand","isid"}
        allowed.update(name for name,options in self._program_options.items() if options.get("byable")=="recall")
        _,plain_body=self._prefixes(body)
        cmd=ALIASES.get(plain_body.split()[0],plain_body.split()[0])
        if cmd not in allowed and cmd not in self.programs:
            ado=self._find_ado(cmd)
            if ado:
                self._execute(lex(read_source(ado)))
                allowed.update(name for name,options in self._program_options.items() if options.get("byable")=="recall")
        if cmd not in allowed:raise StataError(f"by prefix not implemented for {cmd}",199)
        original=self.data
        sorted_data=original.sort_values(sortnames,kind="stable",na_position="last").reset_index(drop=True)
        if not sort and not original[sortnames].reset_index(drop=True).equals(sorted_data[sortnames]):
            raise StataError("not sorted",5)
        pieces=[]
        try:
            for _,frame in sorted_data.groupby(names,dropna=False,sort=False):
                self._tick();self.data=frame.reset_index(drop=True).copy();self.command(body);pieces.append(self.data)
            self.data=pd.concat(pieces,ignore_index=True) if pieces else sorted_data
        except Exception:
            self.data=original
            raise

    def _describe(self,command,arg):
        parts=split_outside(arg)
        allowed={"detail","meanonly"} if command=="summarize" else {"simple","short"} if command=="describe" else {"noobs","clean","nolabel","separator"} if command=="list" else {"missing","nolabel","row","column"} if command=="tabulate" else set()
        opt=self.options(",".join(parts[1:]),allowed)
        base,mask=self.qualifiers(" "+parts[0] if parts[0].startswith(("if ","in ")) else parts[0])
        if command=="count":
            if base:raise StataError("count takes only if/in qualifiers")
            self.returns={"N":int(mask.sum())};self.emit(str(self.returns["N"]));return
        names=self.varlist(base)
        frame=self.data.loc[mask,names]
        if command=="summarize":
            self.returns={"N":0}
            if "meanonly" not in opt:self.emit("Variable            Obs          Mean       Std. dev.           Min           Max")
            for name in names:
                if not pd.api.types.is_numeric_dtype(frame[name]):continue
                values=frame[name].dropna().astype(float)
                self.returns={"N":len(values),"sum_w":len(values),"mean":values.mean(),"Var":values.var(ddof=1),
                              "sd":values.std(ddof=1),"min":values.min(),"max":values.max(),"sum":values.sum()}
                if "detail" in opt:
                    for p in [1,5,10,25,50,75,90,95,99]:
                        # Stata's default empirical percentile: average adjacent at integer N*p.
                        n=len(values);v=np.sort(values.to_numpy());q=n*p/100
                        self.returns[f"p{p}"]=np.nan if not n else (v[int(q)-1]+v[int(q)])/2 if q.is_integer() and 0<q<n else v[min(int(q),n-1)]
                    from scipy.stats import skew,kurtosis
                    self.returns["skewness"]=skew(values,bias=True)
                    self.returns["kurtosis"]=kurtosis(values,bias=True,fisher=False)
                r=self.returns
                if "meanonly" not in opt:self.emit(f"{name:<18} {r['N']:>6} {r['mean']:>13.7g} {r['sd']:>13.7g} {r['min']:>13.7g} {r['max']:>13.7g}")
            return
        if command in {"describe","codebook","inspect"}:
            self.returns={"N":len(self.data),"k":len(self.data.columns)}
            self.emit(f"Observations: {len(self.data):,}\nVariables: {len(self.data.columns):,}\n{self.dataset_label}")
            for name in names:
                self.emit(f"{name:<24} {str(self.data[name].dtype):<12} {self.variable_labels.get(name,'')}")
            return
        if command=="list":
            if len(frame)>1000:frame=frame.head(1000);self.emit("Showing first 1000 observations.")
            self.emit(frame.to_string(index="noobs" not in opt));return
        if command=="tabulate":
            if "missing" not in opt:frame=frame.replace("",np.nan)
            if len(names)==1:
                counts=frame[names[0]].value_counts(dropna="missing" not in opt,sort=False).sort_index()
                self.emit(counts.to_string());self.returns={"N":int(counts.sum()),"r":len(counts)}
            elif len(names)==2:
                table=pd.crosstab(frame[names[0]],frame[names[1]],dropna="missing" not in opt)
                self.emit(table.to_string());self.returns={"N":int(table.to_numpy().sum()),"r":len(table),"c":len(table.columns)}
            else:raise StataError("tabulate supports one or two variables")
            return
        if command=="correlate":
            numeric=frame.select_dtypes(include="number").dropna()
            if len(numeric.columns)!=len(names):raise StataError("numeric variables required",109)
            result=numeric.corr();self.matrices["r(C)"]=result.to_numpy();self.returns={"N":len(numeric)}
            if len(names)==2:self.returns["rho"]=result.iloc[0,1]
            self.emit(result.to_string());return

    def _find_ado(self,name):
        if not re.fullmatch(r"[A-Za-z_]\w*",name):return None
        for directory in self._ado_prepend+[self.cwd]:
            path=directory/(name+".ado")
            if path.is_file():return path
        installed=self.packages.find(name)
        if installed:return installed
        for directory in self._ado_append:
            path=directory/(name+".ado")
            if path.is_file():return path
        return None

    def _package(self,command,arg):
        if command=="sysdir":
            words=tokens(arg)
            if len(words)!=3 or words[0]!="set" or words[1].upper()!="PLUS":raise StataError("only sysdir set PLUS is implemented",199)
            self.packages=PackageManager(self.path(words[2]));return
        if command=="adopath":
            if not arg:
                for index,path in enumerate(self._ado_prepend+[self.cwd,self.packages.root]+self._ado_append,1):self.emit(f"[{index}] {path}")
                return
            match=re.fullmatch(r"(\+\+|\+|-)\s*(.+)",arg)
            if not match or len(tokens(match[2]))!=1:raise StataError("adopath requires +, ++ or - and one directory")
            path=self.path(match[2]).resolve()
            self._ado_prepend=[entry for entry in self._ado_prepend if entry!=path]
            self._ado_append=[entry for entry in self._ado_append if entry!=path]
            if match[1]=="++":self._ado_prepend.insert(0,path)
            elif match[1]=="+":self._ado_append.append(path)
            return
        if command=="which":
            if " " in arg:raise StataError("which requires one command")
            path=self._find_ado(arg)
            if path:self.emit(str(path));return
            if arg in self.programs or arg in {"regress","summarize","generate","replace","use","save","ssc","mata","graph","display"}:
                self.emit(f"{arg}: OpenStat built-in subset");return
            raise StataError(f"command {arg} not found",111)
        parts=split_outside(arg);words=tokens(parts[0])
        action=words[0] if words else ""
        allowed={"replace","from","all"} if action in {"install","inst"} else {"from"} if action in {"describe","desc"} else set()
        opt=self.options(" ".join(parts[1:]),allowed)
        if command=="ssc" and words==["dir"]:
            for entry in self.packages.installed():self.emit(entry["name"]+" (execution compatibility unverified)")
            return
        if len(words)!=2 or action not in {"install","inst","describe","desc","uninstall"}:raise StataError(f"unsupported {command} package subcommand",199)
        source=unquote(opt["from"]) if "from" in opt else None
        if source is not None and not re.match(r"^https?://",source,re.I):source=str(self.path(source).resolve())
        if command=="net" and action in {"install","inst","describe","desc"} and source is None:raise StataError(f"net {action} requires from(URL)")
        try:
            if action in {"describe","desc"}:
                description=self.packages.describe(words[1],source)
                self.emit("\n".join(description["description"]))
                self.emit(f"{len(description['files'])} files for {description['platform']}")
            elif action=="uninstall":
                result=self.packages.uninstall(words[1])
                self.emit(f"uninstalled {result['name']}: {len(result['removed'])} files removed; {len(result['preserved'])} edited files preserved")
            else:
                manifest=self.packages.install(words[1],source,"replace" in opt,all_files="all" in opt,ancillary_dir=self.cwd)
                self.emit(f"installed {manifest['name']}: {len(manifest['files'])} files; execution compatibility unverified")
        except (PackageError,OSError) as ex:raise StataError(f"package {action} failed: {ex}",601) from ex

    def _system(self,command,arg):
        if command=="cd":
            path=self.path(arg)
            if not path.is_dir():raise StataError("directory not found",170)
            self.cwd=path.resolve();self.emit(str(self.cwd));return
        if command=="pwd":
            if arg:raise StataError("pwd takes no arguments")
            self.emit(str(self.cwd));return
        if command=="mkdir":self.path(arg).mkdir();return
        if command in {"dir","ls"}:
            self.emit("\n".join(p.name for p in sorted(self.cwd.glob(unquote(arg) or "*"))));return
        if command=="confirm":
            parts=tokens(arg)
            if len(parts)<2:raise StataError("confirm requires type and name")
            if parts[0]=="file":
                if not self.path(parts[1]).is_file():raise StataError("file not found",601)
            elif parts[0]=="variable":self.varlist(" ".join(parts[1:]))
            elif parts[:2]==["new","variable"]:
                for name in parts[2:]:self._new_name(name)
            elif parts[0] in {"numeric","string"} and parts[1]=="variable":
                for name in self.varlist(" ".join(parts[2:])):
                    numeric=pd.api.types.is_numeric_dtype(self.data[name])
                    if numeric!=(parts[0]=="numeric"):raise StataError("type mismatch",109)
            else:raise StataError("confirm target not implemented",199)
            return
        if command=="log":
            if arg=="close":
                if self._log:self._log.close();self._log=None
                else:raise StataError("no log open",606)
                return
            parts=split_outside(arg);words=tokens(parts[0]);opt=self.options(",".join(parts[1:]),{"replace","append","text"})
            if len(words)!=2 or words[0]!="using":raise StataError("only text log using/close implemented",199)
            if self._log:raise StataError("log already open",604)
            path=self.path(words[1],".log")
            if path.exists() and not ("replace" in opt or "append" in opt):raise StataError("log exists",602)
            self._log=path.open("a" if "append" in opt else "w",encoding="utf-8");return
        if command=="about":
            self.emit("OpenStat 0.1.0 | MIT | Independent experimental runtime\nStata-style subset; arbitrary do-file and Mata compatibility not established.");return
        if command=="help":
            self.emit("OpenStat implements a documented subset of do-file syntax.\nSee docs/compatibility.md for commands, limits and verification.\nUnsupported commands/options stop with an explicit return code.")
            if arg:self.emit(f"Requested command: {arg}")
            return
        if command=="exit":
            parts=split_outside(arg)
            self.options(" ".join(parts[1:]),{"clear"})
            if parts[0] and not re.fullmatch(r"\d+",parts[0]):raise StataError("exit requires an integer return code")
            code=int(parts[0] or "0")
            raise _ExitFlow(code)
