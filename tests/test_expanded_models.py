import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from openstat import Engine
from qa.run_differential import parse_scalars

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('case',['factors_weights','panel','instrumental'])
def test_independent_estimator_matches_recorded_reference(tmp_path,case):
    source=ROOT/'qa/cases'/f'{case}.do'
    record=json.loads((ROOT/'qa/expanded_reference.json').read_text())['cases'][case]
    assert hashlib.sha256(source.read_bytes()).hexdigest()==record['sha256']
    engine=Engine(tmp_path);result=engine.run_file(source)
    assert result.rc==record['rc'],result.output
    observed=parse_scalars(result.output)
    assert set(observed)==set(record['scalars'])
    for key,value in record['scalars'].items():assert observed[key]==pytest.approx(value,rel=1e-8,abs=1e-9),key


def test_factor_prediction_survives_estimate_store_restore(tmp_path):
    engine=Engine(tmp_path)
    result=engine.run('set obs 36\ngen x=_n/10\ngen g=mod(_n,3)\ngen y=1+2*x+3*(g==1)+mod(_n,5)/10\nregress y i.g##c.x\nestimates store full\npredict double original\nregress y x\nestimates restore full\npredict double restored')
    assert result.rc==0,result.output
    assert np.allclose(engine.data.original,engine.data.restored)


def test_panel_lags_obey_time_gaps_and_panel_boundaries(tmp_path):
    engine=Engine(tmp_path)
    engine.data=pd.DataFrame({'id':[1,1,1,2,2],'t':[1,2,4,1,3],'x':[10.,20.,40.,100.,300.]})
    result=engine.run('xtset id t\ngen double lagged=L.x\ngen double differenced=D.x\ngen double lead=F.x')
    assert result.rc==0,result.output
    assert np.isnan(engine.data.lagged.iloc[[0,2,3,4]]).all()
    assert engine.data.lagged.iloc[1]==10
    assert engine.data.differenced.iloc[1]==10
    assert engine.data.lead.iloc[0]==20


def test_collinear_terms_are_omitted_without_changing_identified_slope(tmp_path):
    engine=Engine(tmp_path)
    result=engine.run('set obs 20\ngen x=_n\ngen duplicate=2*x\ngen y=1+2*x+mod(_n,3)\nregress y x duplicate')
    assert result.rc==0,result.output
    assert engine.coefficients['duplicate']==(0.,0.)
    assert engine.estimates['rank']==2


def test_iv_rank_failure_does_not_publish_guessed_result(tmp_path):
    engine=Engine(tmp_path)
    result=engine.run('set obs 20\ngen x=_n\ngen y=2*x\ngen d=mod(_n,2)\nivregress 2sls y x (d=x)')
    assert result.rc==481,result.output
