"""Small numerical result adapter shared by independent linear estimators."""
from __future__ import annotations

from types import SimpleNamespace
import re

import numpy as np
import pandas as pd
from scipy import stats


class LinearResult:
    def __init__(self,params,covariance,df_resid,nobs,residuals=None):
        self.params=pd.Series(params,dtype=float)
        self._covariance=pd.DataFrame(covariance,index=self.params.index,columns=self.params.index)
        self.bse=pd.Series(np.sqrt(np.maximum(np.diag(covariance),0)),index=self.params.index)
        self.df_resid=df_resid;self.nobs=nobs;self.resid=residuals

    def cov_params(self):return self._covariance.copy()

    def predict(self,frame):
        if isinstance(frame,pd.DataFrame):return frame.reindex(columns=self.params.index).to_numpy()@self.params.to_numpy()
        return np.asarray(frame)@self.params.to_numpy()

    def f_test(self,constraints):
        names=list(self.params.index);rows=[];targets=[]
        for constraint in str(constraints).split(','):
            lhs,_,rhs=constraint.partition('=');row=np.zeros(len(names));value=0.
            for text,sign in ((lhs,1),(rhs or '0',-1)):
                text=text.strip()
                if text in names:row[names.index(text)]+=sign
                else:
                    try:value-=sign*float(text)
                    except ValueError:raise ValueError('test currently requires coefficient = coefficient or number')
            rows.append(row);targets.append(value)
        r=np.asarray(rows);difference=r@self.params.to_numpy()-targets
        covariance=r@self._covariance.to_numpy()@r.T
        rank=np.linalg.matrix_rank(covariance)
        if not rank:raise ValueError('constraint covariance has zero rank')
        statistic=float(difference@np.linalg.pinv(covariance)@difference/rank)
        return SimpleNamespace(fvalue=statistic,pvalue=float(stats.f.sf(statistic,rank,self.df_resid)),df_num=rank,df_denom=self.df_resid)


def post_linear_result(engine,params,covariance,valid,design,estimates,residuals=None):
    engine._fit=LinearResult(params,covariance,estimates.get('df_r',np.inf),estimates['N'],residuals)
    engine._model_columns=[name for name in params.index if name!='_cons']
    engine._model_constant='_cons' in params.index
    engine._fit_design=design;engine._sample_mask=valid.copy();engine.estimates=estimates
    engine.coefficients={name:(float(value),float(engine._fit.bse[name])) for name,value in params.items()}
    if design:engine.coefficients.update({name:(0.,0.) for name in design.omitted})
    engine.matrices['e(b)']=params.to_numpy()[None,:];engine.matrices['e(V)']=np.asarray(covariance)
    engine._fit_residuals=pd.Series(np.nan,index=engine.data.index)
    if residuals is not None:engine._fit_residuals.loc[valid]=np.asarray(residuals)
    engine.emit(f"{estimates['cmd']}: {estimates['depvar']}    Number of obs = {estimates['N']}")
    engine.emit('Variable              Coefficient      Std. err.')
    for name,(coefficient,error) in engine.coefficients.items():engine.emit(f'{name:<22}{coefficient:>13.8g}  {error:>13.8g}')
