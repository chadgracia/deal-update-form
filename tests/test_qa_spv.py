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


if __name__ == "__main__":
    unittest.main()
