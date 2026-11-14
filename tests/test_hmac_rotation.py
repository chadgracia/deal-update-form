"""HMAC_SECRET rotation: current key, legacy (HMAC_SECRET_OLD) key, empty key.
Fake keys only; no AWS, Pipeline or email."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HMAC_SECRET", "test-hmac-key")  # fake key; the code has no fallback
import lambda_function as lf

NEW, OLD, FORM = "test-new-hmac", "test-old-hmac", "test-form-hmac"


def body(resp):
    return resp.get("body") or ""


class Rotation(unittest.TestCase):
    def setUp(self):
        for name, val in (("HMAC_SECRET", NEW), ("HMAC_SECRET_OLD", OLD), ("FORM_HMAC_SECRET", FORM)):
            p = mock.patch.object(lf, name, val); p.start(); self.addCleanup(p.stop)
        self.email = mock.patch.object(lf, "send_email").start(); self.addCleanup(mock.patch.stopall)
        self.api = mock.patch.object(lf, "call_pipeline_api",
                                     side_effect=AssertionError("no Pipeline call expected")).start()
        mock.patch.object(lf, "get_jwt", return_value="jwt").start()
        mock.patch.object(lf.boto3, "client", side_effect=AssertionError("no AWS in tests")).start()

    # --- signing ---------------------------------------------------------
    def test_signing_uses_current_key_only(self):
        self.assertEqual(lf.make_token(7), lf._sign(NEW, 7))
        self.assertNotEqual(lf.make_token(7), lf._sign(OLD, 7))

    # --- current key -----------------------------------------------------
    def test_current_token_accepted_everywhere(self):
        self.assertTrue(lf.verify_token(7, lf._sign(NEW, 7)))
        self.assertTrue(lf.verify_deal_token(9, lf._sign(NEW, 9)))
        self.assertTrue(lf.verify_deal_token(9, lf._sign(FORM, 9)))
        self.assertTrue(lf.verify_new_token(7, lf._sign(NEW, "new:7")))
        self.assertFalse(lf.is_legacy_token(7, lf._sign(NEW, 7)))

    def test_unsubscribe_current_token(self):
        r = lf.handle_get({"action": "unsubscribe", "person_id": "7", "token": lf._sign(NEW, 7)})
        self.assertEqual(r["statusCode"], 200)
        self.email.assert_called_once()

    # --- legacy key: allowed ---------------------------------------------
    def test_unsubscribe_accepts_legacy_token(self):
        r = lf.handle_get({"action": "unsubscribe", "person_id": "7", "token": lf._sign(OLD, 7)})
        self.assertEqual(r["statusCode"], 200)
        self.email.assert_called_once()   # the internal unsubscribe notice to the agent inbox

    # --- legacy key: refused ---------------------------------------------
    def test_legacy_deal_link_refused_with_expired_text(self):
        r = lf.handle_get({"deal_id": "9", "token": lf._sign(OLD, 9)})
        self.assertEqual(r["statusCode"], 400)
        self.assertIn(lf.LINK_EXPIRED_MSG, body(r))
        self.api.assert_not_called()

    def test_legacy_deal_write_refused(self):
        tok = lf._sign(OLD, 9)
        for action in ("hold", "confirm", "still_active", "cancel"):
            r = lf.handle_post(f"deal_id=9&submit_action={action}&token={tok}")
            self.assertEqual(r["statusCode"], 400, action)
            self.assertIn(lf.LINK_EXPIRED_MSG, body(r), action)
        self.api.assert_not_called()
        self.email.assert_not_called()

    def test_legacy_new_order_refused(self):
        r = lf.handle_get({"action": "new", "person_id": "7", "token": lf._sign(OLD, "new:7")})
        self.assertIn(lf.LINK_EXPIRED_MSG, body(r))
        self.assertFalse(lf.verify_new_token(7, lf._sign(OLD, "new:7")))
        self.api.assert_not_called()

    def test_legacy_qa_answer_refused(self):
        tok = lf._sign(OLD, "set1")
        r = lf.handle_qa_answer_page({"deal_id": "9", "set": "set1", "token": tok})
        self.assertIn(lf.LINK_EXPIRED_MSG, body(r))
        r = lf.handle_qa_answer_submit({"deal_id": "9", "set": "set1", "token": tok})
        self.assertIn(lf.LINK_EXPIRED_MSG, body(r))
        self.email.assert_not_called()

    def test_garbage_token_keeps_old_message(self):
        r = lf.handle_get({"deal_id": "9", "token": "nope"})
        self.assertIn("Invalid or expired link.", body(r))
        self.assertNotIn(lf.LINK_EXPIRED_MSG, body(r))

    def test_no_old_key_means_no_legacy(self):
        with mock.patch.object(lf, "HMAC_SECRET_OLD", ""):
            self.assertFalse(lf.is_legacy_token(7, lf._sign(OLD, 7)))
            r = lf.handle_get({"action": "unsubscribe", "person_id": "7", "token": lf._sign(OLD, 7)})
            self.assertEqual(r["statusCode"], 400)

    # --- empty HMAC_SECRET -------------------------------------------------
    def test_empty_key_fails_closed(self):
        with mock.patch.object(lf, "HMAC_SECRET", ""), mock.patch.object(lf, "FORM_HMAC_SECRET", ""):
            self.assertEqual(lf.make_token(7), "")
            self.assertEqual(lf.make_deal_token(9), "")
            self.assertEqual(lf.make_new_token(7), "")
            for tok in ("", lf._sign("", 7), lf._sign(OLD, 7), lf._sign(NEW, 7)):
                self.assertFalse(lf.verify_token(7, tok))
                self.assertFalse(lf.is_legacy_token(7, tok))
            self.assertFalse(lf.verify_deal_token(9, lf._sign("", 9)))
            r = lf.handle_get({"action": "unsubscribe", "person_id": "7", "token": lf._sign(OLD, 7)})
            self.assertEqual(r["statusCode"], 400)
        self.email.assert_not_called()


if __name__ == "__main__":
    unittest.main()
