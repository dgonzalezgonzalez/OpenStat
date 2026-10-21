"""Macro expansion of the return, e-class and c-class values.

The expected strings are the ones Stata 19.5 SE produces; they were measured
against the installed Stata, not guessed.  Stata expands `` `r(name)' ``,
`` `e(name)' `` and `` `c(name)' `` to *text*: a number is rendered with
``%18.0g`` and the padding is removed.
"""
import tempfile
import unittest
from pathlib import Path

from openstat.engine import Engine


class ReturnClassMacroTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="openstat-returns-")
        self.addCleanup(directory.cleanup)
        self.engine = Engine(cwd=Path(directory.name))

    def command(self, source):
        result = self.engine.run(source)
        self.assertEqual(result.rc, 0, result.output)
        return result

    def test_r_values_expand_in_macro_position(self):
        result = self.command("set obs 3\ngenerate x = 1\nsummarize x\n"
                              "display \"`r(mean)'\"")
        self.assertIn("1", result.output)

    def test_r_values_expand_to_the_general_format_text(self):
        # Stata renders the value with %18.0g and strips the padding.
        result = self.command("set obs 3\ngenerate double x = 0.585533385208193\n"
                              "summarize x\ndisplay \"[`r(mean)']\"")
        self.assertIn("[.585533385208193]", result.output)

    def test_a_float_variable_loses_precision_before_the_mean_is_taken(self):
        # ``generate`` creates a float variable, so the mean is the mean of
        # single-precision values.
        result = self.command("set obs 3\ngenerate x = 0.585533385208193\n"
                              "summarize x\ndisplay \"[`r(mean)']\"")
        self.assertIn("[.5855333805084229]", result.output)

    def test_r_values_expand_inside_a_local_assignment(self):
        self.command("set obs 3\ngenerate x = 2\nsummarize x\n"
                     "local m = r(mean)\nscalar doubled = `m' * 2")
        self.assertEqual(self.engine.scalars["doubled"], 4)

    def test_c_values_report_the_data_and_the_session(self):
        result = self.command("set obs 5\ngenerate x = 1\ngenerate y = 2\n"
                              "display \"`c(N)'`c(k)'\"")
        self.assertIn("52", result.output)

    def test_pi_is_available_as_a_c_value(self):
        result = self.command("display \"`c(pi)'\"")
        self.assertIn("3.141592653589793", result.output)

    def test_unknown_class_values_expand_to_nothing(self):
        # Stata does not complain about an unknown name in macro position:
        # `r(nosuchthing)' is simply the empty string.
        result = self.command("display \"[`r(nosuchthing)']\"")
        self.assertIn("[]", result.output)

    def test_an_unknown_r_value_is_missing_in_expression_position(self):
        result = self.command("display r(nosuchthing)")
        self.assertIn(".", result.output)

    def test_an_unknown_c_value_is_an_error_in_expression_position(self):
        result = self.engine.run("display c(nosuchthing)")
        self.assertEqual(result.rc, 133)
        self.assertIn("c(nosuchthing) undefined", result.output)

    def test_only_the_c_values_stata_defines_are_available(self):
        # c(SCS), c(n_processors) and c(endian) are not Stata 19.5 values.
        for name in ("SCS", "n_processors", "endian", "cmd", "release"):
            result = self.engine.run(f"display c({name})")
            self.assertEqual(result.rc, 133, name)

    def test_a_macro_expansion_in_a_string_is_shown_verbatim(self):
        # Inside a string literal the expansion text is used as it is.
        result = self.command("display \"[`c(pi)']\"")
        self.assertIn("[3.141592653589793]", result.output)

    def test_a_macro_expansion_in_an_expression_is_shown_as_a_number(self):
        # Outside a string the text is parsed as a number and then rendered
        # with the default numeric format.
        result = self.command("display `c(pi)'")
        self.assertIn("3.1415927", result.output)


if __name__ == "__main__":
    unittest.main()
