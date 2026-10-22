"""Factor-variable and time-series design expansion for independent estimators."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations,product
import re

import numpy as np
import pandas as pd


@dataclass
class Design:
    frame: pd.DataFrame
    specification: str
    levels: dict
    omitted: dict
    sources: list


def model_tokens(text):
    """Whitespace-separated model terms with parenthesis binding preserved."""
    result=[];depth=0;start=0
    for i,char in enumerate(text):
        if char=='(':depth+=1
        elif char==')':depth-=1
        elif char.isspace() and depth==0:
            if text[start:i].strip():result.append(text[start:i].strip())
            start=i+1
    if text[start:].strip():result.append(text[start:].strip())
    if depth:raise ValueError('unbalanced model parentheses')
    return result


def _bound_terms(term):
    match=re.search(r'\(([^()]*)\)',term)
    if not match:return [term]
    # ib(first/last) selects a base rather than binding a variable list.
    if term[:match.start()].endswith('ib'):
        return [term]
    before,after=term[:match.start()],term[match.end():]
    return [value for choice in model_tokens(match[1]) for value in _bound_terms(before+choice+after)]


def parse_weight(text):
    match=re.search(r'\[\s*(aweight|aw|fweight|fw|pweight|pw|iweight|iw)\s*=\s*([^\[\]]+)\]',text,re.I)
    if not match:return text,None,None
    kind={'aweight':'aw','fweight':'fw','pweight':'pw','iweight':'iw'}.get(match[1].lower(),match[1].lower())
    return (text[:match.start()]+' '+text[match.end():]).strip(),kind,match[2].strip()


def time_series(engine,spec):
    """Lookup lags/leads by declared panel/time keys, preserving gaps."""
    from .engine import StataError
    match=re.fullmatch(r'([LFDS])(\d*)\.(.+)',spec,re.I)
    if not match:
        if spec not in engine.data:raise StataError(f'variable {spec} not found',111)
        return engine.data[spec]
    kind,count,source=match.groups();kind=kind.upper();count=int(count or 1)
    panel=getattr(engine,'_panel_variable',None);time=getattr(engine,'_time_variable',None)
    if not time:raise StataError('time variable not set; use tsset or xtset',111)
    if count<0:raise StataError('invalid time-series order',198)
    current=time_series(engine,source)
    if kind=='D':
        for _ in range(count):
            current=current-time_series_lookup(engine,current,1,panel,time)
        return current
    if kind=='S':return current-time_series_lookup(engine,current,count,panel,time)
    return time_series_lookup(engine,current,count if kind=='L' else -count,panel,time)


def time_series_lookup(engine,values,lag,panel,time):
    keys=[panel,time] if panel else [time]
    frame=engine.data[keys].copy();frame['__value']=np.asarray(values)
    shifted=frame.copy();shifted[time]=shifted[time]+lag*getattr(engine,'_time_delta',1)
    # Keys were uniqueness-checked at declaration and are rechecked on lookup.
    if frame.duplicated(keys).any():
        from .engine import StataError
        raise StataError('repeated time values within panel',451)
    result=frame[keys].merge(shifted,on=keys,how='left',sort=False)['__value']
    result.index=engine.data.index
    return result


def build_design(engine,specification,mask=None,training=None):
    from .engine import StataError
    mask=pd.Series(True,index=engine.data.index) if mask is None else pd.Series(mask,index=engine.data.index)
    frame=pd.DataFrame(index=engine.data.index);levels=dict(training.levels) if training else {};omitted={};sources=[]
    terms=[expanded for term in model_tokens(specification) for expanded in _bound_terms(term)]

    def atom(text,interaction=False):
        match=re.fullmatch(r'(i|c|ibn|ib(?:\d+|\(first\)|\(last\))|b\d+|\d+)\.(.+)',text)
        prefix,name=match.groups() if match else ('i' if interaction else 'c',text)
        if name not in sources:sources.append(name)
        values=time_series(engine,name)
        if not pd.api.types.is_numeric_dtype(values):raise StataError(f'{name}: numeric variables required',109)
        if prefix=='c':return {('c.'+name if interaction else name):values.astype(float)}
        active=values.loc[mask].dropna()
        if (active<0).any() or (active!=np.floor(active)).any():raise StataError(f'{name}: factor variables may not contain negative or noninteger values',452)
        known=levels.setdefault(name,sorted(active.unique().tolist()))
        if not known:return {}
        if prefix.isdigit():selected=[int(prefix)];base=None
        elif prefix=='ibn':selected=known;base=None
        else:
            if prefix in {'i','ib(first)'}:base=known[0]
            elif prefix=='ib(last)':base=known[-1]
            else:base=int(re.sub(r'\D','',prefix))
            selected=[level for level in known if level!=base]
            omitted[f'{int(base)}b.{name}']=0.
        result={}
        for level in selected:
            result[f'{int(level)}.{name}']=(values==level).astype(float).where(values.notna())
        return result

    def add(term):
        factors=term.split('#')
        if len(factors)>8:raise StataError('at most eight-way factor interactions supported',198)
        if len(factors)==1:
            text=factors[0]
            if not re.match(r'(?:i|c|ib[^.]*|b\d+|\d+)\.',text) and not re.match(r'[LFDS]\d*\.',text,re.I):
                names=engine.varlist(text)
                for name in names:
                    for label,value in atom('c.'+name).items():frame[label]=value
            else:
                for label,value in atom(text).items():frame[label]=value
        else:
            choices=[list(atom(x,interaction=True).items()) for x in factors]
            for combo in product(*choices):
                name='#'.join(label for label,_ in combo)
                values=combo[0][1].copy()
                for _,value in combo[1:]:values=values*value
                frame[name]=values

    for term in terms:
        if '##' in term:
            factors=term.split('##')
            for k in range(1,len(factors)+1):
                for subset in combinations(factors,k):add('#'.join(subset))
        else:add(term)
    return Design(frame,specification,levels,omitted,sources)


def independent_columns(frame,constant=True):
    """Omit redundant regressors while retaining intercept and leftmost terms."""
    selected=[];rank=0;values=np.empty((len(frame),0))
    if constant:
        values=np.ones((len(frame),1));rank=1
    for name in frame:
        candidate=np.column_stack([values,frame[name].to_numpy(dtype=float)])
        next_rank=np.linalg.matrix_rank(candidate)
        if next_rank>rank:selected.append(name);values=candidate;rank=next_rank
    omitted=[name for name in frame if name not in selected]
    return frame[selected],omitted
