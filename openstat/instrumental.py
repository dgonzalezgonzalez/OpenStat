"""Independent two-stage least-squares estimator and covariance calculations."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .design import build_design,parse_weight,independent_columns
from .linear import post_linear_result
from .parser import split_outside


def instrumental_regression(engine,command,arg):
    from .errors import StataError
    parts=split_outside(arg)
    options=engine.options(' '.join(parts[1:]),{'robust','vce','cluster','small','noconstant','first','level'})
    specification=parts[0]
    if command=='ivregress':
        words=specification.split(maxsplit=1)
        if len(words)!=2 or words[0]!='2sls':raise StataError('ivregress currently supports 2sls',199)
        specification=words[1]
    specification,weight_kind,weight_expression=parse_weight(specification)
    if weight_kind=='iw':raise StataError('IV importance weights not implemented',199)
    specification,valid=engine.qualifiers(specification)
    match=re.search(r'\(([^()]*)=([^()]*)\)',specification)
    if not match:raise StataError('endogenous variables and excluded instruments required',198)
    endogenous_spec,instrument_spec=match.groups()
    ordinary=(specification[:match.start()]+' '+specification[match.end():]).strip().split(maxsplit=1)
    if not ordinary or ordinary[0] not in engine.data:raise StataError('dependent variable not found',111)
    dependent=ordinary[0];exogenous_spec=ordinary[1] if len(ordinary)>1 else ''
    weights=pd.Series(1.,index=engine.data.index)
    if weight_kind:
        weights=pd.Series(engine.evaluate(weight_expression),index=engine.data.index,dtype=float)
        if weights.lt(0).any():raise StataError('negative weights not allowed',402)
        if weight_kind=='fw' and weights.dropna().mod(1).ne(0).any():raise StataError('frequency weights must be integers',401)
        valid &= weights.gt(0)&np.isfinite(weights)
    valid &= engine.data[dependent].notna()
    exogenous=build_design(engine,exogenous_spec,valid)
    endogenous=build_design(engine,endogenous_spec,valid)
    instruments=build_design(engine,instrument_spec,valid)
    design=build_design(engine,(exogenous_spec+' '+endogenous_spec).strip(),valid)
    all_inputs=pd.concat([design.frame,instruments.frame],axis=1)
    valid &= all_inputs.notna().all(axis=1)&np.isfinite(all_inputs).all(axis=1)
    cluster=options.get('cluster');vce=options.get('vce','robust' if 'robust' in options or weight_kind=='pw' else 'unadjusted')
    if cluster:
        if 'vce' in options:raise StataError('cluster() and vce() may not be combined',198)
        vce='cluster '+str(cluster)
    if str(vce).startswith('cluster '):
        names=str(vce).split()[1:]
        if len(names)!=1:raise StataError('multiway IV cluster covariance not implemented',199)
        cluster=names[0];engine.varlist(cluster);valid &= engine.data[cluster].notna()
    elif vce not in {'unadjusted','robust','ols'}:raise StataError('IV covariance option unsupported',199)
    x=design.frame.loc[valid].astype(float).copy();z=pd.concat([exogenous.frame,instruments.frame],axis=1).loc[valid].copy()
    if z.columns.duplicated().any():z=z.loc[:,~z.columns.duplicated()]
    constant='noconstant' not in options
    x,omitted=independent_columns(x,constant=constant);z,_=independent_columns(z,constant=constant)
    if constant:x['_cons']=1.;z['_cons']=1.
    if len(z.columns)<len(x.columns):raise StataError('equation not identified: too few excluded instruments',481)
    n=len(x);w=weights.loc[valid].to_numpy();N=int(w.sum()) if weight_kind=='fw' else n;k=len(x.columns)
    if N<=k:raise StataError('insufficient observations',2001)
    xx=x.to_numpy();zz=z.to_numpy();y=engine.data.loc[valid,dependent].to_numpy(dtype=float)
    z_cross=zz.T@(w[:,None]*zz)
    if np.linalg.matrix_rank(z_cross)<len(z.columns):raise StataError('instrument matrix is rank deficient',481)
    projected=zz@np.linalg.solve(z_cross,zz.T@(w[:,None]*xx))
    cross=xx.T@(w[:,None]*projected)
    if np.linalg.matrix_rank(cross)<k:raise StataError('equation not identified: projected regressors lack rank',481)
    bread=np.linalg.inv(cross);beta=bread@(projected.T@(w*y));residual=y-xx@beta
    rss_raw=float(np.sum(w*residual**2));small='small' in options
    clusters=0
    if vce in {'unadjusted','ols'}:covariance=bread*rss_raw/(N-k if small else N)
    elif vce=='robust':
        frequency=w if weight_kind=='fw' else w*w
        covariance=bread@(projected.T@((frequency*residual**2)[:,None]*projected))@bread
        if small:covariance*=N/(N-k)
    else:
        scores=pd.DataFrame(projected*(w*residual)[:,None]).groupby(engine.data.loc[valid,cluster].to_numpy(),sort=False).sum().to_numpy();clusters=len(scores)
        if clusters<2:raise StataError('at least two clusters required',459)
        covariance=bread@(scores.T@scores)@bread
        if small:covariance*=(N-1)*clusters/((N-k)*(clusters-1))
    rss=rss_raw*(n/w.sum() if weight_kind in {'aw','pw'} else 1.)
    ymean=np.average(y,weights=w) if constant else 0.;tss=float(np.sum(w*(y-ymean)**2))*(n/w.sum() if weight_kind in {'aw','pw'} else 1.)
    result={'N':N,'df_r':clusters-1 if small and clusters else N-k,'df_m':k-int(constant),'cmd':command,'depvar':dependent,'rank':k,
            'rss':rss,'mss':tss-rss,'r2':1-rss/tss if tss else np.nan,'r2_a':1-(rss/(N-k))/(tss/(N-int(constant))) if tss else np.nan,
            'rmse':float(np.sqrt(rss/(N-k if small else N))),'small':int(small),'N_clust':clusters,'insts':instrument_spec,'instd':endogenous_spec}
    params=pd.Series(beta,index=x.columns)
    post_linear_result(engine,params,covariance,valid,design,result,residual)
    for name in omitted:engine.coefficients[name]=(0.,0.)
    if 'first' in options:
        engine.emit('First-stage projections:')
        for name in endogenous.frame:
            coefficients=np.linalg.solve(z_cross,zz.T@(w*endogenous.frame.loc[valid,name].to_numpy()))
            engine.emit(name+': '+', '.join(f'{term}={value:.8g}' for term,value in zip(z.columns,coefficients)))
