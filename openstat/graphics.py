"""Original OpenStat plots. Stata graph syntax coverage is deliberately bounded."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from .parser import split_outside,tokens


class GraphicsCommands:
    def _graph(self,command,arg):
        from .engine import StataError
        if command=="twoway":
            words=arg.split(maxsplit=1)
            if not words or words[0] not in {"scatter","line"}:raise StataError("only single scatter/line twoway plots implemented",199)
            command=words[0];arg=words[1] if len(words)>1 else ""
        parts=split_outside(arg)
        if command=="graph":
            words=tokens(parts[0])
            if len(words)!=2 or words[0]!="export":raise StataError("graph subcommand not implemented",199)
            opt=self.options(",".join(parts[1:]),{"replace","width","height"})
            if self._figure is None:raise StataError("no graph exists",693)
            path=self.path(words[1])
            if path.suffix.lower() not in {".png",".svg",".pdf"}:raise StataError("export supports PNG, SVG or PDF",199)
            if path.exists() and "replace" not in opt:raise StataError("file already exists",602)
            width=int(opt.get("width",900));height=int(opt.get("height",600))
            if not (64<=width<=8192 and 64<=height<=8192):raise StataError("graph dimensions must be 64–8192")
            oldsize=self._figure.get_size_inches().copy()
            self._figure.set_size_inches(width/100,height/100)
            try:self._figure.savefig(path,dpi=100,format=path.suffix[1:])
            finally:self._figure.set_size_inches(oldsize)
            self.graphs.append(path);self.emit(f"graph exported: {path.name}");return
        opt=self.options(",".join(parts[1:]),{"title","xtitle","ytitle","name","replace","bin","frequency","percent","density"})
        unsupported=set(opt)&({"bin","frequency","percent","density"} if command in {"scatter","line"} else {"name","replace"})
        if unsupported:raise StataError(f"options not implemented: {', '.join(sorted(unsupported))}",199)
        base,mask=self.qualifiers(parts[0]);names=self.varlist(base)
        frame=self.data.loc[mask,names].dropna()
        if any(not np.issubdtype(frame[n].dtype,np.number) for n in names):raise StataError("numeric graph variables required",109)
        fig=Figure(figsize=(9,6),dpi=100,facecolor="#ffffff");axes=fig.subplots()
        axes.set_facecolor("#f3f7fa");axes.grid(color="white",linewidth=1.3);axes.set_axisbelow(True)
        for spine in axes.spines.values():spine.set_visible(False)
        if command in {"scatter","line"}:
            if len(names)!=2:raise StataError("graph requires y x")
            if command=="scatter":axes.scatter(frame[names[1]],frame[names[0]],s=26,color="#087f81",alpha=.85)
            else:axes.plot(frame[names[1]],frame[names[0]],color="#087f81",linewidth=1.8)
            axes.set_xlabel(names[1]);axes.set_ylabel(names[0])
        elif command in {"histogram","hist"}:
            if len(names)!=1:raise StataError("histogram requires one variable")
            modes=set(opt)&{"frequency","percent","density"}
            if len(modes)>1:raise StataError("histogram scale options mutually exclusive")
            bins=int(opt.get("bin",min(30,max(1,int(np.sqrt(len(frame)))))))
            if bins<1 or bins>1000:raise StataError("bin must be between 1 and 1000")
            axes.hist(frame[names[0]],bins=bins,color="#087f81",edgecolor="white",density=not modes or "density" in modes,
                      weights=np.full(len(frame),100/len(frame)) if "percent" in modes and len(frame) else None)
            axes.set_xlabel(names[0]);axes.set_ylabel("Frequency" if "frequency" in modes else "Percent" if "percent" in modes else "Density")
        else:raise StataError("unsupported graph command",199)
        from .parser import unquote
        for key,setter in (("title",axes.set_title),("xtitle",axes.set_xlabel),("ytitle",axes.set_ylabel)):
            if key in opt:setter(unquote(opt[key]))
        fig.tight_layout();self._figure=fig
        output=self.cwd/"openstat-output";output.mkdir(exist_ok=True)
        path=output/f"graph-{len(self.graphs)+1}.png";fig.savefig(path,dpi=100)
        self.graphs.append(path);self.emit(f"graph created: {path.name}")
