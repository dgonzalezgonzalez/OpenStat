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
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .expression import Evaluator, ExpressionError, truth
from .mata import Mata, MataError
from .packages import PackageError, PackageManager, download
from .parser import Statement, lex, split_outside, tokens, unquote
from .statistics import StatisticalCommands
from .graphics import GraphicsCommands
from .data_commands import IOCommands
from .metadata import MetadataCommands


class StataError(Exception):
    def __init__(self, message, rc=198, line=None):
        super().__init__(message)
        self.rc, self.line = rc, line


class _ExitFlow(Exception):
    def __init__(self,code=0):self.code=code


@dataclass
class RunResult:
    output: str
    rc: int = 0
    line: int | None = None


ALIASES = {"gen":"generate","g":"generate","repl":"replace","su":"summarize","sum":"summarize",
           "summ":"summarize","reg":"regress","di":"display","dis":"display","disp":"display",
           "qui":"quietly","cap":"capture","noi":"noisily","des":"describe","desc":"describe",
           "l":"list","cou":"count","tab":"tabulate","corr":"correlate","ren":"rename",
           "mat":"matrix","loc":"local","glo":"global","prog":"program","ret":"return"}


class Engine(IOCommands, MetadataCommands, StatisticalCommands, GraphicsCommands):
    def __init__(self,cwd: Path|str|None=None, ado_dir: Path|str|None=None):
        self.cwd=Path(cwd or Path.cwd()).resolve()
        self.data=pd.DataFrame()
        self.variable_labels={}
        self.value_labels={}
        self.value_label_names={}
        self.formats={}
        self.dataset_label=""
        self.locals={}
        self.globals={}
        self.scalars={}
        self.matrices={}
        self.returns={}
        self.estimates={}
        self.coefficients={}
        self.programs={}
        self.stored_estimates={}
        self.graphs=[]
        self._figure=None
        self._fit=None
        self._model_columns=[]
        self._preserved=None
        self._cancel=threading.Event()
        self._run_lock=threading.Lock()
        self._depth=0
        self._steps=0
        self._output=[]
        self._quiet=0
        self._log=None
        self.rc=0
        self.rng=np.random.default_rng()
        self.packages=PackageManager(Path(ado_dir) if ado_dir else None)
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
            source=path.read_text(encoding="utf-8-sig")
        except (OSError,UnicodeError) as error:
            return RunResult(str(error),601)
        old=self.locals
        self.locals={}
        try:return self.run(source)
        finally:self.locals=old

    def path(self,value,extension=None):
        path=Path(unquote(value))
        if extension and not path.suffix:path=path.with_suffix(extension)
        return path if path.is_absolute() else self.cwd/path

    def expand(self,text):
        for _ in range(30):
            previous=text
            text=re.sub(r"`([^`']+)'",lambda m:str(self.locals.get(m[1],"")),text)
            text=re.sub(r'\$\{([A-Za-z_]\w*)\}|\$([A-Za-z_]\w*)',lambda m:str(self.globals.get(m[1] or m[2],"")),text)
            if text==previous:return text
        raise StataError("macro expansion recursion limit",920)

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
                    command=text.split()[0].lower()
                    command=ALIASES.get(command,command)
                    if command in {"forvalues","foreach","while","if"} and text.endswith("{"):
                        depth,j=1,i+1
                        while j<len(statements):
                            value=statements[j].text.strip()
                            if value.endswith("{"):depth+=1
                            if value=="}":depth-=1
                            if depth==0:break
                            j+=1
                        if depth:raise StataError("unclosed brace")
                        body=statements[i+1:j]
                        header=text[:-1].strip()
                        if command=="forvalues":
                            m=re.fullmatch(r'\w+\s+(\w+)\s*=\s*(-?\d+)\s*(?:/\s*(-?\d+)|\(\s*(-?\d+)\s*\)\s*(-?\d+))',header)
                            if not m:raise StataError("unsupported forvalues range")
                            start=int(m[2]);step=int(m[4]) if m[4] else 1;end=int(m[5] or m[3])
                            if step==0:raise StataError("zero loop increment")
                            for value in range(start,end+(1 if step>0 else -1),step):
                                self._tick();self.locals[m[1]]=str(value);self._execute(body)
                        elif command=="foreach":
                            m=re.fullmatch(r'foreach\s+(\w+)\s+(in|of\s+(?:varlist|numlist|local|global))\s+(.*)',header)
                            if not m:raise StataError("unsupported foreach syntax")
                            values=m[3]
                            if m[2]=="of local":values=str(self.locals.get(values,""))
                            elif m[2]=="of global":values=str(self.globals.get(values,""))
                            elif m[2]=="of varlist":values=" ".join(self.varlist(values))
                            elif m[2]=="of numlist":
                                mm=re.fullmatch(r'(\d+)/(\d+)',values)
                                if mm:values=" ".join(map(str,range(int(mm[1]),int(mm[2])+1)))
                            for value in tokens(values):
                                self._tick();self.locals[m[1]]=unquote(value);self._execute(body)
                        elif command=="while":
                            raw_condition=statement.text.strip()[:-1].strip()[5:].strip()
                            while self._condition(self.expand(raw_condition)):
                                self._tick();self._execute(body)
                        elif self._condition(header[2:].strip()):
                            self._execute(body)
                        i=j+1;continue
                    if command=="program" and re.match(r'prog(?:ram)?\s+(?:define\s+)?',text) and not text.startswith("program drop"):
                        parts=split_outside(text)
                        head=tokens(parts[0]);name=head[2] if len(head)>2 and head[1]=="define" else head[1]
                        self.options(",".join(parts[1:]),{"rclass","eclass"})
                        j=i+1
                        while j<len(statements) and statements[j].text.strip()!="end":j+=1
                        if j==len(statements):raise StataError("program missing end")
                        self.programs[name]=statements[i+1:j]
                        i=j+1;continue
                    if text in {"mata","mata:"}:
                        j=i+1
                        while j<len(statements) and statements[j].text.strip()!="end":j+=1
                        if j==len(statements):raise StataError("Mata block missing end")
                        try:self.mata.run("\n".join(self.expand(x.text) for x in statements[i+1:j]))
                        except MataError as ex:raise StataError(str(ex),199) from ex
                        i=j+1;continue
                    self.command(text)
                except (StataError,ValueError,OSError,KeyError,TypeError,IndexError) as ex:
                    if not isinstance(ex,StataError):ex=StataError(str(ex),198)
                    if ex.line is None:ex.line=statement.line
                    raise ex
                i+=1
        finally:
            self._depth-=1

    def options(self,text,allowed):
        output={}
        for match in re.finditer(r'([A-Za-z_]\w*)(?:\(([^()]*)\))?|\S+',text):
            if not match[1]:raise StataError(f"invalid option {match[0]}")
            name=match[1].lower()
            if name not in allowed:raise StataError(f"option {name} not allowed",198)
            output[name]=match[2] if match[2] is not None else True
        return output

    def varlist(self,text,default=True):
        if not text.strip():return list(self.data.columns) if default else []
        result=[]
        for token in text.split():
            if "-" in token and not any(c in token for c in "*?"):
                first,last=token.split("-",1);cols=list(self.data.columns)
                if first not in cols or last not in cols:raise StataError(f"variable {token} not found",111)
                names=cols[cols.index(first):cols.index(last)+1]
            else:names=[str(c) for c in self.data if fnmatch.fnmatchcase(str(c),token)]
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
        return data

    def _write(self,path,version=118):
        # pandas writer supports dta 114,117,118,119 (119 allows >32767 vars).
        value_labels={n:self.value_labels[label] for n,label in self.value_label_names.items() if n in self.data and label in self.value_labels}
        self.data.to_stata(path,write_index=False,version=version,data_label=self.dataset_label,
                           variable_labels={n:v for n,v in self.variable_labels.items() if n in self.data},value_labels=value_labels)

    def command(self,text):
        self._tick()
        words=text.split(maxsplit=1)
        command=ALIASES.get(words[0].lower(),words[0].lower())
        arg=words[1].strip() if len(words)>1 else ""
        if command in {"capture","quietly","noisily"}:
            if not arg:raise StataError("prefix requires command")
            oldquiet=self._quiet
            if command in {"capture","quietly"}:self._quiet+=1
            if command=="noisily":self._quiet=0
            try:
                self.command(arg);self.rc=0
            except (StataError,ValueError,OSError,KeyError,TypeError,IndexError) as ex:
                if command!="capture":raise
                self.rc=getattr(ex,"rc",198)
            finally:self._quiet=oldquiet
            return
        # _rc must remain visible to local assignment immediately after capture.
        previous_rc=self.rc
        if command not in {"local","global","display","scalar"}:self.rc=0
        if command in {"by","bysort"}:
            self._by(arg,sort=command=="bysort");return
        if command=="clear":
            if arg not in {"","all"}:raise StataError("unsupported clear target")
            self.data=pd.DataFrame();self.variable_labels={};self.value_labels={};self.value_label_names={};self.formats={};self.dataset_label=""
            if arg=="all":
                self.scalars.clear();self.matrices.clear();self.programs.clear();self.estimates.clear();self.returns.clear();self.mata.variables.clear()
                self.coefficients.clear();self.stored_estimates.clear();self._fit=None;self._model_columns=[]
            return
        if command=="set":
            self._set(arg);return
        if command=="version":
            m=re.fullmatch(r'(\d+(?:\.\d+)?)(?:\s*:\s*(.*))?',arg)
            if not m or float(m[1])>19.5:raise StataError("unsupported version request",9)
            if m[2]:self.command(m[2])
            return
        if command in {"local","global"}:
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
                if command=="tempfile":
                    root=Path(tempfile.mkdtemp(prefix="openstat-"));self.locals[name]=str(root/(name+".dta"))
                else:self.locals[name]="__"+name+str(self._steps)
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
            for mapping in (self.variable_labels,self.value_label_names,self.formats):
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
            self._preserved=copy.deepcopy((self.data,self.variable_labels,self.value_labels,self.value_label_names,self.formats,self.dataset_label));return
        if command=="restore":
            opt=self.options(arg.lstrip(", "),{"not","preserve"})
            if self._preserved is None:raise StataError("nothing to restore",622)
            if "not" not in opt:self.data,self.variable_labels,self.value_labels,self.value_label_names,self.formats,self.dataset_label=copy.deepcopy(self._preserved)
            if "preserve" not in opt:self._preserved=None
            return
        if command in {"regress","logit","logistic","probit","poisson","predict","test","estimates","ttest"}:
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
            old=self.locals
            if command!="include":self.locals={str(i):unquote(v) for i,v in enumerate(args[1:],1)}
            try:self._execute(lex(path.read_text(encoding="utf-8-sig")))
            except _ExitFlow as flow:
                if flow.code:raise StataError("do-file exited",flow.code)
            finally:self.locals=old
            return
        if command=="program":
            if arg.startswith("drop "):
                for name in arg[5:].split():
                    if name=="_all":self.programs.clear()
                    elif name in self.programs:self.programs.pop(name)
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
            old=self.locals
            args=tokens(arg);self.locals={str(i):unquote(v) for i,v in enumerate(args,1)};self.locals["0"]=arg
            try:self._execute(self.programs[command])
            except _ExitFlow as flow:
                if flow.code:raise StataError("program exited",flow.code)
            finally:self.locals=old
            return
        ado=self.packages.find(command)
        if ado:
            self._execute(lex(ado.read_text(encoding="utf-8-sig")))
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
        if name=="more" and value in {"off","on"}:return
        if name=="varabbrev" and value=="off":return
        if name in {"linesize","rmsg"}:
            if name=="linesize" and not value.isdigit():raise StataError("invalid linesize")
            if name=="rmsg" and value not in {"on","off"}:raise StataError("invalid rmsg")
            return
        raise StataError(f"set {name} {value} not implemented",199)

    @staticmethod
    def _format(value,fmt=None):
        if isinstance(value,str):return value
        if not np.isscalar(value):return str(value)
        if pd.isna(value):return "."
        if fmt:
            m=re.fullmatch(r'%(-?\d+)(?:\.(\d+))?([fg])',fmt)
            if not m:raise StataError(f"display format {fmt} unsupported")
            return format(float(value),f"{int(m[1])}.{m[2] or '0'}{m[3]}")
        return format(float(value),".15g") if isinstance(value,(float,np.floating,int,np.integer)) else str(value)

    def _display(self,arg):
        arg=re.sub(r'^as\s+(?:text|result|error)\s+',"",arg)
        parts=[];pending=arg.strip();fmt=None
        while pending:
            if pending.startswith('"'):
                match=re.match(r'"([^"\n]*)"',pending)
                if not match:raise StataError("unclosed string")
                parts.append(match[1]);pending=pending[match.end():].strip()
            elif pending.startswith("%"):
                match=re.match(r'%[-\d.]+[fg]',pending)
                if not match:raise StataError("unsupported display format")
                fmt=match[0];pending=pending[match.end():].strip()
            else:
                # Adjacent unquoted expressions require explicit operators.
                parts.append(self._format(self.evaluate(pending),fmt));pending=""
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
        if storage and storage.startswith("str"):
            value=value.fillna("").astype(str)
            if storage[3:].isdigit():value=value.str.slice(0,int(storage[3:]))
        elif storage:
            numeric=pd.to_numeric(value,errors="raise")
            if storage in {"byte","int","long"}:
                bounds={"byte":(-127,100),"int":(-32767,32740),"long":(-2147483647,2147483620)}[storage]
                numeric=np.trunc(numeric).where(numeric.between(*bounds))
            value=numeric.astype(np.float32 if storage=="float" else np.float64)
        if command!="replace":
            self.data[name]="" if value.dtype==object or pd.api.types.is_string_dtype(value.dtype) else np.nan
        try:self.data.loc[mask,name]=value.loc[mask]
        except (ValueError,TypeError) as ex:raise StataError(f"type mismatch: {ex}",109) from ex
        if storage:self.formats[name]=storage

    def _by(self,arg,sort=False):
        parts=split_outside(arg,":")
        if len(parts)!=2:raise StataError("by requires varlist: command")
        group_spec,body=parts
        match=re.fullmatch(r'([^()]+)(?:\(([^()]+)\))?',group_spec)
        if not match:raise StataError("invalid by variables")
        names=self.varlist(match[1]);sortnames=names+self.varlist(match[2] or "",default=False)
        allowed={"egen","generate","replace","summarize","count","assert"}
        cmd=ALIASES.get(body.split()[0],body.split()[0])
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
            self.emit("Variable            Obs          Mean       Std. dev.           Min           Max")
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
                self.emit(f"{name:<18} {r['N']:>6} {r['mean']:>13.7g} {r['sd']:>13.7g} {r['min']:>13.7g} {r['max']:>13.7g}")
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

    def _package(self,command,arg):
        if command=="sysdir":
            words=tokens(arg)
            if len(words)!=3 or words[:2]!=["set","PLUS"]:raise StataError("only sysdir set PLUS is implemented",199)
            self.packages=PackageManager(self.path(words[2]));return
        if command=="adopath":
            if arg:raise StataError("adopath edits unsupported; use sysdir set PLUS",199)
            self.emit(str(self.packages.root));return
        if command=="which":
            if " " in arg:raise StataError("which requires one command")
            path=self.packages.find(arg)
            if path:self.emit(str(path));return
            if arg in self.programs or arg in {"regress","summarize","generate","replace","use","save","ssc","mata","graph","display"}:
                self.emit(f"{arg}: OpenStat built-in subset");return
            raise StataError(f"command {arg} not found",111)
        parts=split_outside(arg);words=tokens(parts[0]);opt=self.options(",".join(parts[1:]),{"replace","from"})
        if command=="ssc" and words==["dir"]:
            for entry in self.packages.installed():self.emit(entry["name"]+" (execution compatibility unverified)")
            return
        if len(words)!=2 or words[0]!="install":raise StataError(f"{command} supports install only",199)
        if command=="net" and "from" not in opt:raise StataError("net install requires from(URL)")
        try:manifest=self.packages.install(words[1],unquote(opt["from"]) if "from" in opt else None,"replace" in opt)
        except (PackageError,OSError) as ex:raise StataError(f"package install failed: {ex}",601) from ex
        self.emit(f"installed {manifest['name']}: {len(manifest['files'])} files; execution compatibility unverified")

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
            code=int(arg or "0")
            raise _ExitFlow(code)
