"""Synthetic data-management conformance and failure atomicity checks."""
import numpy as np
import pandas as pd
import pytest

from openstat import Engine


@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path, ado_dir=tmp_path/'ado')


def run(engine, script):
    result = engine.run(script)
    assert result.rc == 0, result.output
    return result


def test_duplicates_tag_counts_missing_keys_and_sample(engine):
    engine.data = pd.DataFrame({'x': [1., 1., 2., np.nan, np.nan], 's': ['a', 'a', 'b', '', '']})
    run(engine, 'duplicates tag x if _n>1, gen(tag)')
    assert np.isnan(engine.data.tag[0])
    assert engine.data.tag[1:].tolist() == [0, 0, 1, 1]
    run(engine, 'duplicates report x')
    assert engine.returns == {'N': 5, 'unique_value': 3}


def test_duplicates_drop_requires_force_and_preserves_first_record(engine):
    engine.data = pd.DataFrame({'x': [1, 1, 2, 2], 's': ['first', 'second', 'third', 'fourth']})
    assert engine.run('duplicates drop x').rc == 198
    assert len(engine.data) == 4
    run(engine, 'duplicates drop x if _n<4, force')
    assert engine.data.s.tolist() == ['first', 'third', 'fourth']
    assert engine.returns == {'N': 3, 'N_drop': 1}


def test_isid_rejects_duplicates_missing_and_sorts_when_requested(engine):
    engine.data = pd.DataFrame({'id': [3., 1., 2., np.nan]})
    assert engine.run('isid id').rc == 459
    run(engine, 'isid id, missok sort')
    assert engine.data.id[:3].tolist() == [1, 2, 3]
    engine.data.loc[3, 'id'] = 2
    assert engine.run('isid id, missok').rc == 459


def test_recode_first_match_wins_ranges_missing_and_labels(engine):
    run(engine, 'set obs 5\ngen x=_n\nreplace x=. in 5\n'
        'recode x (1/2=0 "Low") (2/3=1 "Middle") (nonmissing=2 "High") (missing=9 "Missing"), gen(category) label(groups) test')
    assert engine.data.category.tolist() == [0, 0, 1, 2, 9]
    assert engine.data.x[:4].tolist() == [1, 2, 3, 4]
    assert engine.value_labels['groups'] == {0: 'Low', 1: 'Middle', 2: 'High', 9: 'Missing'}
    assert engine.value_label_names['category'] == 'groups'


def test_recode_sample_min_max_copyrest_and_swaps(engine):
    run(engine, 'set obs 4\ngen x=_n\nrecode x (min=10) (max=20) if x>1, gen(a)\n'
        'recode x (1=2) (2=1) if x<3, gen(b) copyrest\nrecode x (1=2) (2=1)')
    assert np.isnan(engine.data.a[0])
    assert engine.data.a[1:].tolist() == [10, 3, 20]
    assert engine.data.b.tolist() == [2, 1, 3, 4]
    assert engine.data.x.tolist() == [2, 1, 3, 4]


def test_recode_validates_all_variables_and_rules_before_writing(engine):
    engine.data = pd.DataFrame({'x': [1, 2], 's': ['a', 'b']})
    before = engine.data.copy()
    assert engine.run('recode x s (1=2), prefix(new)').rc == 109
    pd.testing.assert_frame_equal(before, engine.data)
    assert engine.run('recode x (else=2) (1=3)').rc == 198
    assert engine.run('recode x (1=2), gen(y) unknown').rc == 198


def test_expand_rounds_counts_retains_original_order_and_marks_copies(engine):
    engine.data = pd.DataFrame({'id': [1, 2, 3, 4, 5], 'copies': [-2., 0., 1.49, 1.5, 2.5]})
    run(engine, 'expand copies, generate(copy)')
    assert engine.data.id.tolist() == [1, 2, 3, 4, 5, 4, 5, 5]
    assert engine.data['copy'].tolist() == [0, 0, 0, 0, 0, 1, 1, 1]
    run(engine, 'keep if copy==0')
    assert engine.data.id.tolist() == [1, 2, 3, 4, 5]


def test_expand_qualifiers_and_invalid_count_preserve_data(engine):
    run(engine, 'set obs 3\ngen x=_n\nexpand 3 if x==2')
    assert engine.data.x.tolist() == [1, 2, 3, 2, 2]
    before = engine.data.copy()
    assert engine.run('expand 1000000000').rc == 901
    pd.testing.assert_frame_equal(before, engine.data)


