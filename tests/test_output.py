from pathlib import Path

import pandas as pd

from openstat import Engine


def test_file_write_read_controls_and_eof(tmp_path):
    engine=Engine(tmp_path)
    result=engine.run('file open h using "text.txt", write replace\nfile write h "one" _tab %5.2f (2.5) _n "two" _n\nfile close h\nfile open h using "text.txt", read\nfile read h first\nfile read h second\nfile read h third\nscalar eof=r(eof)\nfile close h')
    assert result.rc==0,result.output
    assert (tmp_path/'text.txt').read_text()=='one\t 2.50\ntwo\n'
    assert engine.locals['second']=='two'
    assert engine.scalars['eof']==1


def test_postfile_records_typed_synthetic_data(tmp_path):
    engine=Engine(tmp_path)
    result=engine.run('postfile h double estimate str10 model using "posted.dta", replace\npost h (1.25) ("one")\npost h (2.5) ("two")\npostclose h')
    assert result.rc==0,result.output
    frame=pd.read_stata(tmp_path/'posted.dta')
    assert frame.estimate.tolist()==[1.25,2.5]
    assert frame.model.tolist()==['one','two']


def test_eststo_estadd_and_tex_table_are_real_outputs(tmp_path):
    engine=Engine(tmp_path)
    result=engine.run('set obs 20\ngen x=_n\ngen y=1+2*x+mod(_n,3)\neststo model1: regress y x\nestadd scalar custom = 7\nesttab model1 using "models.tex", replace se b(4) scalars(custom) nostar')
    assert result.rc==0,result.output
    text=(tmp_path/'models.tex').read_text()
    assert '\\begin{tabular}' in text
    assert 'custom & 7.000' in text
    assert 'model1' in text
    assert f'{engine.coefficients["x"][0]:.4f}' in text


def test_post_rejects_type_mismatch_and_does_not_add_row(tmp_path):
    engine=Engine(tmp_path)
    assert engine.run('postfile h double x using "posted.dta", replace').rc==0
    assert engine.run('post h ("wrong")').rc==109
    assert engine.run('postclose h').rc==0
    assert len(pd.read_stata(tmp_path/'posted.dta'))==0
