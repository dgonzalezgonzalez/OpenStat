"""Interpreter boundary and numerical behavior beyond the differential fixtures."""
import threading
import time

import numpy as np
import pytest

from openstat import Engine


def test_while_reexpands_macros_and_preserves_nested_loops(tmp_path):
    e=Engine(tmp_path)
    result=e.run("""local i 0
while `i' < 3 {
local i = `i' + 1
}
scalar answer = `i'
""")
    assert result.rc==0,result.output
    assert e.scalars['answer']==3


def test_comments_continuations_delimit_and_quoted_commas(tmp_path):
    e=Engine(tmp_path)
    result=e.run('''* comment
set obs 3
generate x = 1 ///
    + _n /* middle */
#delimit ;
scalar a = 2; scalar b = 3;
#delimit cr
display "a,b // literal"
''')
    assert result.rc==0,result.output
    assert list(e.data.x)==[2,3,4]
    assert e.scalars=={'a':2,'b':3}
    assert 'a,b // literal' in result.output


def test_no_python_attribute_access_or_code_execution(tmp_path):
    e=Engine(tmp_path)
    for expression in ['__import__("os")','(1).__class__','open("pwned","w")','[x for x in (1,2)]']:
        result=e.run('scalar a = '+expression)
        assert result.rc!=0
    assert not (tmp_path/'pwned').exists()


def test_cancel_interrupts_do_file_at_statement_boundary(tmp_path):
    e=Engine(tmp_path)
    output=[]
    worker=threading.Thread(target=lambda:output.append(e.run('while 1 {\nscalar x = 1\n}')))
    worker.start()
    time.sleep(.03)
    e.cancel();worker.join(3)
    assert not worker.is_alive()
    assert output[0].rc in {1,901}  # Finite step budget may win on very fast hosts.
    assert e.run('scalar after = 1').rc==0


def test_one_based_subscripts_missing_and_probability_functions(tmp_path):
    e=Engine(tmp_path)
    result=e.run('set obs 3\ngen x = _n\nreplace x = . in 2\nscalar t = ttail(10, 0)\nassert x[1]==1\nassert x[2]==.\nassert x[3]==3\n')
    assert result.rc==0,result.output
    assert e.scalars['t']==.5


def test_regression_prediction_and_robust_covariance(tmp_path):
    e=Engine(tmp_path)
    r=e.run('set obs 20\ngen x = _n\ngen y = 1 + 2*x + mod(_n,3)\nregress y x, robust\npredict double fitted\npredict double residual, residuals\n')
    assert r.rc==0,r.output
    assert np.allclose(e.data.y,e.data.fitted+e.data.residual)
    assert abs(e.data.residual.sum())<1e-9
    assert e.estimates['N']==20
    assert np.linalg.eigvalsh(e.matrices['e(V)']).min()>0


def test_extended_missing_dta_is_rejected_without_overwriting(tmp_path):
    import pyreadstat
    import pandas as pd
    path=tmp_path/'extended.dta'
    pyreadstat.write_dta(pd.DataFrame({'x':[1.,'a']}),path,missing_user_values={'x':['a']})
    e=Engine(tmp_path)
    assert e.run('set obs 2\ngen original = _n').rc==0
    result=e.run('use "extended.dta", clear')
    assert result.rc==199,result.output
    assert list(e.data.original)==[1,2]


def test_unknown_options_and_factor_variables_fail_explicitly(tmp_path):
    e=Engine(tmp_path)
    e.run('set obs 4\ngen x = _n\ngen y = x*x')
    assert e.run('regress y i.x').rc==199
    assert e.run('scatter y x, nonsense').rc==198


def test_recursive_replace_follows_observation_order(tmp_path):
    e=Engine(tmp_path)
    r=e.run('set obs 4\ngen x=.\nreplace x=1 in 1\nreplace x=x[_n-1] in 2/l\ngen y=1\nreplace y=y[_n-1]+1 in 2/l\n')
    assert r.rc==0,r.output
    assert list(e.data.x)==[1,1,1,1]
    assert list(e.data.y)==[1,2,3,4]
    assert e.run('gen z=cond(_n==1,1,z[_n-1]+1)').rc==111


