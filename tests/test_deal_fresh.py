import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf


class PublishFreshDealTests(unittest.TestCase):
    def _run(self, api_result, put_side_effect=None):
        s3 = mock.Mock()
        s3.put_object.side_effect = put_side_effect
        with mock.patch.object(lf, "call_pipeline_api", return_value=api_result) as api, \
             mock.patch.object(lf.boto3, "client", return_value=s3):
            lf.publish_fresh_deal(55674185, "jwt")
        return api, s3

    def test_writes_overlay_with_deal_exactly_as_returned(self):
        deal = {"id": 55674185, "name": "Kraken: $2M - $10M Sell", "custom_fields": {"custom_label_3064339": 30.0}}
        api, s3 = self._run({"status": 200, "data": deal})
        api.assert_called_once_with("GET", "/deals/55674185.json", jwt="jwt")
        kw = s3.put_object.call_args.kwargs
        self.assertEqual((kw["Bucket"], kw["Key"], kw["ContentType"]),
                         ("full-pipeline-cache", "deal-fresh/55674185.json", "application/json"))
        body = json.loads(kw["Body"])
        self.assertEqual(body["deal"], deal)
        self.assertTrue(body["fetched_at"].endswith("+00:00"))

    def test_failed_get_writes_nothing(self):
        _, s3 = self._run({"status": 404, "data": "Not found"})
        s3.put_object.assert_not_called()
        _, s3 = self._run({"status": 200, "data": {"name": "no id"}})
        s3.put_object.assert_not_called()

    def test_s3_error_never_raises(self):
        with mock.patch("builtins.print") as p:
            self._run({"status": 200, "data": {"id": 1}}, put_side_effect=Exception("AccessDenied"))
        self.assertTrue(p.call_args.args[0].startswith("deal-fresh:"))
        self.assertIn("AccessDenied", p.call_args.args[0])

    def test_pipeline_exception_never_raises(self):
        with mock.patch.object(lf, "call_pipeline_api", side_effect=RuntimeError("boom")):
            lf.publish_fresh_deal(1, "jwt")


if __name__ == "__main__":
    unittest.main()
