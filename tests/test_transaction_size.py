import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HMAC_SECRET", "test-hmac-key")  # fake key; the code has no fallback
import lambda_function as lf


def _deal(sell=True):
    cf = {lf.DEAL_TYPE_FIELD: [lf.SELL_TYPE_ID if sell else lf.BUY_TYPE_ID],
          lf.STRUCTURE_FIELD: [lf.DIRECT_STRUCTURE_ID]}
    return {"id": 9, "name": "Kraken: Sell", "company": {"id": 3, "name": "Kraken"},
            "primary_contact": {"id": 4, "full_name": "Pat Doe"}, "deal_stage": {"name": "Inquiry"},
            "custom_fields": cf}


class SubmitSizeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.deal = _deal()
        for name, val in (("get_jwt", "jwt"), ("publish_fresh_deal", None), ("confirmed_today", "2026-11-14"),
                          ("seller_landing_url", None), ("load_all_deals", [])):
            p = mock.patch.object(lf, name, return_value=val)
            p.start(); self.addCleanup(p.stop)
        for name in ("send_email", "write_visibility_override"):
            p = mock.patch.object(lf, name)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "call_pipeline_api", side_effect=self._api)
        p.start(); self.addCleanup(p.stop)

    def _api(self, method, endpoint, payload=None, jwt=None):
        self.calls.append((method, endpoint, payload))
        if method == "GET" and endpoint.startswith("/deals/"):
            return {"status": 200, "data": self.deal}
        return {"status": 200, "data": {"id": 9}}

    def _post(self, mn, mx, price="net=37.5", action="confirm"):
        body = (f"deal_id=9&submit_action={action}&{price}&share_count=100&share_class=5077831"
                f"&min_size={mn}&max_size={mx}")
        return lf.handle_post(body, {"token": lf.make_deal_token(9)})

    def _writes(self):
        return [c for c in self.calls if c[0] != "GET"]

    def _rejected(self, r, msg):
        self.assertEqual(r["statusCode"], 400)
        self.assertIn(msg, r["body"])
        self.assertEqual(self._writes(), [])

    def test_price_per_share_rejected_and_kept(self):
        r = self._post("37.50", "45", price="net=")
        self._rejected(r, "not the price per share")
        self.assertIn('name="min_size" value="37.50"', r["body"])
        self.assertIn('name="max_size" value="45"', r["body"])

    def test_not_a_number(self):
        self._rejected(self._post("abc", "200000"), "not the price per share")

    def test_below_minimum(self):
        self._rejected(self._post("50000", "300000"), lf.MIN_SIZE_MSG)
        self.calls.clear()
        self._rejected(self._post("", "249999"), lf.MIN_SIZE_MSG)

    def test_below_minimum_not_per_share_message(self):
        r = self._post("100000", "300000")
        self._rejected(r, lf.MIN_SIZE_MSG)
        self.assertNotIn(lf.SIZE_PER_SHARE_MSG, r["body"])

    def test_at_minimum_accepted(self):
        self._post("250000", "250000")
        self.assertEqual(len([c for c in self.calls if c[0] == "PUT"]), 1)

    def test_min_greater_than_max(self):
        self._rejected(self._post("500000", "300000"), lf.SIZE_ORDER_MSG)

    def test_equals_price(self):
        self._rejected(self._post("300000", "400000", price="net=300000"), lf.SIZE_PER_SHARE_MSG)
        self.deal = _deal(sell=False)
        self.calls.clear()
        self._rejected(self._post("250000", "400000", price="gross=400000"), lf.SIZE_PER_SHARE_MSG)

    def test_dollar_sign_accepted(self):
        r = self._post("%24250%2C000", "%24+300%2C000")
        puts = [c for c in self.calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 1, r.get("body", "")[:300])
        cf = puts[0][2]["deal"]["custom_fields"]
        self.assertEqual(cf[lf.MIN_SIZE_FIELD], 250000.0)
        self.assertEqual(cf[lf.MAX_SIZE_FIELD], 300000.0)

    def test_hold_and_cancel_and_still_active_unaffected(self):
        for action in ("hold", "still_active"):
            self.calls.clear()
            r = self._post("37.5", "45", action=action)
            self.assertNotEqual(r.get("statusCode"), 400, action)
            self.assertTrue(self._writes(), action)
        self.calls.clear()
        r = self._post("37.5", "45", action="cancel")
        self.assertNotEqual(r.get("statusCode"), 400)
        self.assertTrue(self._writes())


class QaSizeTests(unittest.TestCase):
    def m(self, qid, answer):
        return lf.qa_answers_to_custom({qid: {"answer": answer, "counter": "", "note": ""}})

    def test_small_size_skipped(self):
        self.assertEqual(self.m("max_ticket", "45"), ({}, [("max_ticket", "45")]))
        self.assertEqual(self.m("min_ticket", "249,999"), ({}, [("min_ticket", "249,999")]))

    def test_ok_size_written(self):
        self.assertEqual(self.m("min_ticket", "$250,000")[0], {lf.MIN_SIZE_FIELD: 250000.0})

    def test_shares_avail_unchanged(self):
        self.assertEqual(self.m("shares_avail", "45")[0], {lf.SHARE_COUNT_FIELD: 45.0})


class FormRenderTests(unittest.TestCase):
    def test_labels_and_hint(self):
        body = lf.render_form(_deal(), {}, "?u", [], "")["body"]
        for s in ("Minimum Transaction Size (total $)", "Maximum Transaction Size (total $)",
                  'placeholder="e.g. 1000000"', "Total dollar amount of the transaction, not the price per share. Minimum $250,000.",
                  'id="min_size_hint"', 'id="max_size_hint"', "This looks like a price per share. Enter the total dollar amount.",
                  "Our minimum transaction size is $250,000.", 'placeholder="e.g. 250000"'):
            self.assertIn(s, body)


if __name__ == "__main__":
    unittest.main()
