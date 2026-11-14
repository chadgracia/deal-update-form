import io
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("HMAC_SECRET", "test-hmac-key")  # fake key; the code has no fallback
import lambda_function as lf

V = lf.VISIBILITY_FIELD
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


class NormalizeTests(unittest.TestCase):
    def test_values(self):
        for raw, want in ((None, lf.VIS_ALL), ("", lf.VIS_ALL), ([], lf.VIS_ALL), ("junk", lf.VIS_ALL),
                          (123, lf.VIS_ALL), (True, lf.VIS_ALL), ([1, 2], lf.VIS_ALL),
                          (7223457, lf.VIS_INTERESTED), ("7223458", lf.VIS_PRIVATE),
                          ("7223458.0", lf.VIS_PRIVATE), ([7223457], lf.VIS_INTERESTED),
                          (["7223456"], lf.VIS_ALL)):
            self.assertEqual(lf.normalize_visibility(raw), want, raw)


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class OverrideWriteTests(unittest.TestCase):
    def _run(self, existing=None, get_exc=None, put_exc=None):
        s3 = mock.Mock()
        if get_exc:
            s3.get_object.side_effect = get_exc
        else:
            s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(existing).encode())}
        s3.put_object.side_effect = put_exc
        with mock.patch.object(lf.boto3, "client", return_value=s3), \
             mock.patch.object(lf, "_utcnow", return_value=NOW):
            lf.write_visibility_override(55, lf.VIS_PRIVATE)
        return s3

    def test_merges_and_drops_old(self):
        fresh = (NOW - timedelta(hours=2)).isoformat()
        old = (NOW - timedelta(hours=4)).isoformat()
        s3 = self._run({"1": {"v": lf.VIS_INTERESTED, "at": fresh}, "2": {"v": lf.VIS_ALL, "at": old},
                        "3": {"v": 1, "at": "bad"}})
        kw = s3.put_object.call_args.kwargs
        self.assertEqual((kw["Bucket"], kw["Key"]), ("full-pipeline-cache", "visibility-overrides.json"))
        self.assertEqual(json.loads(kw["Body"]), {"1": {"v": lf.VIS_INTERESTED, "at": fresh},
                                                  "55": {"v": lf.VIS_PRIVATE, "at": NOW.isoformat()}})

    def test_missing_file_starts_empty(self):
        s3 = self._run(get_exc=_NoSuchKey())
        self.assertEqual(list(json.loads(s3.put_object.call_args.kwargs["Body"])), ["55"])

    def test_read_denied_writes_nothing_and_logs_once(self):
        with mock.patch("builtins.print") as p:
            s3 = self._run(get_exc=Exception("AccessDenied"))
        s3.put_object.assert_not_called()
        self.assertEqual(p.call_count, 1)
        self.assertTrue(p.call_args.args[0].startswith("visibility-overrides:"))

    def test_put_failure_never_raises(self):
        with mock.patch("builtins.print") as p:
            self._run({}, put_exc=Exception("AccessDenied"))
        self.assertEqual(p.call_count, 1)


def _deal(sell=True, vis=None):
    cf = {lf.DEAL_TYPE_FIELD: [lf.SELL_TYPE_ID if sell else lf.BUY_TYPE_ID],
          lf.STRUCTURE_FIELD: [lf.DIRECT_STRUCTURE_ID]}
    if vis is not None:
        cf[V] = vis
    return {"id": 9, "name": "Kraken: Sell", "company": {"id": 3, "name": "Kraken"},
            "primary_contact": {"id": 4, "full_name": "Pat Doe"}, "deal_stage": {"name": "Inquiry"},
            "custom_fields": cf}


