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
        self.assertEqual(self.m("est_valuation", "150"), ({}, [("est_valuation", "150")]))

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


if __name__ == "__main__":
    unittest.main()
