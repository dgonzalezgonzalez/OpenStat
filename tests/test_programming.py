"""Programming contracts, including strict errors and sample restrictions."""
import numpy as np
import pandas as pd
import pytest

from openstat import Engine
from openstat.engine import StataError
from openstat.parser import lex, split_outside, tokens, unquote, parse_options
from openstat.programming import ProgrammingCommands, expand_macros


@pytest.fixture
def engine(tmp_path):
    result = Engine(tmp_path)
    result.data = pd.DataFrame({"x": [1., 2., np.nan, 4.], "y": [10., np.nan, 30., 40.],
                               "group": ["b", "a", "", "a"], "w": [1., 0., 1., 2.]})
    return result


def test_compound_strings_protect_commas_comments_and_nested_quotes():
    text = '`"title "quoted", // text `"nested"\'"\''
    assert tokens(text + ' x') == [text, 'x']
    assert unquote(text) == 'title "quoted", // text `"nested"\''
    assert split_outside(text + ', nested(a,b)') == [text, 'nested(a,b)']
    assert [s.text for s in lex('display ' + text + ' // comment')] == ['display ' + text]


def test_lexer_normalizes_inline_blocks_else_and_nested_comments():
    script = 'if 1 { display "{" } else if 0 { display "bad" } else { display "ok" }\n'
    assert [s.text for s in lex(script)] == ['if 1 {', 'display "{"', '}', 'else if 0 {',
                                             'display "bad"', '}', 'else {', 'display "ok"', '}']
    assert [s.text for s in lex('gen/* outer /* inner */ still outer */ x=1')] == ['gen  x=1']


def test_delimiter_protects_compound_strings_and_bracket_expressions():
    script = '#delimit ;\ndisplay `"one;two "q""\';\nscalar x = (1+\n 2);\n#delimit cr;\nscalar y=4\n'
    result = lex(script)
    assert [s.text for s in result] == ['display `"one;two "q""\'', 'scalar x = (1+ 2)', 'scalar y=4']
    assert [s.line for s in result] == [2, 3, 6]
    with pytest.raises(ValueError, match="delimiter"):
        lex('#delimit ?\n')


def test_nested_and_escaped_macro_references():
    local = {"which": "two", "var_two": "income", "indirect": "var_two", "i": "2", "name2": "ok"}
    global_ = {"root_two": "data", "loop": "$loop"}
    assert expand_macros("`var_`which'' ${root_`which'} ``indirect'' `name`i''", local, global_) == "income data income ok"
    assert expand_macros(r"\`which' \$root_two", local, global_) == "`which' $root_two"
    assert expand_macros('`"`which\' "quote""\'', local, global_) == '`"two "quote""\''
    assert expand_macros("`=2+3'", local, global_, evaluate=lambda x: 5) == "5"
    with pytest.raises(ValueError, match="recursion"):
        expand_macros("$loop", local, global_, limit=5)


def test_syntax_varlist_options_abbreviations_qualifiers_and_weights(engine):
    engine.locals['0'] = 'x y if x>1 in 2/4 [aw=w], ti("Results, one") det reps(4) generate(answer)'
    engine._syntax('varlist(min=1 numeric) [if] [in] [aw fw pw iw] [, TItle(string) Detail REPS(integer 3) GENerate(name)]')
    assert {k: engine.locals[k] for k in ('varlist', 'if', 'in', 'weight', 'exp', 'title', 'detail', 'reps', 'generate')} == {
        'varlist': 'x y', 'if': 'if x>1', 'in': 'in 2/4', 'weight': 'aweight', 'exp': '= w',
        'title': 'Results, one', 'detail': 'detail', 'reps': '4', 'generate': 'answer'}


def test_syntax_defaults_required_options_and_passthrough(engine):
    engine.locals['0'] = 'x, gen(result) unknown(a(b,c))'
    engine._syntax('varname, GENerate(name) [Count(integer 3) Beta(real 1.5) SAVing(passthru) *]')
    assert engine.locals['generate'] == 'result'
    assert engine.locals['count'] == '3'
    assert engine.locals['beta'] == '1.5'
    assert engine.locals['options'] == 'unknown(a(b,c))'
    engine.locals['0'] = 'x'
    with pytest.raises(StataError, match='generate required'):
        engine._syntax('varname, GENerate(name)')


