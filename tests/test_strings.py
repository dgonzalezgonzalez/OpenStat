import numpy as np
import pandas as pd

from openstat import Engine


def test_unicode_normalization_and_named_subject_indicators(tmp_path):
    engine=Engine(tmp_path)
    engine.data=pd.DataFrame({'text':['Matemáticas | Inglés','Lengua castellana y literatura','',''],'marked':[1.,1.,0.,np.nan]})
    result=engine.run('gen strL normalized=ustrlower(ustrnormalize(text,"nfd"))\nreplace normalized=ustrregexra(normalized,"\\p{Mark}","")\ngen strL selected="|"+subinstr(normalized," ","",.)+"|"\nforeach subject in math english {\nlocal token "matematicas"\nif "`subject\'"=="english" local token "ingles"\ngen double indicator_`subject\'=0 if marked==0 & strtrim(text)==""\nreplace indicator_`subject\'=100*(strpos(selected,"|`token\'|")>0) if marked==1 & strtrim(text)!=""\n}')
    assert result.rc==0,result.output
    assert engine.data.indicator_math.iloc[0]==100
    assert engine.data.indicator_english.iloc[0]==100
    assert engine.data.indicator_math.iloc[1]==0
    assert engine.data.indicator_math.iloc[2]==0
    assert np.isnan(engine.data.indicator_math.iloc[3])


def test_regex_captures_and_string_formatting(tmp_path):
    engine=Engine(tmp_path)
    engine.data=pd.DataFrame({'text':['ID12','ID34','none']})
    result=engine.run('gen str extracted=regexs(1) if regexm(text,"ID([0-9]+)")\ngen str formatted=string(1.25,"%9.2f")\nscalar byte_length=strlen("ñ")\nscalar unicode_length=ustrlen("ñ")')
    assert result.rc==0,result.output
    assert engine.data.extracted.tolist()==['12','34','']
    assert engine.data.formatted.tolist()==['1.25']*3
    assert engine.scalars['byte_length']==2
    assert engine.scalars['unicode_length']==1


def test_double_expression_promotes_float_storage_before_arithmetic(tmp_path):
    engine=Engine(tmp_path)
    assert engine.run('set obs 1\ngen x=1\ngen double y=x+1e-9').rc==0
    assert engine.data.y.iloc[0]>1
