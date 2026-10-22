"""Static audit privacy, dependency and scope checks using synthetic scripts."""
import json

from openstat.audit import analyze_source, audit_paths


def test_static_inventory_preserves_prefixes_and_dependency_kinds():
    report = analyze_source('''
* reghdfe ignored comment
capture noisily bysort group: regress y c.x##i.group [pw=weight], vce(cluster id)
ssc install reghdfe, replace
do "private-name.do"
local files : dir "." files "*.dta"
generate double lag = L.x
''')
    assert report["commands"]["regress"] == 1
    assert report["prefixes"] == {"capture": 1, "noisily": 1, "bysort": 1}
    assert report["packages_requested"] == {"reghdfe": 1}
    assert report["features"]["factor_variables_or_interactions"] == 1
    assert report["features"]["weights"] == 1
    assert report["features"]["time_series_operators"] == 1
    assert report["features"]["extended_macro_functions"] == 1
    assert report["dependency_kinds"] == {"do": 1}
    assert "private-name" not in json.dumps(report)
    assert "reghdfe" not in report["commands"]


def test_audit_never_executes_and_default_reports_omit_paths(tmp_path):
    source = 'shell touch "SHOULD-NOT-EXIST"\nuse "private-person.dta", clear\n'
    script = tmp_path / "private-project.do"
    script.write_text(source)
    (tmp_path / "copy.do").write_text(source)
    (tmp_path / "._mac-metadata.do").write_bytes(b"\x00\x00")
    (tmp_path / "unrelated.txt").write_text("not part of inventory")
    skipped = tmp_path / ".local"
    skipped.mkdir()
    (skipped / "ignored.do").write_text("regress x y")
    result = audit_paths([tmp_path])
    assert result["execution_performed"] is False
    assert result["files_analyzed"] == 2
    assert result["unique_contents"] == 1
    assert result["duplicate_files"] == 1
    serialized = json.dumps(result)
    assert str(tmp_path) not in serialized
    assert "private-project" not in serialized
    assert "private-person" not in serialized
    assert "SHOULD-NOT-EXIST" not in serialized
    assert not (tmp_path / "SHOULD-NOT-EXIST").exists()
    assert result["unique_content_totals"]["commands"]["shell"] == 1
    private = audit_paths([script], include_paths=True)
    assert private["files"][0]["path"] == str(script)


def test_mata_programs_comments_and_bounded_reads(tmp_path):
    source = '''
/*
fake_command, fakeoption
*/
program define my_custom
    display 1
end
my_custom
mata:
real matrix x
x = (1, 2 \\ 3, 4)
end
regress y /// continuation
    x, robust
'''
    result = analyze_source(source)
    assert result["locally_defined_program_count"] == 1
    assert "my_custom" not in result["unrecognized_commands"]
    assert "fake_command" not in result["commands"]
    assert "real" not in result["commands"]
    assert result["features"]["mata_statement"] == 2
    assert result["commands"]["regress"] == 1
    script = tmp_path / "oversized.do"
    script.write_text(source)
    report = audit_paths([script], max_bytes=5)
    assert report["files_analyzed"] == 0
    assert report["errors"] == [{"reason": "file_exceeds_byte_limit"}]


def test_version_prefix_and_semicolon_delimiter():
    result = analyze_source('#delimit ;\nversion 17: regress y x, robust;\ndisplay "unused.do";\n')
    assert result["commands"] == {"version": 1, "regress": 1, "display": 1}
    assert result["options"]["robust"] == 2  # visible on version prefix and inner command
    assert not result["dependency_kinds"]


def test_mixin_commands_recognized_and_explicit_file_exclusions(tmp_path):
    report = analyze_source('recode x (1=2)\nmark valid\npostclose h\nreghdfe y x, absorb(id)')
    assert not report['unrecognized_commands']
    excluded = tmp_path / 'OpenStat'
    excluded.mkdir()
    script = excluded / 'own-fixture.do'
    script.write_text('display 1')
    assert audit_paths([script], exclude=['openstat'])['files_discovered'] == 0
