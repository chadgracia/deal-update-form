import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf


class QaSpvTests(unittest.TestCase):
    def test_est_valuation_gp_alloc(self):
        self.assertEqual(lf.qa_question_text("est_valuation", True),
                         "What is the round valuation (pre-money)?")

    def test_est_valuation_not_gp(self):
        self.assertEqual(lf.qa_question_text("est_valuation", False),
                         lf.QA_TEXT["est_valuation"])

    def test_max_ticket(self):
        self.assertEqual(lf.qa_question_text("max_ticket"), lf.QA_TEXT["max_ticket"])
        self.assertEqual(lf.qa_question_text("max_ticket", True), lf.QA_TEXT["max_ticket"])

    def test_existing_unchanged(self):
        self.assertEqual(lf.qa_question_text("shares_avail"), "How many shares are available to buy?")
        self.assertEqual(lf.QA_ANSWER["shares_avail"], {"type": "number"})

    def test_dollars_formatter(self):
        self.assertEqual(lf.fmt_dollars_answer("2000000"), "$2,000,000")
        self.assertEqual(lf.fmt_dollars_answer("abc"), "abc")

    def test_record_gp_alloc_stored(self):
        self.assertTrue(lf._record_gp_alloc({"gp_allocation": True}, 1))
        self.assertFalse(lf._record_gp_alloc({"gp_allocation": False}, 1))

    def test_record_gp_alloc_error_false(self):
        orig = lf.get_jwt
        lf.get_jwt = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            self.assertFalse(lf._record_gp_alloc({}, 1))
        finally:
            lf.get_jwt = orig


class QaFieldMappingTests(unittest.TestCase):
    def m(self, qid, answer):
        return lf.qa_answers_to_custom({qid: {"answer": answer, "counter": "", "note": ""}})

    def test_max_ticket(self):
        self.assertEqual(self.m("max_ticket", "2,000,000")[0], {lf.MAX_SIZE_FIELD: 2000000.0})

    def test_est_valuation_billions(self):
        self.assertEqual(self.m("est_valuation", "2.5B")[0], {lf.EST_VAL_FIELD: 2.5})
        self.assertEqual(self.m("est_valuation", "150M")[0], {lf.EST_VAL_FIELD: 0.15})

    def test_est_valuation_too_small_skipped(self):
        self.assertEqual(self.m("est_valuation", "5000"), ({}, [("est_valuation", "5000")]))

    def test_class_both(self):
        self.assertEqual(self.m("class", "Both")[0], {lf.SHARE_CLASS_FIELD: 5077912})

    def test_fund_exemption_dont_know(self):
        self.assertEqual(self.m("fund_exemption", "Don't know"), ({}, []))

    def test_blank_and_unparseable(self):
        self.assertEqual(self.m("max_ticket", ""), ({}, []))
        self.assertEqual(self.m("max_ticket", "abc"), ({}, [("max_ticket", "abc")]))
        self.assertEqual(self.m("seller_fee", "abc")[0], {})

    def test_fee_structure_never_written(self):
        for a in ("Accept", "Terms non-negotiable — original terms stand", ""):
            self.assertEqual(self.m("fee_structure", a), ({}, []))
        self.assertEqual(self.m("accept_bid", "Accept"), ({}, []))


class ParseQaValuationTests(unittest.TestCase):
    def test_cases(self):
        cases = {"2.5": 2.5, "0.15": 0.15, "$2.5B": 2.5, "150M": 0.15, "150,000,000": 0.15,
                 "5000": None, "0.0005": None, "abc": None, "": None}
        for raw, want in cases.items():
            got = lf.parse_qa_valuation_billions(raw)
            if want is None:
                self.assertIsNone(got, raw)
            else:
                self.assertAlmostEqual(got, want, msg=raw)


