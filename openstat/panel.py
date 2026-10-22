"""Panel declarations and fixed-effect model commands."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .parser import split_outside
from .design import build_design,parse_weight,model_tokens,independent_columns
from .linear import post_linear_result


class PanelCommands:
    def _panel_command(self,command,arg):
        from .engine import StataError
        if command not in {'xtset','tsset','xtdescribe','xtreg','areg','reghdfe'}:return False
        if command in {'xtset','tsset'}:
            parts=split_outside(arg);options=self.options(' '.join(parts[1:]),{'clear','delta','daily','weekly','monthly','quarterly','yearly','generic'})
            if 'clear' in options:
                if parts[0]:raise StataError('clear does not accept variables',198)
                self._panel_variable=None;self._time_variable=None;return True
            names=parts[0].split()
            if not names:
                self.emit(f'Panel: {getattr(self,"_panel_variable",None)}; time: {getattr(self,"_time_variable",None)}');return True
            if len(names)>2:raise StataError('at most two declaration variables allowed',198)
            for name in names:self.varlist(name)
            panel=names[0] if command=='xtset' or len(names)==2 else None
            time=names[1] if len(names)==2 else names[0] if command=='tsset' else None
            if any(not pd.api.types.is_numeric_dtype(self.data[name]) for name in names):raise StataError('numeric declaration variables required',109)
            if time:
                keys=[panel,time] if panel else [time]
                if self.data.loc[self.data[keys].notna().all(axis=1)].duplicated(keys).any():raise StataError('repeated time values within panel',451)
            self._panel_variable=panel;self._time_variable=time;self._time_delta=float(options.get('delta',1))
            if self._time_delta<=0:raise StataError('delta must be positive',198)
            self.data=self.data.sort_values(names,kind='stable').reset_index(drop=True)
            self.returns={'panelvar':panel or '', 'timevar':time or '', 'tdelta':self._time_delta}
            self.emit(f'Panel: {panel}; time: {time}; delta: {self._time_delta:g}');return True
        if command=='xtdescribe':
            panel=getattr(self,'_panel_variable',None)
            if not panel:raise StataError('panel variable not set; use xtset',459)
            sizes=self.data.groupby(panel,dropna=True).size();self.returns={'N':len(self.data),'N_g':len(sizes),'Tmin':int(sizes.min()),'Tmax':int(sizes.max())};self.emit(str(self.returns));return True
        return self._fixed_effects(command,arg)

    def _fixed_effects(self,command,arg):
        from .engine import StataError
        parts=split_outside(arg)
        opts=self.options(' '.join(parts[1:]),{'absorb','fe','robust','vce','cluster','noconstant','keepsingletons','tolerance','iterate','residuals','level','nosample','noabsorbtest','dfabsorb'})
        if 'noconstant' in opts:raise StataError('noconstant for absorbed models is not implemented',199)
        if command=='xtreg':
            if 'fe' not in opts:raise StataError('xtreg currently requires fe',199)
            absorb=getattr(self,'_panel_variable',None)
            if not absorb:raise StataError('panel variable not set; use xtset',459)
        else:
            absorb=opts.get('absorb')
            if not isinstance(absorb,str) or not absorb.strip():raise StataError('absorb() is required',198)
        spec,weight_kind,weight_expression=parse_weight(parts[0]);base,valid=self.qualifiers(spec)
        words=base.split(maxsplit=1)
        if not words or words[0] not in self.data:raise StataError('dependent variable not found',111)
        dependent=words[0];outcome=self.data[dependent]
        if not pd.api.types.is_numeric_dtype(outcome):raise StataError('numeric outcome required',109)
        valid &= outcome.notna()&np.isfinite(outcome)
        weights=pd.Series(1.,index=self.data.index)
        if weight_kind:
            if weight_kind not in {'aw','pw','fw'}:raise StataError('unsupported weight type',199)
            weights=pd.Series(self.evaluate(weight_expression),index=self.data.index,dtype=float)
            if weights.lt(0).any():raise StataError('negative weights not allowed',402)
            if weight_kind=='fw' and weights.dropna().mod(1).ne(0).any():raise StataError('frequency weights must be integers',401)
            valid &= weights.gt(0)&np.isfinite(weights)
        design=build_design(self,words[1] if len(words)>1 else '',valid)
        valid &= design.frame.notna().all(axis=1)&np.isfinite(design.frame).all(axis=1)
        effects=[]
        for term in model_tokens(absorb):
            groupvars=[];slopes=[]
            if '##' in term:raise StataError('factorial absorb syntax is not implemented',199)
            for component in term.split('#'):
                if component.startswith('c.'):slopes.append(component[2:])
                else:groupvars.append(component[2:] if component.startswith('i.') else component)
            for name in groupvars+slopes:
                self.varlist(name);valid &= self.data[name].notna()
            if not groupvars:groupvars=[]
            effects.append({'groups':groupvars,'slopes':slopes,'name':term})
        if not effects:raise StataError('empty absorb specification',198)
        # reghdfe removes singleton groups iteratively unless explicitly retained.
        if command=='reghdfe' and 'keepsingletons' not in opts:
            for _ in range(100):
                previous=valid.copy()
                for effect in effects:
                    if effect['slopes'] or not effect['groups']:continue
                    counts=self.data.loc[valid].groupby(effect['groups'],dropna=False)[dependent].transform('size')
                    valid.loc[counts.index] &= counts.gt(1)
                if previous.equals(valid):break
        clustertext=opts.get('cluster')
        vcetype=opts.get('vce','robust' if 'robust' in opts or weight_kind=='pw' else 'ols')
        if clustertext:
            if 'vce' in opts:raise StataError('cluster() and vce() may not be combined',198)
            vcetype='cluster '+str(clustertext)
        if command=='xtreg' and vcetype=='robust':vcetype='cluster '+absorb
        cluster=None
        if str(vcetype).startswith('cluster '):
            clusterspec=str(vcetype).split()[1:]
            if len(clusterspec)!=1:raise StataError('multiway cluster covariance is not yet implemented',199)
            cluster=clusterspec[0];self.varlist(cluster);valid &= self.data[cluster].notna()
        elif vcetype not in {'ols','robust'}:raise StataError('unsupported covariance type',199)
        sample=self.data.loc[valid];n=len(sample)
        if not n:raise StataError('no observations',2000)
        w=weights.loc[valid].to_numpy();y=outcome.loc[valid].to_numpy(dtype=float)
        x=design.frame.loc[valid].to_numpy(dtype=float)
        terms=[];total_levels=0
        for effect in effects:
            group=effect['groups'];slopes=effect['slopes']
            codes=pd.factorize(pd.MultiIndex.from_frame(sample[group]),sort=True)[0] if group else np.zeros(n,dtype=int)
            basis=np.ones(n)
            for name in slopes:
                if not pd.api.types.is_numeric_dtype(sample[name]):raise StataError('numeric absorbed slopes required',109)
                basis*=sample[name].to_numpy(dtype=float)
            count=int(codes.max())+1;total_levels+=count
            terms.append((codes,basis,count,effect))
        has_intercept_effect=any(not effect['slopes'] for effect in effects)
        z=np.column_stack([y,x] if has_intercept_effect else [y,x,np.ones(n)])
        means=np.average(z,axis=0,weights=w);transformed=z.copy()
        tolerance=float(opts.get('tolerance',1e-10));iterations=int(opts.get('iterate',10000))
        if not (0<tolerance<1 and 1<=iterations<=100000):raise StataError('invalid projection convergence settings',198)
        for iteration in range(iterations):
            self._tick();previous=transformed.copy()
            for codes,basis,count,_ in terms:
                denominators=np.bincount(codes,weights=w*basis*basis,minlength=count)
                for column in range(transformed.shape[1]):
                    numerators=np.bincount(codes,weights=w*basis*transformed[:,column],minlength=count)
                    coefficients=np.divide(numerators,denominators,out=np.zeros(count),where=denominators>0)
                    transformed[:,column]-=basis*coefficients[codes]
            if np.max(np.abs(transformed-previous))<tolerance:break
        else:raise StataError('absorbed projections did not converge',430)
        if has_intercept_effect:
            transformed+=means;yy=transformed[:,0];xx=pd.DataFrame(transformed[:,1:],columns=design.frame.columns,index=sample.index)
            xx,omitted=independent_columns(xx,constant=True);xx['_cons']=1.
        else:
            yy=transformed[:,0];xx=pd.DataFrame(transformed[:,1:],columns=list(design.frame.columns)+['_cons'],index=sample.index)
            xx,omitted=independent_columns(xx,constant=False)
        # Exact absorbed rank for manageable problems. Large one/two categorical
        # effects use group/connected-component identities without dense dummies.
        if n*total_levels<=2_000_000 and total_levels<=1000:
            blocks=[]
            for codes,basis,count,_ in terms:
                block=np.zeros((n,count));block[np.arange(n),codes]=basis;blocks.append(block)
            absorbed_rank=int(np.linalg.matrix_rank(np.column_stack(blocks)))
        elif len(terms)==1:absorbed_rank=terms[0][2]
        elif len(terms)==2 and all(not term[3]['slopes'] for term in terms):
            first,second=terms;offset=first[2];parent=np.arange(offset+second[2])
            def find(a):
                while parent[a]!=a:parent[a]=parent[parent[a]];a=parent[a]
                return a
            for a,b in zip(first[0],second[0]+offset):parent[find(a)]=find(b)
            components=len({find(i) for i in range(len(parent))});absorbed_rank=total_levels-components
        else:raise StataError('exact degrees of freedom for this large absorb specification are not yet implemented',199)
        absorbed_df=absorbed_rank-int(has_intercept_effect);k=xx.shape[1]
        N=int(w.sum()) if weight_kind=='fw' else n;df_resid=N-k-absorbed_df
        if df_resid<=0:raise StataError('insufficient residual degrees of freedom',2001)
        matrix=xx.to_numpy();bread=np.linalg.inv(matrix.T@(w[:,None]*matrix));beta=bread@(matrix.T@(w*yy));residual=yy-matrix@beta
        rss_raw=float(np.sum(w*residual**2));denominator=df_resid;clusters=None
        if vcetype=='ols':covariance=bread*rss_raw/df_resid
        elif vcetype=='robust':
            frequency=w if weight_kind=='fw' else w*w
            covariance=bread@(matrix.T@((frequency*residual**2)[:,None]*matrix))@bread*N/df_resid
        else:
            grouped=pd.DataFrame(matrix*(w*residual)[:,None]).groupby(sample[cluster].to_numpy(),sort=False).sum().to_numpy();clusters=len(grouped)
            if clusters<2:raise StataError('at least two clusters required',459)
            if command=='xtreg':denominator=N-k
            elif command=='reghdfe':
                nested=all(effect['groups'] and not effect['slopes'] and sample.groupby(effect['groups'])[cluster].nunique().max()==1 for effect in effects)
                if nested:denominator=N-k
            covariance=bread@(grouped.T@grouped)@bread*clusters/(clusters-1)*(N-1)/denominator
        scale=n/w.sum() if weight_kind in {'aw','pw'} else 1.
        rss=rss_raw*scale;tss=float(np.sum(w*(y-np.average(y,weights=w))**2))*scale
        centered_within=transformed[:,0]-np.average(transformed[:,0],weights=w)
        within_tss=float(np.sum(w*centered_within**2))*scale
        results={'N':N,'df_r':clusters-1 if clusters else df_resid,'df_a':absorbed_df,'df_m':k-1,'cmd':command,'depvar':dependent,'rank':k,'rss':rss,'mss':tss-rss,'r2':1-rss/tss if tss else np.nan,'r2_a':1-(rss/df_resid)/(tss/(N-1)) if tss and N>1 else np.nan,'rmse':float(np.sqrt(rss/df_resid)),'r2_within':1-rss/within_tss if within_tss else np.nan,'converged':1,'absvars':absorb,'N_clust':clusters or 0}
        if command=='xtreg':results['N_g']=sample[absorb].nunique();results['r2_w']=results['r2_within']
        params=pd.Series(beta,index=xx.columns)
        post_linear_result(self,params,covariance,valid,design,results,residual)
        for name in omitted:self.coefficients[name]=(0.,0.)
        self._absorbed_effects=outcome.loc[valid]-design.frame.loc[valid].reindex(columns=[c for c in params.index if c!='_cons'],fill_value=0).to_numpy()@params.drop('_cons',errors='ignore').to_numpy()-params.get('_cons',0)-residual
        if isinstance(opts.get('residuals'),str):
            name=opts['residuals'];self._new_name(name);self.data[name]=self._fit_residuals
        return True
