"""Local text output, posting datasets and estimation-table workflows."""
from __future__ import annotations

import copy
import fnmatch
import re

import numpy as np
import pandas as pd
from scipy import stats

from .parser import split_outside,tokens,unquote


class OutputCommands:
    def _output_command(self,command,arg):
        from .engine import StataError
        if command not in {'file','postfile','post','postclose','postutil','eststo','estadd','esttab','estout'}:return False
        parts=split_outside(arg);head=parts[0];optiontext=' '.join(parts[1:])
        if command=='file':
            words=tokens(head)
            if not words:raise StataError('file subcommand required',198)
            action=words[0]
            handles=getattr(self,'_file_handles',None)
            if handles is None:self._file_handles={};handles=self._file_handles
            if action=='open':
                opts=self.options(optiontext,{'read','write','append','replace','text','binary'})
                if len(words)!=4 or words[2]!='using':raise StataError('file open requires handle using filename',198)
                name=words[1];path=self.path(words[3])
                if name in handles:raise StataError('file handle already exists',110)
                if 'binary' in opts:raise StataError('binary file handles are not yet implemented',199)
                modes=set(opts)&{'read','write','append'}
                if len(modes)!=1:raise StataError('specify one of read, write, append',198)
                if 'read' in opts:
                    if not path.is_file():raise StataError('file not found',601)
                    mode='r'
                elif 'append' in opts:mode='a'
                else:
                    if path.exists() and 'replace' not in opts:raise StataError('file already exists',602)
                    mode='w'
                handles[name]=path.open(mode,encoding='utf-8',newline='')
                return True
            if action=='close':
                self.options(optiontext,set())
                if len(words)!=2:raise StataError('file close requires handle',198)
                selected=list(handles) if words[1]=='_all' else [words[1]]
                for name in selected:
                    if name not in handles:raise StataError('file handle not found',111)
                    handles.pop(name).close()
                return True
            if len(words)<2 or words[1] not in handles:raise StataError('file handle not found',111)
            handle=handles[words[1]]
            if action=='write':
                if optiontext:raise StataError('file write options unsupported',198)
                # Quoted literals, parenthesized expressions, formats and control tokens.
                content=head[head.find(words[1])+len(words[1]):].strip();output=[];fmt=None
                while content:
                    match=re.match(r'(_n(?:\(\d+\))?|_tab|_skip\(\d+\)|_char\(\d+\))(?=\s|$)',content)
                    if match:
                        control=match[0]
                        if control=='_tab':output.append('\t')
                        elif control.startswith('_skip'):output.append(' '*int(control[6:-1]))
                        elif control.startswith('_char'):output.append(chr(int(control[6:-1])))
                        else:output.append('\n'*(int(control[3:-1]) if '(' in control else 1))
                        content=content[match.end():].lstrip();continue
                    if content.startswith('"') or content.startswith('`"'):
                        token=tokens(content)[0];output.append(unquote(token));content=content[len(token):].lstrip();continue
                    match=re.match(r'%[-\d.]+[fge]',content)
                    if match:fmt=match[0];content=content[match.end():].lstrip();continue
                    if content.startswith('('):
                        depth=0;end=None;quote=False
                        for i,c in enumerate(content):
                            if c=='"':quote=not quote
                            if not quote:
                                if c=='(':depth+=1
                                elif c==')':
                                    depth-=1
                                    if depth==0:end=i;break
                        if end is None:raise StataError('unclosed file write expression',198)
                        output.append(self._format(self.evaluate(content[1:end]),fmt));content=content[end+1:].lstrip();continue
                    raise StataError('file write requires quoted text or parenthesized expressions',198)
                handle.write(''.join(output));handle.flush();return True
            if action=='read':
                if len(words)!=3 or optiontext:raise StataError('file read requires handle localname',198)
                line=handle.readline();self.locals[words[2]]=line.rstrip('\r\n');self.returns={'eof':int(line=='')};return True
            if action=='seek':
                if len(words)!=3 or optiontext:raise StataError('file seek requires handle position',198)
                if words[2]=='query':self.returns={'loc':handle.tell()}
                elif words[2]=='eof':handle.seek(0,2)
                else:handle.seek(int(self.evaluate(words[2])))
                return True
            raise StataError(f'file {action} is not implemented',199)
        posts=getattr(self,'_post_handles',None)
        if posts is None:self._post_handles={};posts=self._post_handles
        if command=='postfile':
            opts=self.options(optiontext,{'replace','every','double'})
            words=tokens(head)
            if len(words)<4 or 'using' not in words:raise StataError('postfile requires handle variables using filename',198)
            position=words.index('using');name=words[0]
            if position<2 or len(words)!=position+2:raise StataError('invalid postfile specification',198)
            if name in posts:raise StataError('post handle already exists',110)
            columns=[];types=[];storage='double' if 'double' in opts else 'float'
            for token in words[1:position]:
                if re.fullmatch(r'byte|int|long|float|double|str\d+|strL',token):storage=token;continue
                if not re.fullmatch(r'[A-Za-z_]\w{0,31}',token) or token in columns:raise StataError('invalid postfile variable',198)
                columns.append(token);types.append(storage)
            path=self.path(words[-1],'.dta')
            if path.exists() and 'replace' not in opts:raise StataError('file already exists',602)
            posts[name]={'columns':columns,'types':types,'path':path,'rows':[]};return True
        if command=='post':
            words=tokens(head)
            if not words or words[0] not in posts:raise StataError('post handle not found',111)
            name=words[0];rest=head[len(name):].strip();expressions=[]
            while rest:
                if not rest.startswith('('):raise StataError('post expressions require parentheses',198)
                depth=0;quote=False;end=None
                for i,c in enumerate(rest):
                    if c=='"':quote=not quote
                    if not quote:
                        if c=='(':depth+=1
                        elif c==')':
                            depth-=1
                            if depth==0:end=i;break
                if end is None:raise StataError('unclosed post expression',198)
                expressions.append(rest[1:end]);rest=rest[end+1:].lstrip()
            if len(expressions)!=len(posts[name]['columns']):raise StataError('post expression count does not match schema',198)
            row=[]
            for expression,storage in zip(expressions,posts[name]['types']):
                value=self.evaluate(expression)
                if not np.isscalar(value):raise StataError('post requires scalar expressions',198)
                if storage.startswith('str'):
                    if not isinstance(value,str):raise StataError('post string type mismatch',109)
                    if storage[3:].isdigit():value=value[:int(storage[3:])]
                elif isinstance(value,str):raise StataError('post numeric type mismatch',109)
                row.append(value)
            posts[name]['rows'].append(row);return True
        if command in {'postclose','postutil'}:
            if command=='postutil':
                if arg=='dir':self.emit('\n'.join(posts));return True
                if arg!='clear':raise StataError('postutil supports dir or clear',198)
                selected=list(posts)
            else:selected=[arg.strip()]
            for name in selected:
                if name not in posts:raise StataError('post handle not found',111)
                data=posts[name]
                frame=pd.DataFrame(data['rows'],columns=data['columns'])
                for column,storage in zip(data['columns'],data['types']):
                    if not storage.startswith('str'):frame[column]=pd.to_numeric(frame[column],errors='raise').astype('float32' if storage=='float' else 'float64')
                frame.to_stata(data['path'],write_index=False,version=118)
                posts.pop(name)
            return True
        if command=='eststo':
            # Prefix form eststo name: estimation_command.
            colon=split_outside(arg,':')
            if len(colon)==2:
                self.command(colon[1]);arg=colon[0].strip();parts=split_outside(arg);head=parts[0];optiontext=' '.join(parts[1:])
            opts=self.options(optiontext,{'title','refresh','nocopy'})
            if head=='clear':self.stored_estimates.clear();return True
            if head=='dir':self.emit('\n'.join(self.stored_estimates));return True
            if head.startswith('drop '):
                for pattern in head[5:].split():
                    for name in list(self.stored_estimates):
                        if fnmatch.fnmatchcase(name,pattern):self.stored_estimates.pop(name)
                return True
            name=head or f'est{len(self.stored_estimates)+1}'
            if not re.fullmatch(r'[A-Za-z_]\w*',name):raise StataError('invalid estimate name',198)
            self._statistics('estimates','store '+name)
            if 'title' in opts:
                metadata=getattr(self,'_estimate_titles',{});metadata[name]=unquote(opts['title']);self._estimate_titles=metadata
            return True
        if command=='estadd':
            opts=self.options(optiontext,{'replace','quietly'})
            target=head;model=None
            colon=split_outside(target,':')
            if len(colon)==2:target,model=colon
            match=re.fullmatch(r'(scalar|local)\s+(\w+)\s*(?:=\s*)?(.*)',target)
            if not match:raise StataError('estadd currently supports scalar or local',199)
            kind,name,expression=match.groups()
            if name in self.estimates and 'replace' not in opts:raise StataError('estimate result already exists',110)
            self.estimates[name]=self.evaluate(expression) if kind=='scalar' else unquote(expression)
            if model:
                if model not in self.stored_estimates:raise StataError('estimates not found',111)
                saved=list(self.stored_estimates[model]);saved[3]=copy.deepcopy(self.estimates);self.stored_estimates[model]=tuple(saved)
            elif self.stored_estimates:
                # estadd applies to last stored result when that result is active.
                name_of_model=next(reversed(self.stored_estimates));saved=list(self.stored_estimates[name_of_model]);saved[3]=copy.deepcopy(self.estimates);self.stored_estimates[name_of_model]=tuple(saved)
            return True
        if command in {'esttab','estout'}:
            self._estimation_table(head,optiontext);return True
        return False

    def _estimation_table(self,head,optiontext):
        from .engine import StataError
        allowed={'replace','append','b','se','t','p','ci','star','nostar','keep','drop','order','stats','scalars','r2','ar2','n','noobs','label','title','mtitles','nomtitles','nonumbers','numbers','collabels','coeflabels','varlabels','booktabs','tex','csv','tab','plain','fragment','nomtitles','nodepvars','nogaps','compress','not','nonotes','addnotes','alignment','eqlabels','nobaselevels','noomitted'}
        opts=self.options(optiontext,allowed)
        if sum(key in opts for key in ('se','t','p','ci'))>1:raise StataError('choose one secondary statistic',198)
        words=tokens(head);destination=None
        if 'using' in words:
            index=words.index('using')
            if len(words)!=index+2:raise StataError('one table filename required',198)
            destination=self.path(words[index+1]);words=words[:index]
            if destination.exists() and not (set(opts)&{'replace','append'}):raise StataError('file already exists',602)
        models=[]
        if words:
            for pattern in words:
                selected=[name for name in self.stored_estimates if fnmatch.fnmatchcase(name,pattern)]
                if not selected:raise StataError('stored estimation result not found',111)
                models.extend((name,self.stored_estimates[name][4],self.stored_estimates[name][3]) for name in selected)
        elif self.stored_estimates:models=[(name,saved[4],saved[3]) for name,saved in self.stored_estimates.items()]
        elif self._fit is not None:models=[('current',self.coefficients,self.estimates)]
        else:raise StataError('no estimation results available',301)
        names=list(dict.fromkeys(name for _,coefficients,_ in models for name in coefficients))
        if 'keep' in opts:names=[name for name in names if any(fnmatch.fnmatchcase(name,p) for p in tokens(opts['keep']))]
        if 'drop' in opts:names=[name for name in names if not any(fnmatch.fnmatchcase(name,p) for p in tokens(opts['drop']))]
        if 'order' in opts:
            first=[name for pattern in tokens(opts['order']) for name in names if fnmatch.fnmatchcase(name,pattern)];names=list(dict.fromkeys(first+names))
        def precision(value,default):
            if value is True:return default
            try:return int(str(value).split()[0])
            except (TypeError,ValueError):raise StataError('table formats require decimal precision',198)
        decimals=precision(opts.get('b',3),3);secondary=next((key for key in ('se','t','p','ci') if key in opts),None)
        secondary_decimals=precision(opts.get(secondary,3),3) if secondary else 3
        titles=[unquote(value) for value in tokens(opts['mtitles'])] if isinstance(opts.get('mtitles'),str) else [name for name,_,_ in models]
        if 'nomtitles' in opts:titles=['']*len(models)
        if len(titles)!=len(models):raise StataError('model title count mismatch',198)
        rows=[['']+titles]
        star_levels=[('*',.05),('**',.01),('***',.001)]
        if isinstance(opts.get('star'),str):
            entries=tokens(opts['star'])
            if len(entries)%2:raise StataError('star() requires symbol threshold pairs',198)
            star_levels=[(entries[i],float(entries[i+1])) for i in range(0,len(entries),2)]
        for name in names:
            row=[self.variable_labels.get(name,name) if 'label' in opts else name];other=['']
            for _,coefficients,results in models:
                if name not in coefficients:row.append('');other.append('');continue
                coefficient,error=coefficients[name];df=results.get('df_r',np.inf)
                ratio=coefficient/error if error else np.nan
                pvalue=2*stats.t.sf(abs(ratio),df) if np.isfinite(df) and results.get('cmd') in {'regress','areg','xtreg','reghdfe','ivregress'} else 2*stats.norm.sf(abs(ratio))
                stars=''
                if 'nostar' not in opts:
                    for symbol,threshold in sorted(star_levels,key=lambda item:item[1],reverse=True):
                        if pvalue<threshold:stars=symbol
                row.append(f'{coefficient:.{decimals}f}'+stars)
                if secondary:
                    if secondary=='ci':
                        critical=stats.t.isf(.025,df) if np.isfinite(df) else stats.norm.isf(.025)
                        other.append(f'[{coefficient-critical*error:.{secondary_decimals}f}, {coefficient+critical*error:.{secondary_decimals}f}]')
                    else:
                        number={'se':error,'t':ratio,'p':pvalue}[secondary];other.append(f'({number:.{secondary_decimals}f})')
            rows.append(row)
            if secondary:rows.append(other)
        result_names=[]
        if isinstance(opts.get('stats'),str):
            result_names=split_outside(opts['stats'])[0].split()
        elif isinstance(opts.get('scalars'),str):result_names=opts['scalars'].split()
        if 'r2' in opts:result_names.append('r2')
        if 'ar2' in opts:result_names.append('r2_a')
        if 'noobs' not in opts:result_names.append('N')
        for result_name in dict.fromkeys(result_names):
            values=[]
            for _,_,results in models:
                value=results.get(result_name,'')
                values.append(f'{value:.0f}' if result_name=='N' and isinstance(value,(int,float)) else f'{value:.3f}' if isinstance(value,(int,float)) else str(value))
            rows.append([result_name]+values)
        extension=destination.suffix.lower() if destination else ''
        if 'tex' in opts or 'booktabs' in opts or extension=='.tex':
            def escape(value):return str(value).replace('_',r'\_').replace('%',r'\%').replace('&',r'\&')
            body='\n'.join(' & '.join(escape(value) for value in row)+r' \\' for row in rows)
            content=body if 'fragment' in opts else '\\begin{tabular}{l'+('r'*len(models))+'}\n'+body+'\n\\end{tabular}\n'
        elif 'csv' in opts or extension=='.csv':
            import csv,io
            buffer=io.StringIO();csv.writer(buffer).writerows(rows);content=buffer.getvalue()
        else:content='\n'.join('\t'.join(row) for row in rows)+'\n'
        if destination:
            with destination.open('a' if 'append' in opts else 'w',encoding='utf-8') as handle:handle.write(content)
            self.emit(f'table written: {destination.name}')
        else:self.emit(content.rstrip())
