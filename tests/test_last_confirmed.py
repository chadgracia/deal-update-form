import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf

F = lf.LAST_CONFIRMED_FIELD


class LastConfirmedTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.status = {}
        for name, val in (("get_jwt", "jwt"), ("publish_fresh_deal", None),
                          ("confirmed_today", "2026-11-02")):
            p = mock.patch.object(lf, name, return_value=val)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "send_email")
        self.email = p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "call_pipeline_api", side_effect=self._api)
        p.start(); self.addCleanup(p.stop)

    def _api(self, method, endpoint, payload=None, jwt=None):
        self.calls.append((method, endpoint, payload))
        st = self.status.get(len(self.calls), 200)
        return {"status": st, "data": {"id": 9, "name": "Kraken: Sell"} if st == 200 else "err"}

    def _post(self, body, qs=None):
        return lf.handle_post(body, qs or {})

    def test_put_helper_stamps_same_put(self):
        lf.put_deal_confirmed(9, {"deal_stage_id": 1, "custom_fields": {"x": 1}}, "jwt")
        self.assertEqual(self.calls, [("PUT", "/deals/9.json",
                                       {"deal": {"deal_stage_id": 1, "custom_fields": {"x": 1, F: "2026-11-02"}}})])

    def test_put_helper_retries_without_field(self):
        self.status = {1: 422}
        r = lf.put_deal_confirmed(9, {"deal_stage_id": 1}, "jwt")
        self.assertEqual(r["status"], 200)
        self.assertEqual(self.calls[1][2], {"deal": {"deal_stage_id": 1}})

    def test_hold_sets_field(self):
        self._post("deal_id=9&submit_action=hold", {"token": lf.make_deal_token(9)})
        self.assertEqual(self.calls[0][2]["deal"]["custom_fields"], {F: "2026-11-02"})

    def test_cancel_does_not_set_field(self):
        self._post("deal_id=9&submit_action=cancel", {"token": lf.make_deal_token(9)})
        self.assertTrue(all(F not in str(c[2]) for c in self.calls))

    def test_still_active_writes_only_field(self):
        r = self._post("deal_id=9&submit_action=still_active", {"deal_id": "9", "token": lf.make_deal_token(9)})
        self.assertEqual(self.calls[0], ("PUT", "/deals/9.json", {"deal": {"custom_fields": {F: "2026-11-02"}}}))
        self.assertEqual(len([c for c in self.calls if c[0] != "GET"]), 1)
        self.assertIn("confirmed as active", r["body"])
        self.assertEqual(self.email.call_args.args[1], "Kraken: Sell confirmed active by client")

    def test_still_active_bad_token_writes_nothing(self):
        self._post("deal_id=9&submit_action=still_active", {"token": "nope"})
        self.assertEqual(self.calls, [])

    def test_still_active_write_failure_still_confirms(self):
        self.status = {1: 500}
        r = self._post("deal_id=9&submit_action=still_active", {"token": lf.make_deal_token(9)})
        self.assertIn("confirmed as active", r["body"])

    def test_get_never_writes(self):
        with mock.patch.object(lf, "handle_get", return_value={"statusCode": 200}) as g:
            lf.lambda_handler({"requestContext": {"http": {"method": "GET"}},
                               "queryStringParameters": {"deal_id": "9", "token": lf.make_deal_token(9),
                                                         "submit_action": "still_active"}}, None)
        g.assert_called_once()
        self.assertTrue(all(c[0] == "GET" for c in self.calls))

    def test_form_has_button(self):
        src = open(lf.__file__).read()
        self.assertIn('value="still_active" class="btn-secondary" formnovalidate', src)


if __name__ == "__main__":
    unittest.main()
