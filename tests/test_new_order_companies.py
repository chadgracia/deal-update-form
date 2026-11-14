import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HMAC_SECRET", "test-hmac-key")  # fake key; the code has no fallback
import lambda_function as lf

DEALS = {"deals": [{"company": {"id": 1, "name": "OpenAI (deal)"}},
                   {"company": {"id": 2, "name": "Stripe"}}]}
COMPANIES = {"companies": [
    {"id": 1, "name": "OpenAI", "custom_fields": {}},
    {"id": 10, "name": "Skild AI", "custom_fields": {}},
    {"id": 11, "name": "  Figure  AI ", "custom_fields": {}},
    {"id": 12, "name": "Cerebras", "custom_fields": {"custom_label_3790429": 8100000000}},
    {"id": 13, "name": "Sequoia Capital", "custom_fields": {"custom_label_3999575": None,
                                                           "custom_label_3999603": "",
                                                           "custom_label_3064363": 0,
                                                           "custom_label_3826032": []}},
    {"id": 14, "name": "Restricted Co", "custom_fields": {"custom_label_3999603": "IPO"}},
]}
RESTRICTED = {"companies": [{"id": 14, "name": "Restricted Co"}]}
LABELS = {
    "/admin/person_custom_field_labels/3322093.json":
        {"status": 200, "data": {"custom_field_label_dropdown_entries": [{"name": "skild ai"}, {"name": "Nonexistent Inc"}]}},
    "/admin/person_custom_field_labels/3759156.json":
        {"status": 200, "data": {"entry": {"custom_field_label_dropdown_entries": [{"name": "Figure AI"}]}}},
}


class NewOrderCompaniesTests(unittest.TestCase):
    def setUp(self):
        lf._new_order_cache.update(at=0.0, companies=None)

    def _run(self, objects, api=None):
        def get_object(Bucket, Key):
            self.assertEqual(Bucket, "full-pipeline-cache")
            v = objects.get(Key)
            if v is None or isinstance(v, Exception):
                raise v or Exception(f"NoSuchKey {Key}")
            return {"Body": io.BytesIO(json.dumps(v).encode())}
        s3 = mock.Mock()
        s3.get_object.side_effect = get_object
        api = api or (lambda method, endpoint, payload=None, jwt=None: LABELS[endpoint])
        with mock.patch.object(lf.boto3, "client", return_value=s3), \
             mock.patch.object(lf, "get_jwt", return_value="jwt"), \
             mock.patch.object(lf, "call_pipeline_api", side_effect=api) as call:
            with self.assertLogs(lf.logger, level="INFO") as logs:
                lf.logger.info("start")
                result = lf.load_new_order_companies()
        self.calls, self.logs, self.s3 = call, logs.output, s3
        return result

    def _all(self):
        return {"deals.json": DEALS, "companies.json": COMPANIES, "restricted-issuers.json": RESTRICTED}

    def test_union_of_sources(self):
        r = self._run(self._all())
        self.assertEqual(r, {1: "OpenAI", 2: "Stripe", 10: "Skild AI", 11: "Figure  AI", 12: "Cerebras"})

    def test_buy_interest_option_matched(self):
        self.assertEqual(self._run(self._all())[10], "Skild AI")

    def test_sell_interest_option_matched_whitespace_case_insensitive(self):
        self.assertIn(11, self._run(self._all()))

    def test_unmatched_option_skipped_and_counted(self):
        r = self._run(self._all())
        self.assertNotIn("Nonexistent Inc", r.values())
        self.assertTrue(any("new-order: 1 interest option" in l for l in self.logs))

    def test_issuer_field_company_included(self):
        self.assertEqual(self._run(self._all())[12], "Cerebras")

    def test_investor_firm_excluded(self):
        self.assertNotIn(13, self._run(self._all()))

    def test_restricted_excluded(self):
        self.assertNotIn(14, self._run(self._all()))

    def test_missing_restricted_file_skips_filter(self):
        objs = self._all(); del objs["restricted-issuers.json"]
        r = self._run(objs)
        self.assertIn(14, r)
        self.assertTrue(any("new-order:" in l and "restricted" in l for l in self.logs))

    def test_label_failure_keeps_deals_and_issuer_fields(self):
        r = self._run(self._all(), api=lambda *a, **k: {"status": 500, "data": "boom"})
        self.assertEqual(r, {1: "OpenAI", 2: "Stripe", 12: "Cerebras"})
        self.assertTrue(any("new-order: failed to load interest label" in l for l in self.logs))

    def test_companies_json_failure_falls_back_to_deals(self):
        objs = self._all(); objs["companies.json"] = Exception("AccessDenied")
        r = self._run(objs)
        self.assertEqual(r, {1: "OpenAI (deal)", 2: "Stripe"})
        self.assertTrue(any("new-order: failed to load companies.json" in l for l in self.logs))

    def test_cached_for_warm_container(self):
        self._run(self._all())
        self.s3.get_object.reset_mock()
        r = self._run({})
        self.assertIn(10, r)
        self.s3.get_object.assert_not_called()
        self.calls.assert_not_called()

    def test_empty_result_not_cached(self):
        self._run({"deals.json": {"deals": []}, "companies.json": {"companies": []}},
                  api=lambda *a, **k: {"status": 200, "data": {}})
        self.assertIsNone(lf._new_order_cache["companies"])

    def test_no_writes(self):
        self._run(self._all())
        self.s3.put_object.assert_not_called()
        for c in self.calls.call_args_list:
            self.assertEqual(c.args[0], "GET")


if __name__ == "__main__":
    unittest.main()
