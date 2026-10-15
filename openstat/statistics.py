"""Numerical commands; independently implemented through statsmodels/SciPy."""
from __future__ import annotations

import copy
import re

import numpy as np
import pandas as pd
from scipy import stats

from .parser import split_outside, tokens


class StatisticalCommands:
    def _statistics(self,command,arg):
        from .engine import StataError
        import statsmodels.api as sm
        parts=split_outside(arg)
        if command in {"regress","logit","logistic","probit","poisson"}:
            opt=self.options(",".join(parts[1:]),{"noconstant","robust","vce","level","nolog","irr","or"})
            if "irr" in opt and command!="poisson" or "or" in opt and command not in {"logit","logistic"}:
                raise StataError("invalid exponentiated coefficient display option",198)
            base,mask=self.qualifiers(parts[0]);names=base.split()
            if len(names)<1:raise StataError("dependent variable required")
            dependent=self.varlist(names[0])
            if len(dependent)!=1:raise StataError("one dependent variable required")
            regressors=[]
            for name in names[1:]:
                if any(c in name for c in "#.["):
                    raise StataError("factor variables, interactions, time operators and weights not implemented",199)
                regressors+=self.varlist(name)
            regressors=list(dict.fromkeys(regressors))
            frame=self.data[[dependent[0]]+regressors].copy()
            if any(not pd.api.types.is_numeric_dtype(frame[c]) for c in frame):raise StataError("numeric variables required",109)
            valid=mask & frame.notna().all(axis=1) & np.isfinite(frame).all(axis=1)
            sample=frame.loc[valid]
            x=sample[regressors].astype(float)
            if "noconstant" not in opt:x["_cons"]=1.
            y=sample[dependent[0]].astype(float)
            if len(x)==0:raise StataError("no observations",2000)
            if len(x)<=len(x.columns):raise StataError("insufficient residual degrees of freedom",2001)
            if np.linalg.matrix_rank(x.to_numpy())<len(x.columns):raise StataError("collinear design not supported; remove redundant regressors",459)
            vce=opt.get("vce", "robust" if "robust" in opt else "ols")
            cov_kw={}
            cov_type="nonrobust"
            if vce=="robust":cov_type="HC1" if command=="regress" else "HC0"
            elif isinstance(vce,str) and vce.startswith("cluster "):
                cluster=vce.split()[1]
                self.varlist(cluster)
                if self.data.loc[valid,cluster].isna().any():raise StataError("missing cluster values unsupported",459)
                cov_type="cluster";cov_kw={"groups":self.data.loc[valid,cluster].to_numpy(),"use_correction":True}
            elif vce not in {"ols","oim"}:raise StataError(f"vce({vce}) not implemented",199)
            if command=="regress":
                fit=sm.OLS(y,x).fit(cov_type=cov_type,cov_kwds=cov_kw,use_t=True)
            else:
                if command in {"logit","logistic","probit"} and not y.isin([0,1]).all():raise StataError("binary outcome must contain only 0/1",459)
                if command=="poisson" and ((y<0).any() or (y!=np.floor(y)).any()):raise StataError("nonnegative count outcome required",459)
                model={"logit":sm.Logit,"logistic":sm.Logit,"probit":sm.Probit,"poisson":sm.Poisson}[command](y,x)
                fit_options={"disp":False,"maxiter":100}
                if cov_type!="nonrobust":
                    fit_options["cov_type"]=cov_type
                    if cov_kw:fit_options["cov_kwds"]=cov_kw
                fit=model.fit(**fit_options)
                if not fit.mle_retvals.get("converged",False):raise StataError("model failed to converge",430)
            self._fit=fit;self._model_columns=regressors;self._model_constant="noconstant" not in opt
            self._sample_mask=valid
            self.coefficients={n:(float(fit.params[n]),float(fit.bse[n])) for n in x.columns}
            self.matrices["e(b)"]=fit.params.to_numpy()[None,:];self.matrices["e(V)"]=fit.cov_params().to_numpy()
            self.estimates={"N":int(fit.nobs),"df_r":float(getattr(fit,"df_resid_inference",fit.df_resid) if cov_type=="cluster" else fit.df_resid),"df_m":float(fit.df_model),"ll":float(fit.llf),
                            "cmd":"logit" if command=="logistic" else command,"depvar":dependent[0],"rank":len(x.columns)}
            if command=="regress":
                self.estimates.update({"r2":float(fit.rsquared),"r2_a":float(fit.rsquared_adj),"rmse":float(np.sqrt(fit.mse_resid)),
                                       "rss":float(fit.ssr),"mss":float(fit.ess),"F":float(fit.fvalue) if fit.fvalue is not None else np.nan})
            else:self.estimates["r2_p"]=float(fit.prsquared)
            self.emit(f"{command}: {dependent[0]}    Number of obs = {len(sample)}")
            exponentiate=command=="logistic" or "or" in opt or "irr" in opt
            self.emit("Variable              "+("Odds ratio" if exponentiate and command in {"logit","logistic"} else "IRR" if exponentiate else "Coefficient")+"      Std. err.")
            for name,(coef,se) in self.coefficients.items():
                if exponentiate:coef=np.exp(coef);se=coef*se
                self.emit(f"{name:<22}{coef:>13.8g}  {se:>13.8g}")
            return
        if command=="predict":
            if self._fit is None:raise StataError("last estimates not found",301)
            opt=self.options(",".join(parts[1:]),{"xb","residuals","pr"})
            if len(opt)>1:raise StataError("predict options mutually exclusive")
            name,mask=self.qualifiers(parts[0]);name=re.sub(r'^(?:double|float)\s+',"",name)
            self._new_name(name)
            x=self.data[self._model_columns].astype(float).copy()
            if self._model_constant:x["_cons"]=1.
            valid=mask & x.notna().all(axis=1)
            if "xb" in opt:values=x.loc[valid].to_numpy()@np.asarray(self._fit.params)
            else:values=self._fit.predict(x.loc[valid])
            if "residuals" in opt:
                if self.estimates["cmd"]!="regress":raise StataError("residuals only implemented after regress",199)
                values=self.data.loc[valid,self.estimates["depvar"]]-values
            self.data[name]=np.nan;self.data.loc[valid,name]=values
            return
        if command=="test":
            if self._fit is None:raise StataError("last estimates not found",301)
            if len(parts)>1:raise StataError("test options unsupported",198)
            value=parts[0]
            if "=" not in value:value=", ".join(f"{x}=0" for x in value.split())
            # Only named coefficients, numeric constants and equality constraints.
            if not re.fullmatch(r'[A-Za-z_0-9+*/().,=\-\s]+',value):raise StataError("unsupported test constraint")
            result=self._fit.f_test(value)
            self.returns={"F":float(result.fvalue),"p":float(result.pvalue),"df":float(result.df_num),"df_r":float(result.df_denom)}
            self.emit(str(result));return
        if command=="estimates":
            words=tokens(arg)
            if not words:raise StataError("estimates subcommand required")
            action=words[0]
            if action=="store" and len(words)==2:
                if self._fit is None:raise StataError("last estimates not found",301)
                self.stored_estimates[words[1]]=copy.deepcopy((self._fit,self._model_columns,self._model_constant,self.estimates,self.coefficients,self._sample_mask,self.matrices["e(b)"],self.matrices["e(V)"]));return
            if action=="restore" and len(words)==2:
                if words[1] not in self.stored_estimates:raise StataError("estimates not found",111)
                self._fit,self._model_columns,self._model_constant,self.estimates,self.coefficients,self._sample_mask,b,v=copy.deepcopy(self.stored_estimates[words[1]])
                self.matrices["e(b)"]=b;self.matrices["e(V)"]=v;return
            if action in {"dir","list"} and len(words)==1:self.emit("\n".join(self.stored_estimates));return
            if action=="clear" and len(words)==1:self.stored_estimates.clear();return
            raise StataError("estimates subcommand not implemented",199)
        if command=="ttest":
            opt=self.options(",".join(parts[1:]),{"by","unequal","level"})
            base,mask=self.qualifiers(parts[0]);frame=self.data.loc[mask]
            if "by" in opt:
                names=self.varlist(base)
                if len(names)!=1:raise StataError("one outcome required")
                group=opt["by"];self.varlist(group)
                levels=sorted(frame[group].dropna().unique())
                if len(levels)!=2:raise StataError("two groups required",420)
                a=frame.loc[frame[group]==levels[0],names[0]].dropna();b=frame.loc[frame[group]==levels[1],names[0]].dropna()
                result=stats.ttest_ind(a,b,equal_var="unequal" not in opt)
                self.returns={"N_1":len(a),"N_2":len(b),"mu_1":a.mean(),"mu_2":b.mean(),"t":float(result.statistic),"p":float(result.pvalue),"df_t":float(result.df)}
            else:
                match=re.fullmatch(r'(\w+)\s*==\s*(.*)',base)
                if not match:raise StataError("one-sample ttest requires variable == value")
                self.varlist(match[1]);a=frame[match[1]].dropna();mu=float(self.evaluate(match[2]));result=stats.ttest_1samp(a,mu)
                self.returns={"N_1":len(a),"mu_1":a.mean(),"t":float(result.statistic),"p":float(result.pvalue),"df_t":len(a)-1}
            self.emit(str(self.returns));return