def test_group_rename_is_atomic_and_carries_labels_and_formats(engine):
    run(engine, 'set obs 2\ngen a=_n\ngen b=2*_n\nlabel variable a "A"\nformat a %9.2f\n'
        'rename (a b) (b a)')
    assert list(engine.data) == ['b', 'a']
    assert engine.data.b.tolist() == [1, 2]
    assert engine.variable_labels['b'] == 'A'
    assert engine.formats['b'] == '%9.2f'
    assert engine._storage_types['b'] == 'float'
    assert engine.run('rename (b a) (c c)').rc == 110
    assert list(engine.data) == ['b', 'a']
    run(engine, 'rename *, upper')
    assert list(engine.data) == ['B', 'A']


def test_cross_combines_all_pairs_retains_master_overlap_and_imports_metadata(engine, tmp_path):
    using = pd.DataFrame({'u': [10, 20], 'x': [999, 999]})
    using.to_stata(tmp_path/'using.dta', write_index=False, variable_labels={'u': 'Using label'})
    engine.data = pd.DataFrame({'x': [1, 2, 3]})
    run(engine, 'cross using using')
    assert len(engine.data) == 6
    assert set(map(tuple, engine.data[['x', 'u']].to_numpy())) == {(x, u) for x in [1, 2, 3] for u in [10, 20]}
    assert engine.variable_labels['u'] == 'Using label'


def test_joinby_many_to_many_unmatched_and_update(engine, tmp_path):
    pd.DataFrame({'key': [1, 1, 3], 'value': [10., 11., 30.], 'u': ['a', 'b', 'c']}).to_stata(tmp_path/'using.dta', write_index=False)
    engine.data = pd.DataFrame({'key': [1, 1, 2], 'value': [5., np.nan, 20.], 'm': ['x', 'y', 'z']})
    run(engine, 'joinby key using using, unmatched(both) merge(origin) update')
    assert len(engine.data) == 6
    assert engine.data.origin.value_counts().to_dict() == {3: 4, 1: 1, 2: 1}
    matched = engine.data.loc[engine.data.origin.eq(3)]
    assert sorted(matched.value.tolist()) == [5, 5, 10, 11]
    assert engine.data.loc[engine.data.origin.eq(2), 'value'].tolist() == [30]
    assert engine.data.loc[engine.data.origin.eq(1), 'u'].tolist() == ['']


def test_joinby_default_keys_and_default_no_merge_variable(engine, tmp_path):
    pd.DataFrame({'key': [1, 1, 2], 'u': [10, 11, 20]}).to_stata(tmp_path/'using.dta', write_index=False)
    engine.data = pd.DataFrame({'key': [1, 2, 3], 'm': [4, 5, 6]})
    run(engine, 'joinby using using')
    assert len(engine.data) == 3
    assert '_merge' not in engine.data
    assert sorted(engine.data.key.tolist()) == [1, 1, 2]


def test_recast_lossless_numeric_string_force_and_atomicity(engine):
    run(engine, 'set obs 3\ngen double x=_n\ngen double fraction=_n/10\ngen str10 text="abc"\nrecast byte x')
    assert engine.data.x.dtype == np.dtype('int8')
    assert engine._storage_types['x'] == 'byte'
    assert engine.run('recast byte fraction').rc == 459
    assert engine.data.fraction.tolist() == [.1, .2, .3]
    assert engine.run('recast str2 text').rc == 459
    run(engine, 'recast str2 text, force\nrecast byte fraction, force')
    assert engine.data.text.tolist() == ['ab']*3
    assert engine.data.fraction.tolist() == [0, 0, 0]


def test_compress_preserves_values_and_updates_storage(engine):
    run(engine, 'set obs 3\ngen double x=_n\ngen double y=_n/2\ngen str20 text="hi"\ncompress')
    assert engine._storage_types == {'x': 'byte', 'y': 'float', 'text': 'str2'}
    assert engine.data.x.tolist() == [1, 2, 3]
    assert engine.data.y.tolist() == [.5, 1, 1.5]


def test_preserve_restore_restores_storage_metadata(engine):
    run(engine, 'set obs 3\ngen double x=_n\npreserve\nrecast byte x\nrestore')
    assert engine._storage_types['x'] == 'double'
    assert engine.data.x.dtype == np.dtype('float64')


def test_saved_storage_types_preserve_integer_missing_and_float_widths(engine, tmp_path):
    import pyreadstat
    run(engine, 'set obs 3\ngen double x=_n\nreplace x=. in 2\nrecast byte x\n'
        'gen y=1.2\ngen double z=1.2\nsave "types.dta"')
    _, metadata = pyreadstat.read_dta(tmp_path/'types.dta')
    assert metadata.readstat_variable_types == {'x': 'int8', 'y': 'float', 'z': 'double'}