class QaAnswerSubmitTests(unittest.TestCase):
    def run_submit(self, note_status=200):
        rec = {"status": "pending", "deal_name": "Acme SPV", "buyer_email": "b@x.com",
               "buyer_name": "Bob B", "seller_email": "s@x.com", "gp_allocation": False,
               "question_ids": ["max_ticket", "est_valuation", "class", "fee_structure"]}
        self.stored = {}
        test = self

        class S3:
            def get_object(self, **k):
                return {"Body": io.BytesIO(json.dumps(rec).encode())}

            def put_object(self, **k):
                test.stored = json.loads(k["Body"])

        self.calls, self.emails = [], []

        def api(method, endpoint, payload=None, jwt=None):
            self.calls.append((method, endpoint, payload))
            if method == "GET":
                return {"status": 200, "data": {"summary": "x", "custom_fields": {}}}
            if endpoint == "/notes.json":
                return {"status": note_status, "data": {}}
            return {"status": 200, "data": {}}

        orig = (lf.boto3.client, lf.verify_token, lf.get_jwt, lf.call_pipeline_api, lf.send_email)
        lf.boto3.client = lambda *a, **k: S3()
        lf.verify_token = lambda *a: True
        lf.get_jwt = lambda: "j"
        lf.call_pipeline_api = api
        lf.send_email = lambda to, subj, body, html=None: self.emails.append((to, body, html))
        try:
            lf.handle_qa_answer_submit({"deal_id": "9", "set": "s", "token": "t",
                                        "a_max_ticket": "2000000", "a_est_valuation": "2.5",
                                        "a_class": "Both", "a_fee_structure": "Accept"})
        finally:
            (lf.boto3.client, lf.verify_token, lf.get_jwt, lf.call_pipeline_api, lf.send_email) = orig

    def test_no_summary_one_note(self):
        self.run_submit()
        puts = [p for m, e, p in self.calls if m == "PUT"]
        self.assertTrue(puts)
        self.assertFalse(any("summary" in p["deal"] for p in puts))
        notes = [p for m, e, p in self.calls if m == "POST" and e == "/notes.json"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["note"]["deal_id"], 9)
        self.assertTrue(notes[0]["note"]["content"].startswith("Client updated ("))
        self.assertEqual(puts[0]["deal"]["custom_fields"][lf.EST_VAL_FIELD], 2.5)
        self.assertEqual(self.stored["answers"]["est_valuation"]["answer"], "$2.5B")
        self.assertEqual(len(self.emails), 2)
        self.assertFalse(any("Pipeline note NOT saved" in b for _, b, _ in self.emails))

    def test_note_failure_warns_chad(self):
        self.run_submit(note_status=500)
        self.assertEqual(len(self.emails), 2)
        buyer = [b for t, b, _ in self.emails if t == "b@x.com"][0]
        chad = [(b, h) for t, b, h in self.emails if t != "b@x.com"][0]
        self.assertNotIn("Pipeline note NOT saved", buyer)
        self.assertTrue(chad[0].startswith("Pipeline note NOT saved — Q&A answers are only in this email."))
        self.assertIn("Pipeline note NOT saved", chad[1])


class QaDisplayAnswerTests(unittest.TestCase):
    def test_cases(self):
        cases = [(("est_valuation", "5"), "$5B"), (("seller_fee", "3"), "3%"),
                 (("seller_fee", "3%"), "3%"), (("deadline", "2026-10-13"), "Oct 13, 2026"),
                 (("deadline", "soon"), "soon"), (("max_ticket", "500000"), "$500,000"),
                 (("shares_avail", "12500"), "12,500 shares"), (("data_room_avail", "Yes"), "Yes")]
        for args, want in cases:
            self.assertEqual(lf.qa_display_answer(*args), want, args)


DISPLAY_SUBMIT_PARAMS = {
    "a_max_ticket": "2000000", "a_est_valuation": "2.5", "a_class": "Both",
    "a_seller_fee": "3%", "a_deadline": "2026-10-13", "a_shares_avail": "12500",
    "a_accept_bid": "Decline", "c_accept_bid": "12", "m_accept_bid": "500000",
}
DISPLAY_SUBMIT_PUT = {"deal": {"custom_fields": {
    lf.MAX_SIZE_FIELD: 2000000.0, lf.EST_VAL_FIELD: 2.5, lf.SHARE_CLASS_FIELD: 5077912,
    lf.SELLER_FEE_FIELD: 3.0, lf.DEADLINE_FIELD: "2026-10-13", lf.SHARE_COUNT_FIELD: 12500.0,
}}}


class QaDisplaySubmitTests(unittest.TestCase):
    def test_note_format_and_put_unchanged(self):
        rec = {"status": "pending", "deal_name": "Acme", "buyer_email": "b@x.com",
               "buyer_name": "Bob", "seller_email": "s@x.com", "gp_allocation": False,
               "question_ids": ["max_ticket", "est_valuation", "class", "seller_fee",
                                "deadline", "shares_avail", "accept_bid"]}
        calls, emails = [], []

        class S3:
            def get_object(self, **k):
                return {"Body": io.BytesIO(json.dumps(rec).encode())}

            def put_object(self, **k):
                pass

        def api(method, endpoint, payload=None, jwt=None):
            calls.append((method, endpoint, payload))
            if method == "GET":
                return {"status": 200, "data": {"custom_fields": {}}}
            return {"status": 200, "data": {}}

        orig = (lf.boto3.client, lf.verify_token, lf.get_jwt, lf.call_pipeline_api, lf.send_email)
        lf.boto3.client = lambda *a, **k: S3()
        lf.verify_token = lambda *a: True
        lf.get_jwt = lambda: "j"
        lf.call_pipeline_api = api
        lf.send_email = lambda to, subj, body, html=None: emails.append((to, body))
        try:
            lf.handle_qa_answer_submit(dict(DISPLAY_SUBMIT_PARAMS, deal_id="9", set="s", token="t"))
        finally:
            (lf.boto3.client, lf.verify_token, lf.get_jwt, lf.call_pipeline_api, lf.send_email) = orig
        note = [p for m, e, p in calls if e == "/notes.json"][0]["note"]["content"]
        self.assertIn("? $2,000,000", note)
        self.assertNotIn(" : ", note)
        self.assertIn("minimum size for counter terms $500,000", note)
        for _, body in emails:
            self.assertIn("$500,000", body)
        self.assertEqual([p for m, e, p in calls if m == "PUT"], [DISPLAY_SUBMIT_PUT])


if __name__ == "__main__":
    unittest.main()
