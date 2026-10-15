"""Fast tests against synthetic, licensed-Stata-generated reference values.

These tests cover only the included examples. They do not certify full language,
statistics, package, or GUI compatibility. No Stata installation is needed here;
qa/run_differential.py refreshes live comparison evidence when available.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from openstat.engine import Engine
from qa.run_differential import CASE_DIR, CASES, inspect_dta, inspect_png, parse_scalars

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = json.loads((ROOT / "qa" / "stata_reference.json").read_text(encoding="utf-8"))
REFERENCE_CASES = {entry["case"]: entry for entry in REFERENCE["cases"]}


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="openstat-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.cwd = Path(self.temporary.name)
        self.engine = Engine(cwd=self.cwd)

    def run_fixture(self, name: str):
        result = self.engine.run_file(CASE_DIR / f"{name}.do")
        reference = REFERENCE_CASES[name]
        self.assertEqual(result.rc, reference["rc"], msg=f"{name}: {result.output}")
        observed = parse_scalars(result.output)
        self.assertEqual(set(observed), set(reference["scalars"]), msg=result.output)
        for key, expected in reference["scalars"].items():
            with self.subTest(fixture=name, scalar=key):
                self.assertAlmostEqual(observed[key], expected, delta=max(1e-9, abs(expected) * 1e-8))
        return result

    def test_reference_scripts_have_not_changed(self):
        for name, reference in REFERENCE_CASES.items():
            with self.subTest(fixture=name):
                source = CASE_DIR / f"{name}.do"
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), reference["sha256"], "Update live Stata reference after changing a fixture")

    def test_data_generation_group_mean_and_stata_missing_order(self):
        self.run_fixture("data")
        self.assertEqual(list(self.engine.data["id"]), [4, 5, 6, 7, 3])
        self.assertTrue((self.cwd / "data.csv").is_file())

    def test_macros_loops_program_args_return_and_capture(self):
        self.run_fixture("macros")

    def test_regression_coefficients_standard_errors_and_stored_results(self):
        self.run_fixture("regression")

    def test_hc1_robust_regression_standard_errors(self):
        self.run_fixture("robust")

    def test_logistic_stores_log_odds_and_displays_odds_ratios(self):
        result = self.run_fixture("logistic")
        self.assertIn("Odds ratio", result.output)

    def test_recursive_and_programming_semantics(self):
        self.run_fixture("semantics")
        self.assertEqual(list(self.engine.data["y"]), [1., 2., 3., 4., 5.])
        self.assertTrue(pd.api.types.is_numeric_dtype(self.engine.data["flag"]))
        self.assertFalse(pd.api.types.is_bool_dtype(self.engine.data["flag"]))

    def test_generate_rejects_reference_to_nonexistent_variable(self):
        result = self.run_fixture("self_reference")
        self.assertEqual(result.line, 4)

    def test_dta118_roundtrip_preserves_variable_and_value_labels(self):
        self.run_fixture("dta")
        metadata = inspect_dta(self.cwd / "roundtrip.dta")
        self.assertEqual(metadata.get("release"), 118, msg=metadata)
        self.assertEqual(metadata.get("rows"), 4)
        self.assertEqual(metadata["variable_labels"]["outcome"], "Synthetic outcome")
        self.assertIn({"0": "Control", "1": "Treated"}, metadata["value_labels"].values())
        self.assertEqual(metadata["labeled_values"]["treatment"], ["Treated", "Control", "Treated", "Control"])

    def test_preserve_restore(self):
        self.run_fixture("preserve")

    def test_merge_one_to_one(self):
        self.run_fixture("merge")
        self.assertEqual(list(self.engine.data["_merge"]), [3, 3, 3, 1])
        self.assertIn("Matched (3)", (self.cwd / "merged.csv").read_text(encoding="utf-8"))

    def test_append(self):
        self.run_fixture("append")
        self.assertEqual(list(self.engine.data["id"]), [1, 2, 3, 4])

    def test_reshape_roundtrip(self):
        self.run_fixture("reshape")
        self.assertEqual(list(self.engine.data["x2"]), [11, 21, 31])

    def test_mata_subset_matrix_transpose_product_and_st_matrix(self):
        self.run_fixture("mata")

    def test_graph_export_is_real_png_at_requested_width(self):
        self.run_fixture("graph")
        metadata = inspect_png(self.cwd / "scatter.png")
        self.assertTrue(metadata.get("valid_png"), msg=metadata)
        self.assertEqual(metadata["width"], 640)
        self.assertGreater(metadata["bytes"], 100)

    def test_unknown_command_fails_at_source_line(self):
        result = self.run_fixture("unsupported")
        self.assertEqual(result.line, 3)

    def test_unknown_option_is_not_silently_ignored(self):
        result = self.run_fixture("invalid_option")
        self.assertEqual(result.line, 4)

    def test_error_stops_following_commands(self):
        result = self.engine.run('display "before"\nnonexistent_qa_command\ndisplay "must_not_run"')
        self.assertEqual(result.rc, 199)
        self.assertEqual(result.line, 2)
        self.assertNotIn("must_not_run", result.output)

    def test_batch_cli_writes_machine_readable_status(self):
        script = self.cwd / "batch.do"
        script.write_text('clear\nset obs 3\ngenerate x = _n\nsummarize x\n', encoding="utf-8")
        report = self.cwd / "batch.json"
        process = subprocess.run(
            [sys.executable, "-m", "openstat", "--batch", str(script), "--json", str(report)],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(process.returncode, 0, msg=process.stderr + process.stdout)
        status = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(status["rc"], 0)

    def test_save_requires_replace_and_does_not_overwrite_on_error(self):
        result = self.engine.run('set obs 2\ngenerate x = _n\nsave "guard.dta"')
        self.assertEqual(result.rc, 0, msg=result.output)
        path = self.cwd / "guard.dta"
        before = path.read_bytes()
        result = self.engine.run('replace x = 99\nsave "guard.dta"')
        self.assertEqual(result.rc, 602)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_use_file_returns_601(self):
        result = self.engine.run('use "does_not_exist.dta", clear')
        self.assertEqual(result.rc, 601)

    def test_csv_import_and_value_label_export(self):
        (self.cwd / "input.csv").write_text("ID,Outcome\n1,4.5\n2,\n", encoding="utf-8")
        result = self.engine.run('import delimited using "input.csv", clear\nlabel define id_label 1 "One" 2 "Two"\nlabel values id id_label\nexport delimited using "labeled.csv"\nexport delimited using "numeric.csv", nolabel')
        self.assertEqual(result.rc, 0, msg=result.output)
        self.assertEqual(list(self.engine.data), ["id", "outcome"])
        self.assertIn("One", (self.cwd / "labeled.csv").read_text(encoding="utf-8"))
        self.assertNotIn("One", (self.cwd / "numeric.csv").read_text(encoding="utf-8"))

    def test_excel_export_import_with_variable_names(self):
        self.engine.data = pd.DataFrame({"id": [1, 2], "outcome": [1.25, 2.5]})
        result = self.engine.run('export excel using "roundtrip.xlsx", firstrow(variables)\nclear\nimport excel using "roundtrip.xlsx", firstrow clear')
        self.assertEqual(result.rc, 0, msg=result.output)
        self.assertEqual(list(self.engine.data), ["id", "outcome"])
        self.assertEqual(list(self.engine.data["outcome"]), [1.25, 2.5])

    def test_merge_keeps_master_overlap_even_when_missing(self):
        pd.DataFrame({"id": [1, 2, 3], "x": [99., 88., 77.], "new": [10., 20., 30.]}).to_stata(self.cwd / "using.dta", write_index=False, version=118)
        self.engine.data = pd.DataFrame({"id": [1, 2], "x": [5., float("nan")]})
        result = self.engine.run('merge 1:1 id using "using.dta"')
        self.assertEqual(result.rc, 0, msg=result.output)
        self.assertEqual(self.engine.data.loc[0, "x"], 5.)
        self.assertTrue(pd.isna(self.engine.data.loc[1, "x"]))
        self.assertEqual(self.engine.data.loc[2, "x"], 77.)
        self.assertEqual(list(self.engine.data["_merge"]), [3, 3, 2])

    def test_merge_rejects_duplicate_keys_without_changing_master(self):
        pd.DataFrame({"id": [1, 1], "y": [2., 3.]}).to_stata(self.cwd / "duplicates.dta", write_index=False, version=118)
        self.engine.data = pd.DataFrame({"id": [1, 2], "x": [5., 6.]})
        before = self.engine.data.copy()
        result = self.engine.run('merge 1:1 id using "duplicates.dta"')
        self.assertEqual(result.rc, 459)
        pd.testing.assert_frame_equal(before, self.engine.data)

    def test_append_rejects_incompatible_types_without_changing_master(self):
        pd.DataFrame({"x": ["text"]}).to_stata(self.cwd / "strings.dta", write_index=False, version=118)
        self.engine.data = pd.DataFrame({"x": [1.]})
        self.engine.variable_labels = {"x": "Original"}
        result = self.engine.run('append using "strings.dta"')
        self.assertEqual(result.rc, 106)
        self.assertEqual(list(self.engine.data["x"]), [1.])
        self.assertEqual(self.engine.variable_labels, {"x": "Original"})

    def test_collapse_statistics_by_group(self):
        self.engine.data = pd.DataFrame({"group": [1, 1, 2, 2], "x": [1., 3., 2., float("nan")]})
        result = self.engine.run('collapse (mean) avg = x (sum) total=x (count) n=x (sd) sd=x, by(group)')
        self.assertEqual(result.rc, 0, msg=result.output)
        self.assertEqual(list(self.engine.data["avg"]), [2., 2.])
        self.assertEqual(list(self.engine.data["total"]), [4., 2.])
        self.assertEqual(list(self.engine.data["n"]), [2, 1])
        self.assertAlmostEqual(self.engine.data.loc[0, "sd"], 2.**0.5)
        self.assertTrue(pd.isna(self.engine.data.loc[1, "sd"]))

    def test_reshape_wide_rejects_varying_invariant_column(self):
        self.engine.data = pd.DataFrame({"id": [1, 1], "period": [1, 2], "x": [10., 20.], "constant": [1, 2]})
        result = self.engine.run('reshape wide x, i(id) j(period)')
        self.assertEqual(result.rc, 9)


class HarnessTests(unittest.TestCase):
    def test_sentinel_parser_excludes_echoed_stata_commands(self):
        output = '. display "__OPENSTAT__n=" %21.14g r(N)\n__OPENSTAT__n=                    7\n'
        self.assertEqual(parse_scalars(output), {"n": 7.0})

    def test_online_install_is_explicitly_opt_in(self):
        online = [case.name for case in CASES if case.online]
        self.assertEqual(online, ["ssc"])


if __name__ == "__main__":
    unittest.main()