class RenderTests(unittest.TestCase):
    def _form(self, deal, posted=None):
        return lf.render_form(deal, {}, "?u", [], "", posted=posted)["body"]

    def _checked(self, body):
        import re
        return [int(m) for m in re.findall(r'name="visibility" value="(\d+)" checked', body)]

    def test_sell_form_prefill(self):
        for vis, want in ((None, lf.VIS_ALL), ("", lf.VIS_ALL), ([lf.VIS_INTERESTED], lf.VIS_INTERESTED),
                          (lf.VIS_PRIVATE, lf.VIS_PRIVATE), (lf.VIS_ALL, lf.VIS_ALL)):
            body = self._form(_deal(vis=vis))
            self.assertIn("Who can see this deal?", body)
            self.assertIn("Only investors already interested in Kraken", body)
            self.assertIn("Not shown or sent to anyone.", body)
            self.assertEqual(self._checked(body), [want], vis)

    def test_rerender_keeps_posted_choice(self):
        body = self._form(_deal(vis=lf.VIS_ALL), posted={"visibility": str(lf.VIS_PRIVATE)})
        self.assertEqual(self._checked(body), [lf.VIS_PRIVATE])

    def test_buy_form_has_no_control(self):
        self.assertNotIn('name="visibility"', self._form(_deal(sell=False)))

    def test_new_order_page(self):
        with mock.patch.object(lf, "load_new_order_companies", return_value={3: "Kraken"}):
            body = lf.render_new_order_page(4, "Pat", "tok")["body"]
        self.assertIn("Who can see this deal?", body)
        self.assertIn('<span id="vis-co">this company</span>', body)
        self.assertEqual(self._checked(body), [lf.VIS_ALL])
        self.assertIn("Alerted only to buyers already following this company.", body)


class UpdateSaveTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.deal = _deal()
        for name, val in (("get_jwt", "jwt"), ("publish_fresh_deal", None), ("confirmed_today", "2026-11-14"),
                          ("seller_landing_url", None)):
            p = mock.patch.object(lf, name, return_value=val)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "send_email")
        self.email = p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "write_visibility_override")
        self.ovr = p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "call_pipeline_api", side_effect=self._api)
        p.start(); self.addCleanup(p.stop)

    def _api(self, method, endpoint, payload=None, jwt=None):
        self.calls.append((method, endpoint, payload))
        if method == "GET" and endpoint.startswith("/deals/"):
            return {"status": 200, "data": self.deal}
        return {"status": 200, "data": {"id": 9}}

    def _save(self, extra=""):
        body = "deal_id=9&submit_action=confirm&net=10&share_count=100&share_class=5077831&min_size=1&max_size=2" + extra
        return lf.handle_post(body, {"token": lf.make_deal_token(9)})

    def _put_cf(self):
        puts = [c for c in self.calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 1)
        return puts[0][2]["deal"]["custom_fields"]

    def test_each_value_written_in_same_put(self):
        for posted, want in ((lf.VIS_ALL, lf.VIS_ALL), (lf.VIS_INTERESTED, lf.VIS_INTERESTED),
                             (lf.VIS_PRIVATE, lf.VIS_PRIVATE), ("", lf.VIS_ALL), ("999", lf.VIS_ALL)):
            self.calls.clear(); self.ovr.reset_mock(); self.email.reset_mock()
            self._save(f"&visibility={posted}")
            cf = self._put_cf()
            self.assertEqual(cf[V], want)
            self.assertEqual(cf[lf.LAST_CONFIRMED_FIELD], "2026-11-14")
            self.ovr.assert_called_once_with(9, want)
            text = self.email.call_args.args[2]
            if want == lf.VIS_ALL:
                self.assertNotIn("Visibility:", text)
            else:
                self.assertIn(f"Visibility: {lf.VIS_LABELS[want]}", text)

    def test_missing_field_leaves_untouched(self):
        self._save()
        self.assertNotIn(V, self._put_cf())
        self.ovr.assert_not_called()

    def test_buy_deal_ignores_field(self):
        self.deal = _deal(sell=False)
        body = "deal_id=9&submit_action=confirm&gross=10&share_count=100&share_class=5077831&min_size=1&max_size=2&visibility=7223458"
        lf.handle_post(body, {"token": lf.make_deal_token(9)})
        self.assertNotIn(V, self._put_cf())
        self.ovr.assert_not_called()

    def test_failed_put_writes_no_override(self):
        self._api_orig = self._api
        def api(method, endpoint, payload=None, jwt=None):
            r = self._api_orig(method, endpoint, payload, jwt)
            return {"status": 500, "data": "err"} if method == "PUT" else r
        with mock.patch.object(lf, "call_pipeline_api", side_effect=api):
            self._save("&visibility=7223458")
        self.ovr.assert_not_called()

    def test_hold_ignores_visibility(self):
        lf.handle_post("deal_id=9&submit_action=hold&visibility=7223458", {"token": lf.make_deal_token(9)})
        self.assertEqual(self.calls[0][2], {"deal": {"deal_stage_id": 2094373,
                                                     "custom_fields": {lf.LAST_CONFIRMED_FIELD: "2026-11-14"}}})
        self.ovr.assert_not_called()


class NewOrderCreateTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        for name, val in (("get_jwt", "jwt"), ("confirmed_today", "2026-11-14"), ("new_order_lock_age", None),
                          ("open_orders_for", []), ("verify_new_token", True)):
            p = mock.patch.object(lf, name, return_value=val)
            p.start(); self.addCleanup(p.stop)
        for name in ("send_email", "publish_fresh_deal", "write_visibility_override"):
            p = mock.patch.object(lf, name)
            setattr(self, name, p.start()); self.addCleanup(p.stop)
        p = mock.patch.object(lf.boto3, "client", return_value=mock.Mock())
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(lf, "call_pipeline_api", side_effect=self._api)
        p.start(); self.addCleanup(p.stop)

    def _api(self, method, endpoint, payload=None, jwt=None):
        self.calls.append((method, endpoint, payload))
        if endpoint.startswith("/people/"):
            return {"status": 200, "data": {"id": 4, "first_name": "Pat", "email": "p@example.com"}}
        if endpoint.startswith("/companies/"):
            return {"status": 200, "data": {"id": 3, "name": "Kraken"}}
        return {"status": 201, "data": {"id": 77}}

    def _create(self, side="sell", vis=None):
        params = {"submit_action": "create", "new_person_id": "4", "new_token": "t", "side": side,
                  "structure": next(iter(lf.NEW_ORDER_STRUCTURES[side])), f"attest_{side}": "1", "company_id": "3"}
        if vis is not None:
            params["visibility"] = vis
        r = lf.handle_new_order_create(params)
        self.assertEqual(r["statusCode"], 302)
        post = [c for c in self.calls if c[0] == "POST"]
        self.assertEqual(len(post), 1)
        return post[0][2]["deal"]["custom_fields"]

    def test_sell_values(self):
        for posted, want in ((None, lf.VIS_ALL), ("", lf.VIS_ALL), ("7223457", lf.VIS_INTERESTED),
                             ("7223458", lf.VIS_PRIVATE), ("7223456", lf.VIS_ALL), ("x", lf.VIS_ALL)):
            self.calls.clear()
            for m in (self.send_email, self.publish_fresh_deal, self.write_visibility_override):
                m.reset_mock()
            cf = self._create(vis=posted)
            self.assertEqual(cf[V], want)
            self.assertEqual(cf[lf.NEXUS_FIELD], [lf.NEXUS_DIRECT_ID])
            self.assertEqual(cf[lf.LAST_CONFIRMED_FIELD], "2026-11-14")
            self.publish_fresh_deal.assert_called_once_with(77, "jwt")
            self.write_visibility_override.assert_called_once_with(77, want)
            text = self.send_email.call_args.args[2]
            if want == lf.VIS_ALL:
                self.assertNotIn("Visibility:", text)
            else:
                self.assertIn(f"Visibility: {lf.VIS_LABELS[want]}", text)
                self.assertIn(f"Visibility</td>", self.send_email.call_args.kwargs["html"])

    def test_buy_untouched(self):
        cf = self._create(side="buy", vis="7223458")
        self.assertNotIn(V, cf)
        self.write_visibility_override.assert_not_called()
        self.assertNotIn("Visibility:", self.send_email.call_args.args[2])

    def test_repost_forms_carry_visibility(self):
        html_out = lf._new_order_repost_form({"visibility": "7223458", "side": "sell"}, "sell", {}, "Go")
        self.assertIn('name="visibility" value="7223458"', html_out)


if __name__ == "__main__":
    unittest.main()
