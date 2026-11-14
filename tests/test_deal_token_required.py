"""Hold and the default confirm/update save need the signed deal token."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HMAC_SECRET", "test-hmac-key")  # fake key; the code has no fallback
import lambda_function as lf

DEAL = {"id": 9, "name": "Kraken: Sell", "custom_fields": {}, "company": {"id": 5, "name": "Kraken"},
        "primary_contact": {"id": 7, "full_name": "Client"}, "deal_stage": {"name": "Inquiry"}}
CONFIRM = "deal_id=9&submit_action=confirm&net_price=30&min_size=100000&max_size=500000&comments=hi"


class DealTokenRequired(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for name, val in (("get_jwt", "jwt"), ("publish_fresh_deal", None),
                          ("confirmed_today", "2026-11-03"), ("load_all_deals", [])):
            p = mock.patch.object(lf, name, return_value=val)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "send_email")
        self.email = p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "call_pipeline_api", side_effect=self._api)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf.boto3, "client", side_effect=AssertionError("no AWS in tests"))
        p.start(); self.addCleanup(p.stop)

    def _api(self, method, endpoint, payload=None, jwt=None):
        self.calls.append((method, endpoint, payload))
        if endpoint.startswith("/companies/"):
            return {"status": 200, "data": {"id": 5, "name": "Kraken"}}
        return {"status": 200, "data": dict(DEAL)}

    def puts(self):
        return [c for c in self.calls if c[0] != "GET"]

    def post(self, body, token=None, via_query=False):
        qs = {}
        if token is not None:
            if via_query:
                qs["token"] = token
            else:
                body += "&token=" + token
        return lf.handle_post(body, qs)

    # ── missing / wrong token: nothing written, nothing emailed ──
    def _assert_rejected(self, resp):
        self.assertIn("Invalid or expired link.", resp["body"])
        self.assertEqual(self.calls, [])
        self.email.assert_not_called()

    def test_hold_without_token(self):
        self._assert_rejected(self.post("deal_id=9&submit_action=hold"))

    def test_hold_wrong_token(self):
        self._assert_rejected(self.post("deal_id=9&submit_action=hold", lf.make_deal_token(10)))

    def test_confirm_without_token(self):
        self._assert_rejected(self.post(CONFIRM))

    def test_confirm_wrong_token(self):
        self._assert_rejected(self.post(CONFIRM, "nope"))

    def test_no_submit_action_defaults_to_confirm_and_needs_token(self):
        self._assert_rejected(self.post("deal_id=9&net_price=30"))

    # ── valid token: same behavior as before ──
    def test_hold_valid_token(self):
        resp = self.post("deal_id=9&submit_action=hold", lf.make_deal_token(9))
        self.assertEqual(resp["statusCode"], 200)
        self.assertEqual(self.puts(), [("PUT", "/deals/9.json",
                                        {"deal": {"deal_stage_id": 2094373,
                                                  "custom_fields": {lf.LAST_CONFIRMED_FIELD: "2026-11-03"}}})])
        self.email.assert_called_once()

    def test_hold_valid_token_in_query_string(self):
        self.post("deal_id=9&submit_action=hold", lf.make_deal_token(9), via_query=True)
        self.assertEqual(len(self.puts()), 1)

    def test_old_key_token_still_accepted(self):
        with mock.patch.object(lf, "FORM_HMAC_SECRET", "new-key"):
            self.post("deal_id=9&submit_action=hold", lf._sign(lf.HMAC_SECRET, 9))
        self.assertEqual(len(self.puts()), 1)

    def test_confirm_valid_token_writes(self):
        resp = self.post(CONFIRM, lf.make_deal_token(9))
        self.assertNotIn("Invalid or expired link.", resp["body"])
        puts = self.puts()
        self.assertTrue(puts and puts[0][1] == "/deals/9.json", self.calls)

    def test_form_carries_token_field(self):
        resp = lf.render_form(dict(DEAL), {}, "", [], "")
        self.assertIn(f'<input type="hidden" name="token" value="{lf.make_deal_token(9)}">', resp["body"])


if __name__ == "__main__":
    unittest.main()
