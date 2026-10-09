import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf


class AccountSetupPageTests(unittest.TestCase):
    def test_renders_escaped_email_and_signout_link(self):
        resp = lf.account_setup_page(0, "a<b>&c@x.com")
        body = resp["body"]
        self.assertIn("You're signed in as <strong>a&lt;b&gt;&amp;c@x.com</strong>", body)
        self.assertNotIn("a<b>&c@x.com", body)
        self.assertIn('<a href="https://trades.graciagroup.com/?signout=1">sign out</a>', body)
        self.assertIn("wait=1", body)
        self.assertEqual(resp["headers"]["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