@pytest.mark.parametrize('call,specification,rc', [
    ('x group', 'varlist(numeric)', 109),
    ('x y', 'varname', 103),
    ('x, t(4.5)', 'varname [, Title(integer 0)]', 198),
    ('x, reps(2.5)', 'varname [, REPS(integer 1)]', 198),
    ('x, nonsense', 'varname', 198),
    ('x if y>1', 'varname', 101),
    ('x [pw=w]', 'varname [aw]', 101),
    ('', 'varlist', 100),
    ('x', 'varlist(fv)', 199),
])
def test_syntax_rejects_invalid_or_unimplemented_grammar(engine, call, specification, rc):
    engine.locals['0'] = call
    with pytest.raises(StataError) as error:
        engine._syntax(specification)
    assert error.value.rc == rc


def test_syntax_optional_varlist_default_none_and_name(engine):
    engine.locals['0'] = ''
    engine._syntax('[varlist]')
    assert engine.locals['varlist'] == 'x y group w'
    engine._syntax('[varlist(default=none)]')
    assert engine.locals['varlist'] == ''
    engine.locals['0'] = 'left right'
    engine._syntax('namelist(name=targets min=2 max=2)')
    assert engine.locals['targets'] == 'left right'


def test_tokenize_and_gettoken_handle_quoted_phrases_and_parse_tokens(engine):
    engine._tokenize('`"one "two words" three,four"\', parse(" ,")')
    assert [engine.locals[str(i)] for i in range(1, 6)] == ['one', 'two words', 'three', ',', 'four']
    engine.locals['source'] = '"two words" (a b) , rest'
    engine._gettoken('first rest : source, qed(quoted)')
    assert engine.locals['first'] == 'two words'
    assert engine.locals['quoted'] == '1'
    engine._gettoken('second rest : rest, bind parse(" ,")')
    assert engine.locals['second'] == '(a b)'
    engine._gettoken('comma rest : rest, parse(" ,")')
    assert engine.locals['comma'] == ','
    assert engine.locals['rest'] == 'rest'


def test_marksample_respects_missing_values_if_in_weights_and_novarlist(engine):
    engine.locals['0'] = 'x y if x>=1 in 2/l [fw=w]'
    engine._syntax('varlist [if] [in] [fw]')
    engine._marksample('touse')
    assert engine.data[engine.locals['touse']].tolist() == [0, 0, 0, 1]
    engine._marksample('alluse, novarlist zeroweight')
    assert engine.data[engine.locals['alluse']].tolist() == [0, 1, 1, 1]
    engine.data.loc[3, 'w'] = 1.5
    with pytest.raises(StataError) as error:
        engine._marksample('invalid')
    assert error.value.rc == 401


def test_markout_string_semantics_and_additional_missingness(engine):
    engine.locals['0'] = 'x'
    engine._syntax('varlist')
    engine._marksample('touse')
    marker = engine.locals['touse']
    engine._markout(marker + ' y')
    assert engine.data[marker].tolist() == [1, 0, 0, 1]
    engine._markout(marker + ' group, strok')
    assert engine.data[marker].tolist() == [1, 0, 0, 1]
    engine._markout(marker + ' group')
    assert engine.data[marker].tolist() == [0, 0, 0, 0]


def test_unab_ds_and_levelsof_macros(engine):
    engine._unab('variables: x-y')
    assert engine.locals['variables'] == 'x y'
    engine._ds(', has(type numeric)')
    assert engine.returns['varlist'] == 'x y w'
    engine._levelsof('group, local(groups) clean')
    assert engine.locals['groups'] == 'a b'
    engine._levelsof('x, local(levels) missing separate(",")')
    assert engine.locals['levels'] == '1,2,4,.'


def test_extended_macro_word_lists_substitution_and_metadata(engine):
    engine.locals.update({'left': 'a b a c', 'right': 'b d'})
    for target, expression, expected in [
        ('n', 'word count "two words" one', '2'),
        ('second', 'word 2 of a "two words" c', 'two words'),
        ('u', 'list uniq left', 'a b c'),
        ('d', 'list dups left', 'a'),
        ('union', 'list left | right', 'a b c d'),
        ('intersect', 'list left & right', 'b'),
        ('diff', 'list left - right', 'a c'),
        ('sub', 'subinstr local left "a" "z", all', 'z b z c'),
    ]:
        assert engine._programming('local', target + ': ' + expression)
        assert engine.locals[target] == expected
    engine.variable_labels['x'] = 'Income'
    engine._extended_macro('local', 'label: variable label x')
    assert engine.locals['label'] == 'Income'