def test_logical_generation_is_numeric_and_overflow_is_missing(tmp_path):
    e=Engine(tmp_path)
    result=e.run('set obs 3\ngen flag = _n==1\ngen overflow = exp(10000)\nassert overflow==.\nscalar divide = 1/0')
    assert result.rc==0,result.output
    assert list(e.data.flag)==[1,0,0]
    assert e.data.overflow.isna().all()
    assert np.isnan(e.scalars['divide'])


def test_program_exit_returns_to_caller_and_if_uses_first_observation(tmp_path):
    e=Engine(tmp_path)
    r=e.run('set obs 2\ngen x=_n\nprogram define child\nexit 0\nend\nchild\nif x==1 {\ndisplay "caller continued"\n}\n')
    assert r.rc==0,r.output
    assert 'caller continued' in r.output


def test_inrange_has_stata_missing_boundary_rules(tmp_path):
    e=Engine(tmp_path)
    r=e.run('scalar a=inrange(3,.,5)\nscalar b=inrange(.,0,.)\nscalar c=inrange(3,.,.)')
    assert r.rc==0,r.output
    assert e.scalars=={'a':True,'b':False,'c':True}


def test_logical_negation_precedence_and_chained_comparisons(tmp_path):
    e=Engine(tmp_path)
    assert e.run('scalar a = !2 == 1').rc==0
    assert not e.scalars['a']
    # Stata binds "!" tighter than "*" and evaluates chained comparisons
    # left to right: 3 > 2 > 1 is (3 > 2) > 1.
    # Stata binds "!" tighter than "*": !2 * 3 is (0) * 3 = 0.
    assert e.run('scalar b = !2 * 3').rc==0
    assert e.scalars['b']==0
    assert e.run('scalar b2 = !0 * 3').rc==0
    assert e.scalars['b2']==3
    assert e.run('scalar c = 3 > 2 > 1').rc==0
    assert e.scalars['c']==0


def test_cluster_inference_uses_number_of_clusters_minus_one(tmp_path):
    e=Engine(tmp_path)
    result=e.run('set obs 20\ngen g = ceil(_n/5)\ngen x=_n\ngen y=1+2*x+mod(_n,3)\nregress y x, vce(cluster g)')
    assert result.rc==0,result.output
    assert e.estimates['df_r']==3


def test_singular_invsym_fails_instead_of_different_generalized_inverse(tmp_path):
    e=Engine(tmp_path)
    assert e.run('mata:\nA=(1,1 \\ 1,1)\nB=invsym(A)\nend').rc!=0


def test_clear_all_drops_stale_estimation_state(tmp_path):
    e=Engine(tmp_path)
    result=e.run('set obs 6\ngen x=_n\ngen y=2*x+mod(_n,2)\nregress y x\nclear all\nset obs 3\ngen x=_n\npredict y')
    assert result.rc==301,result.output


def test_egen_group_and_tag_use_sample_and_string_missing(tmp_path):
    e=Engine(tmp_path)
    result=e.run('set obs 4\ngen x=_n\negen g=group(x) if _n>2\ngen str1 s=cond(_n==1,"","a")\negen h=group(s)\negen n=count(s)\negen first=tag(s) if _n>2')
    assert result.rc==0,result.output
    assert list(e.data.g.dropna())==[1,2]
    assert np.isnan(e.data.h[0])
    assert list(e.data.h.dropna())==[1,1,1]
    assert list(e.data.n)==[3,3,3,3]
    assert list(e.data['first'].dropna())==[1,0]


def test_running_sum_excludes_unqualified_observations(tmp_path):
    e=Engine(tmp_path)
    result=e.run('set obs 4\ngen x=_n\ngen s=sum(x) if _n>2\ngen n=sum(1)')
    assert result.rc==0,result.output
    assert list(e.data.s.dropna())==[3,7]
    assert list(e.data.n)==[1,2,3,4]


@pytest.mark.parametrize('code',[
    'A=(1,2)\nB=(3 \\ 4)\nC=A+B',
    'A=(2,4)\nB=(2,2)\nC=A/B',
    'A=(1,2 \\ 3,4)\nC=A^2',
    'A=(1,2)\nC=A[0,1]',
])
def test_mata_rejects_incompatible_shapes_and_invalid_indices(tmp_path,code):
    e=Engine(tmp_path)
    assert e.run('mata:\n'+code+'\nend').rc!=0
