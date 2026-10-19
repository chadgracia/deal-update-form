import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf


class ValuationWordingTests(unittest.TestCase):
    def test_round_hint_mentions_shorthand(self):
        self.assertEqual(lf.ROUND_VAL_HINT,
                         "The valuation of the round you&rsquo;re syndicating, before the new money. "
                         "Shorthand works &mdash; type 15B or 250M and we&rsquo;ll convert it.")

    def test_small_valuation_error(self):
        self.assertEqual(lf.EST_VAL_SMALL_MSG,
                         "That looks too small for a company valuation. Try 15B or 250M — no need to type the full number.")

    def test_old_strings_gone_and_placeholder(self):
        src = open(lf.__file__).read()
        self.assertNotIn("Enter the full company valuation", src)
        self.assertNotIn('placeholder="e.g. 150,000,000 or 150M"', src)
        self.assertIn('name="est_valuation" value="{est_val_cur}" placeholder="e.g. 15B or 250M"', src)

    def test_shorthand_inputs_parse(self):
        cases = {"15B": 15e9, "15b": 15e9, "1.5B": 1.5e9, "250M": 250e6, "250m": 250e6,
                 "250mm": 250e6, "$250M": 250e6, "250,000,000": 250e6}
        for raw, want in cases.items():
            self.assertEqual(lf.parse_valuation(raw), want, raw)


if __name__ == "__main__":
    unittest.main()