def test_macro_shift_clears_stale_numbered_macros(engine):
    engine.locals.update({'0': 'one two three', '1': 'one', '2': 'two', '3': 'three'})
    engine._programming('macro', 'shift 2')
    assert engine.locals['0'] == 'one two three'
    assert engine.locals['1'] == 'three'
    assert '2' not in engine.locals and '3' not in engine.locals


def test_balanced_options_preserve_nested_arguments_and_quoted_delimiters(engine):
    value = 'cells(b(fmt(3)) se(fmt(2))) title(`"a,b (literal)"\') replace'
    assert parse_options(value) == [('cells', 'b(fmt(3)) se(fmt(2))'),
                                    ('title', '`"a,b (literal)"\''), ('replace', True)]
    assert engine.options(value, {'cells', 'title', 'replace'})['cells'] == 'b(fmt(3)) se(fmt(2))'
    with pytest.raises(StataError, match='more than once'):
        engine.options('replace replace', {'replace'})
    with pytest.raises(ValueError, match='unclosed'):
        parse_options('cells(b(fmt(3))')


@pytest.mark.parametrize('condition,expected', [('1', 1), ('0', 2)])
def test_else_if_selects_one_branch_and_does_not_evaluate_skipped_conditions(engine, condition, expected):
    result = engine.run('if '+condition+' { scalar answer=1 } else if 1 { scalar answer=2 } else { scalar answer=3 }\n'
                        'if 1 { scalar untouched=1 } else if unknown_name { error 9 }\n')
    assert result.rc == 0, result.output
    assert engine.scalars['answer'] == expected
    assert engine.scalars['untouched'] == 1


def test_inline_if_observes_capture_return_code_and_short_circuits_command(engine):
    result = engine.run('capture error 459\nif _rc == 459 scalar captured=1\n'
                        'if 0 impossible_command x\nif x[1]==1 scalar first=1\n')
    assert result.rc == 0, result.output
    assert engine.scalars['captured'] == 1
    assert engine.scalars['first'] == 1


def test_program_prefixes_raw_arguments_syntax_returns_and_temporary_cleanup(engine):
    result = engine.run('''capture program drop command_test
quietly program define command_test, rclass
syntax varname(numeric) [if] [, Scale(real 2)]
marksample touse
tempvar product
gen double `product'= `varlist' * `scale' if `touse'
quietly summarize `product', meanonly
return scalar answer=r(mean)
end
command_test x if x>1, sc(3)
scalar answer=r(answer)
scalar nresults=`: word count x y z'
quietly command_test x, scale(4)
scalar second=`r(answer)'
''')
    assert result.rc == 0, result.output
    assert engine.scalars['answer'] == 9
    assert engine.scalars['nresults'] == 3
    assert engine.scalars['second'] == pytest.approx(28/3)
    assert list(engine.data) == ['x', 'y', 'group', 'w']
    assert '__openstat_inline_macro' not in engine.locals


def test_capture_and_quietly_apply_to_whole_blocks(engine):
    result = engine.run('''quietly {
display "hidden"
noisily display "visible"
}
capture {
scalar entered=1
error 459
scalar never=1
}
local captured=_rc
scalar captured=`captured'
display "finished"
''')
    assert result.rc == 0, result.output
    assert result.output.splitlines() == ['visible', 'finished']
    assert engine.scalars['captured'] == 459
    assert 'never' not in engine.scalars
    assert engine._quiet == 0


def test_foreach_numlist_forvalues_alias_continue_and_break(engine):
    result = engine.run('''local total=0
foreach i of numlist -2(2)2 5 7:11 {
local total=`total'+`i'
}
scalar total=`total'
local count=0
forv i=6(-2)0 {
if `i'==4 continue
local ++count
if `i'==2 continue, break
}
scalar count=`count'
forval skip=3/1 {
error 9
}
forvalues i=1 3 to 7 {
local ++count
}
scalar final=`count'
''')
    assert result.rc == 0, result.output
    assert engine.scalars['total'] == 32
    assert engine.scalars['count'] == 2
    assert engine.scalars['final'] == 6
    assert engine._loop_depth == 0


def test_foreach_local_compound_phrases_and_nested_branches(engine):
    result = engine.run('''local phrases `"one "two words" three"'
local count=0
foreach phrase of local phrases {
if `"`phrase'"' == "two words" {
local count=`count'+2
} else {
local ++count
}
}
scalar count=`count'
''')
    assert result.rc == 0, result.output
    assert engine.scalars['count'] == 4


