import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf

F = lf.LAST_CONFIRMED_FIELD
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)


class NoSuchKey(Exception):
    pass


class FakeS3:
    """Minimal in-memory S3 for put_object / get_object / delete_object."""

    class exceptions:
        ClientError = type("ClientError", (Exception,), {})

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.puts = []

    def put_object(self, Bucket, Key, Body=b"", **kw):
        self.puts.append(Key)
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise NoSuchKey(f"NoSuchKey: {Key}")
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class Base(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.put_status = 200
        self.s3 = FakeS3()
        for name, val in (("get_jwt", "jwt"), ("publish_fresh_deal", None),
                          ("confirmed_today", "2026-11-06"), ("_utcnow", NOW)):
            p = mock.patch.object(lf, name, return_value=val)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "send_email")
        self.email = p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "call_pipeline_api", side_effect=self._api)
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf.boto3, "client", return_value=self.s3)
        p.start(); self.addCleanup(p.stop)

    def _api(self, method, endpoint, payload=None, jwt=None):
        self.calls.append((method, endpoint, payload))
        if endpoint.startswith("/people/"):
            return {"status": 200, "data": {"email": "cp@x.com", "first_name": "Cy"}}
        if method == "PUT":
            return {"status": self.put_status, "data": {} if self.put_status == 200 else "err"}
        return {"status": 200, "data": {"id": 9, "name": "Kraken: Sell", "primary_contact": {"id": 5},
                                         "custom_fields": {}}}

    def emails_to(self, addr):
        return [c for c in self.email.call_args_list if c.args[0] == addr]


class CatalogTests(unittest.TestCase):
    def test_text_and_answer_type(self):
        self.assertEqual(lf.QA_TEXT["still_active"], "Is this order still active?")
        self.assertEqual(lf.QA_ANSWER["still_active"],
                         {"type": "choice", "options": ["Yes, still active", "No longer available"]})

    def test_no_pipeline_field_mapping(self):
        self.assertEqual(lf.qa_answers_to_custom({"still_active": {"answer": "Yes, still active"}}), ({}, []))


class AnswerTests(Base):
    def _answer(self, answer, qids=("still_active",)):
        self.s3.objects["9/S1.json"] = json.dumps({
            "deal_id": 9, "deal_name": "Kraken: Sell", "question_ids": list(qids),
            "buyer_email": "asker@x.com", "buyer_name": "Ann Asker", "seller_email": "cp@x.com",
            "status": "pending", "answerer_role": "seller"}).encode()
        params = {"deal_id": "9", "set": "S1", "token": lf.make_token("S1"), "a_still_active": answer}
        return lf.handle_qa_answer_submit(params)

    def test_yes_writes_only_last_confirmed(self):
        self._answer("Yes, still active")
        writes = [c for c in self.calls if c[0] == "PUT"]
        self.assertEqual(writes, [("PUT", "/deals/9.json", {"deal": {"custom_fields": {F: "2026-11-06"}}})])
        asker = self.emails_to("asker@x.com")
        self.assertEqual(len(asker), 1)
        self.assertIn("The counterparty confirmed this order is still active.", asker[0].args[2])
        self.assertNotIn("FAILED", self.emails_to(lf.CHAD_EMAIL)[0].args[2])

    def test_yes_write_failure_does_not_block(self):
        self.put_status = 500
        r = self._answer("Yes, still active")
        self.assertEqual(r["statusCode"], 200)
        self.assertEqual(len(self.emails_to("asker@x.com")), 1)
        self.assertIn("(Last Confirmed write FAILED, HTTP 500)", self.emails_to(lf.CHAD_EMAIL)[0].args[2])

    def test_no_follows_cancel_path(self):
        r = self._answer("No longer available")
        self.assertEqual([c for c in self.calls if c[0] in ("PUT", "POST")], [])
        self.assertIn("no longer available", self.emails_to("asker@x.com")[0].args[1])
        chad = self.emails_to(lf.CHAD_EMAIL)[0]
        self.assertIn("Deal CANCEL via Q&A", chad.args[1])
        self.assertIn("Pipeline stage was NOT changed", chad.args[2])
        self.assertIn("the deal is cancelled", r["body"])
        self.assertEqual(json.loads(self.s3.objects["9/S1.json"])["deal_action"], "cancel")


class ThrottleTests(Base):
    def _submit(self, *qids):
        params = {"deal_id": "9", "buyer_email": "asker@x.com", "buyer_name": "Ann"}
        params.update({f"q_{q}": "1" for q in qids})
        return lf.handle_qa_submit(params)

    def _sent_days_ago(self, days):
        self.s3.objects["still-active/9.json"] = json.dumps(
            {"sent_at": (NOW - timedelta(days=days)).isoformat()}).encode()

    def test_first_request_sends_and_records(self):
        self._submit("still_active")
        self.assertEqual(len(self.emails_to("cp@x.com")), 1)
        self.assertEqual(json.loads(self.s3.objects["still-active/9.json"])["sent_at"], NOW.isoformat())

    def test_repeat_within_7_days_suppressed(self):
        self._sent_days_ago(3)
        r = self._submit("still_active")
        self.assertEqual(self.emails_to("cp@x.com"), [])
        self.assertIn("already asked about this order recently", r["body"])
        chad = self.emails_to(lf.CHAD_EMAIL)
        self.assertEqual(len(chad), 1)
        self.assertEqual(chad[0].args[1], "Kraken: Sell: repeat still-active request suppressed")
        self.assertFalse(any(k.startswith("9/") for k in self.s3.objects))

    def test_after_7_days_sends_again(self):
        self._sent_days_ago(8)
        self._submit("still_active")
        self.assertEqual(len(self.emails_to("cp@x.com")), 1)

    def test_other_questions_still_send(self):
        self._sent_days_ago(1)
        r = self._submit("still_active", "min_max")
        cp = self.emails_to("cp@x.com")
        self.assertEqual(len(cp), 1)
        self.assertIn(lf.QA_TEXT["min_max"], cp[0].args[2])
        self.assertNotIn(lf.QA_TEXT["still_active"], cp[0].args[2])
        rec = json.loads(next(v for k, v in self.s3.objects.items() if k.startswith("9/")))
        self.assertEqual(rec["question_ids"], ["min_max"])
        self.assertIn("already asked about this order recently", r["body"])
        self.assertTrue(any(c.args[1] == "Kraken: Sell: repeat still-active request suppressed"
                            for c in self.emails_to(lf.CHAD_EMAIL)))
        # throttle timestamp not refreshed by a suppressed request
        self.assertEqual(json.loads(self.s3.objects["still-active/9.json"])["sent_at"],
                         (NOW - timedelta(days=1)).isoformat())


if __name__ == "__main__":
    unittest.main()
