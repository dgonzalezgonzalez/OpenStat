"""Metadata and matrix behavioral checks, independent of a Stata installation."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from openstat.engine import Engine


class MetadataTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="openstat-metadata-")
        self.addCleanup(directory.cleanup)
        self.engine = Engine(cwd=Path(directory.name))
        self.engine.data = pd.DataFrame({"group": [0, 1, 1, np.nan], "value": [1.25, 2.5, 3.75, 5.]})

    def command(self, source):
        result = self.engine.run(source)
        self.assertEqual(result.rc, 0, result.output)
        return result

    def test_labels_survive_dta_roundtrip(self):
        self.command('label variable value "Synthetic outcome"\nlabel data "QA data"\nlabel define groups 0 "Control" 1 "Treated"\nlabel values group groups')
        destination = self.engine.cwd / "labels.dta"
        self.engine._write(destination)
        data = self.engine._read(destination)
        self.assertEqual(self.engine.variable_labels["value"], "Synthetic outcome")
        self.assertEqual(self.engine.dataset_label, "QA data")
        label_name = self.engine.value_label_names["group"]
        self.assertEqual(self.engine.value_labels[label_name], {0: "Control", 1: "Treated"})
        self.assertEqual(data["group"].iloc[1], 1)

    def test_label_replace_add_modify_and_type_validation(self):
        self.command('label define groups 0 "Control"\nlabel define groups 1 "Treated", add\nlabel define groups 1 "Treatment", modify')
        self.assertEqual(self.engine.value_labels["groups"], {0: "Control", 1: "Treatment"})
        result = self.engine.run('label define groups 1 "Wrong", add')
        self.assertNotEqual(result.rc, 0)
        self.assertEqual(self.engine.value_labels["groups"][1], "Treatment")
        self.command('label define groups 4 "New", replace')
        self.assertEqual(self.engine.value_labels["groups"], {4: "New"})
        self.engine.data["string"] = ["A", "B", "", "A"]
        result = self.engine.run("label values string groups")
        self.assertEqual(result.rc, 181)
        self.assertNotIn("string", self.engine.value_label_names)

    def test_encode_decode_empty_strings_and_variable_label(self):
        self.engine.data["color"] = ["red", "blue", "", "red"]
        self.command('label variable color "Color name"\nencode color, generate(color_id)\ndecode color_id, generate(back)')
        np.testing.assert_allclose(self.engine.data["color_id"], [2, 1, np.nan, 2], equal_nan=True)
        self.assertEqual(self.engine.data["back"].tolist(), ["red", "blue", "", "red"])
        self.assertEqual(self.engine.variable_labels["color_id"], "Color name")
        self.assertEqual(self.engine.variable_labels["back"], "Color name")
        self.assertNotIn("back", self.engine.value_label_names)

    def test_encode_noextend_failure_is_atomic(self):
        self.engine.data["color"] = ["red", "blue", "", "red"]
        self.command('label define colors 1 "blue"')
        result = self.engine.run("encode color, generate(color_id) label(colors) noextend")
        self.assertEqual(result.rc, 182)
        self.assertNotIn("color_id", self.engine.data)
        self.assertEqual(self.engine.value_labels["colors"], {1: "blue"})

    def test_destring_invalid_does_not_partly_modify_data(self):
        self.engine.data["a"] = ["1", "2", ".", ""]
        self.engine.data["b"] = ["1", "bad", "3", "4"]
        result = self.engine.run("destring a b, replace")
        self.assertEqual(result.rc, 7)
        self.assertEqual(self.engine.data["a"].tolist(), ["1", "2", ".", ""])
        self.command("destring a b, generate(aa bb) force")
        np.testing.assert_allclose(self.engine.data["aa"], [1, 2, np.nan, np.nan], equal_nan=True)
        self.assertTrue(pd.isna(self.engine.data["bb"].iloc[1]))

    def test_destring_ignore_tostring_precision_and_options(self):
        self.engine.data["currency"] = ["$1,234.50", "$2", "", "."]
        self.command('destring currency, generate(amount) ignore("$,")\ntostring amount, generate(text) format(%12.2f)')
        self.assertEqual(self.engine.data["text"].tolist(), ["1234.50", "2.00", ".", "."])
        result = self.engine.run("tostring value, generate(lossy) format(%9.0f)")
        self.assertEqual(result.rc, 7)
        self.assertNotIn("lossy", self.engine.data)
        self.command("tostring value, generate(lossy) format(%9.0f) force")
        result = self.engine.run("destring currency, replace unknown")
        self.assertEqual(result.rc, 198)

    def test_formats_validate_types_before_mutation(self):
        self.command("format group value %9.2f")
        self.assertEqual(self.engine.formats["group"], "%9.2f")
        self.engine.data["name"] = ["a", "b", "c", "d"]
        result = self.engine.run("format value name %12.3f")
        self.assertEqual(result.rc, 120)
        self.assertEqual(self.engine.formats["value"], "%9.2f")

    def test_matrix_product_transpose_and_namespace_isolation(self):
        self.engine.mata.variables["A"] = np.eye(9)
        original = self.engine.mata.variables
        self.command("matrix A=(1,2\\3,4)\nmatrix B=A'*A\nmatrix C=I(2)*A\nmatrix list B")
        np.testing.assert_array_equal(self.engine.matrices["B"], [[10, 14], [14, 20]])
        np.testing.assert_array_equal(self.engine.matrices["C"], [[1, 2], [3, 4]])
        self.assertIs(self.engine.mata.variables, original)
        self.assertEqual(self.engine.mata.variables["A"].shape, (9, 9))
        result = self.engine.run("matrix X=I(3)*A")
        self.assertEqual(result.rc, 503)
        self.assertIs(self.engine.mata.variables, original)
        self.assertNotIn("X", self.engine.matrices)

    def test_matrix_estimation_results_and_side_effect_rejection(self):
        self.engine.matrices["e(b)"] = np.array([[2., 3.]])
        self.engine.matrices["e(V)"] = np.eye(2)
        self.command("matrix beta=e(b)\nmatrix variance=e(V)\nmatrix drop beta")
        np.testing.assert_array_equal(self.engine.matrices["variance"], np.eye(2))
        self.assertNotIn("beta", self.engine.matrices)
        result = self.engine.run('matrix hidden=st_matrix("injected",I(2))')
        self.assertEqual(result.rc, 199)
        self.assertNotIn("injected", self.engine.matrices)


if __name__ == "__main__":
    unittest.main()