def test_numlist_command_checks_constraints_and_stores_expansion(engine):
    result = engine.run('numlist "-2(2)4 5 7 to 11", integer ascending\nlocal list `r(numlist)\'\n')
    assert result.rc == 0, result.output
    assert engine.locals['list'] == '-2 0 2 4 5 7 9 11'
    assert engine.run('numlist "1.5", integer').rc == 126
    assert engine.run('numlist "1 0", ascending').rc == 124
    assert engine.run('numlist "1 2", range(>1)').rc == 125
    assert engine.run('numlist "1 2", min(3)').rc == 122
    assert engine.run('numlist "1(0)4"').rc != 0


def test_nested_program_tempvars_and_macros_do_not_escape_scope(engine):
    result = engine.run('''local outside "kept"
program define inner
tempvar work
generate `work'=7
local outside "inner"
end
program define outer
tempvar work
generate `work'=5
inner
assert `work'==5
end
outer
assert x[1]==1
''')
    assert result.rc == 0, result.output
    assert engine.locals['outside'] == 'kept'
    assert list(engine.data) == ['x', 'y', 'group', 'w']


def test_program_error_line_and_capture_restore_output_state(engine):
    result = engine.run('quietly program define failure\nerror 321\nend\nfailure\n')
    assert result.rc == 321
    assert result.line == 2
    assert engine._quiet == 0
    assert engine.run('capture failure\ndisplay "continues"').output == 'continues'


def test_do_file_arguments_and_include_share_only_intended_scope(engine, tmp_path):
    (tmp_path/'child.do').write_text('args first second\nscalar argument=`second\'\nlocal private hidden\n', encoding='utf-8')
    (tmp_path/'include.do').write_text('local shared available\n', encoding='utf-8')
    result = engine.run('do "child.do" "two words" 42\ninclude "include.do"\n')
    assert result.rc == 0, result.output
    assert engine.scalars['argument'] == 42
    assert engine.locals['shared'] == 'available'
    assert 'private' not in engine.locals


def test_generate_default_float_explicit_double_and_replace_quantization(engine):
    result = engine.run('clear\nset obs 2\ngen x=1.2\ngen double y=1.2\nreplace x=1.3 in 2\n'
                        'set type double\ngen z=1.2\nlocal xtype: type x\n')
    assert result.rc == 0, result.output
    assert engine.data.x.dtype == np.dtype('float32')
    assert engine.data.x[0] == np.float32(1.2)
    assert engine.data.x[1] == np.float32(1.3)
    assert engine.data.y.dtype == np.dtype('float64')
    assert engine.data.y[0] == 1.2
    assert engine.data.z[0] == 1.2
    assert engine.locals['xtype'] == 'float'
    assert engine.formats == {}


def test_legacy_source_encoding_urls_and_exit_clear(engine, tmp_path):
    source = tmp_path/'legacy.do'
    source.write_bytes('display "Espa\u00f1a"\nexit, clear\nerror 459\n'.encode('cp1252'))
    result = engine.run_file(source)
    assert result.rc == 0, result.output
    assert result.output == 'Espa\u00f1a'
    assert [s.text for s in lex('net install demo, from(https://example.org/path) // comment')] == ['net install demo, from(https://example.org/path)']


def test_package_install_describe_uninstall_all_and_local_adopath(engine, tmp_path):
    from openstat.packages import PackageManager
    source = tmp_path/'source'
    source.mkdir()
    (source/'demo.pkg').write_text('v 3\nd Synthetic package\nf demo.ado\nf example.do\n', encoding='utf-8')
    (source/'demo.ado').write_text('program define demo, rclass\nsyntax [, Count(integer 7)]\nreturn scalar answer=`count\'\nend\n', encoding='utf-8')
    (source/'example.do').write_text('display "example"', encoding='utf-8')
    engine.packages = PackageManager(tmp_path/'ado')
    result = engine.run('ssc describe demo, from("source")\n'
                        'ssc install demo, from("source") all\ndemo, count(9)\nscalar answer=r(answer)\n')
    assert result.rc == 0, result.output
    assert engine.scalars['answer'] == 9
    assert (tmp_path/'example.do').is_file()
    assert 'Synthetic package' in result.output
    assert engine.run('ssc uninstall demo').rc == 0
    assert engine.packages.find('demo') is None
    assert (tmp_path/'example.do').is_file()
    local = tmp_path/'local'
    local.mkdir()
    (local/'custom.ado').write_text('program define custom\nscalar localado=42\nend\n', encoding='utf-8')
    result = engine.run('adopath ++ "local"\ncustom\nadopath - "local"\n')
    assert result.rc == 0, result.output
    assert engine.scalars['localado'] == 42
    assert engine._find_ado('custom') is None


