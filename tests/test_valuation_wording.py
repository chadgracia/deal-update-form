import json
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


    def _spv_sell_form(self):
        deal = {"id": 123, "company": {"name": "Acme", "id": 9},
                "custom_fields": {"custom_label_1958": [lf.SELL_TYPE_ID],
                                  lf.STRUCTURE_FIELD: [lf.SPV_STRUCTURE_ID]}}
        return lf.render_form(deal, {}, "u", [], "")["body"]

    def test_est_valuation_helper_and_placeholder(self):
        self.assertEqual(lf.VAL_SHORTHAND_HINT,
                         "Shorthand works &mdash; type 15B or 250M and we&rsquo;ll convert it.")
        body = self._spv_sell_form()
        self.assertIn(f'<p id="estValShortHint" style="font-size:12px;color:#888;margin:0 0 6px 0;display:none">'
                      f'{lf.VAL_SHORTHAND_HINT}</p>', body)
        self.assertIn("if (shortHint) shortHint.style.display = gp ? 'none' : '';", body)
        self.assertIn('name="est_valuation" value="" placeholder="e.g. 15B or 250M"', body)

    def test_unreadable_vs_too_small(self):
        self.assertEqual(lf.VAL_UNREADABLE_MSG, "We couldn't read that number. Try 15B or 250M.")
        self.assertEqual(lf.valuation_input_error("abc"), lf.VAL_UNREADABLE_MSG)
        self.assertEqual(lf.valuation_input_error("15X"), lf.VAL_UNREADABLE_MSG)
        self.assertEqual(lf.valuation_input_error("45"), lf.EST_VAL_SMALL_MSG)
        for ok in ("", "15B", "250mm", "$250M", "250,000,000"):
            self.assertIsNone(lf.valuation_input_error(ok), ok)

    def test_browser_uses_same_messages(self):
        body = self._spv_sell_form()
        self.assertIn(f"small: {json.dumps(lf.EST_VAL_SMALL_MSG)}, unreadable: {json.dumps(lf.VAL_UNREADABLE_MSG)}",
                      body)


if __name__ == "__main__":
    unittest.main()