def test_inline_if_else_and_else_if_skip_inactive_commands(engine):
    result = engine.run('if 0 error 321\nelse if 1 scalar branch=2\nelse error 459\n'
                        'if 1 scalar direct=3\nelse error 459\n')
    assert result.rc == 0, result.output
    assert engine.scalars == {'branch': 2, 'direct': 3}


def test_sortpreserve_hides_marker_from_varlist_and_restores_order_on_error(engine):
    result = engine.run('''program define sorter, rclass sortpreserve
syntax [varlist]
local variables: word count `varlist'
sort x
return scalar variables=`variables'
end
sorter
scalar variables=r(variables)
program define badsort, sortpreserve
gsort -x
error 321
end
capture badsort
''')
    assert result.rc == 0, result.output
    assert engine.scalars['variables'] == 4
    assert engine.data.x[:2].tolist() == [1, 2]
    assert np.isnan(engine.data.x[2])
    assert engine.data.x[3] == 4
    assert list(engine.data) == ['x', 'y', 'group', 'w']
    assert not engine._hidden_variables


def test_mark_preserves_syntax_sample_contract_and_byable_program_execution(engine):
    result = engine.run('''program define groupmark, rclass byable(recall)
syntax varname [if]
tempvar sample
mark `sample' `if'
markout `sample' `varlist'
count if `sample'
return scalar selected=r(N)
end
bys group: groupmark x if x>=1
scalar selected=r(selected)
''')
    assert result.rc == 0, result.output
    assert engine.scalars['selected'] == 1
    assert set(engine.data) == {'x', 'y', 'group', 'w'}


def test_display_stream_formats_controls_compound_strings_and_expressions(engine):
    result = engine.run('''display in text "Header" _col(10) as res %5.0f 12 " " %4s "x"
display as txt `"a "quote""' _skip(2) as result 1+2 "!"
display "a" + " " + "b"
display "{hline 3}{c +}{hline 2}"
summarize x, meanonly
''')
    assert result.rc == 0, result.output
    assert result.output.splitlines() == ['Header      12    x', 'a "quote"  3!', 'a b', '───+──']


def test_loop_headers_expand_macros_once_before_parsing(engine):
    """Loop bounds are expanded before the numlist/varlist is parsed.

    ``forvalues t = $start/$end`` is three tokens short of a numlist unless
    the header is expanded first, and ``foreach x in `list'`` would iterate
    over the literal macro reference.  A ``while`` header, by contrast, must
    be re-expanded on every pass, so the expansion belongs to the loop
    handlers and not to the statement walker.
    """
    result = engine.run('''global start 2
global end 4
forvalues t = $start/$end {
    display "t=`t'"
}
local names a b c
foreach x in `names' {
    display "x=`x'"
}
foreach v of varlist x y {
    display "v=`v'"
}
local i 0
while `i' < 3 {
    local i = `i' + 1
    display "w=`i'"
}
''')
    assert result.rc == 0, result.output
    assert result.output.splitlines() == [
        't=2', 't=3', 't=4', 'x=a', 'x=b', 'x=c', 'v=x', 'v=y',
        'w=1', 'w=2', 'w=3']


def test_unmodified_downloaded_distinct_optional_integration(tmp_path):
    """Set OPENSTAT_SSC_TEST_ROOT to locally downloaded packages for this QA."""
    import os
    from pathlib import Path
    root = os.environ.get('OPENSTAT_SSC_TEST_ROOT')
    if not root:
        pytest.skip('optional external package fixture not supplied')
    package_engine = Engine(tmp_path, ado_dir=Path(root))
    result = package_engine.run('set obs 5\ngen x=ceil(_n/2)\ngen y=mod(_n,2)\n'
                                'distinct x, joint\nscalar joint=r(ndistinct)\n'
                                'distinct x\nscalar single=r(ndistinct)\n'
                                'distinct x y, joint\nscalar combined=r(ndistinct)')
    assert result.rc == 0, result.output
    assert package_engine.scalars == {'joint': 3, 'single': 3, 'combined': 5}
    assert package_engine.data.x.tolist() == [1, 1, 2, 2, 3]
    assert list(package_engine.data) == ['x', 'y']
