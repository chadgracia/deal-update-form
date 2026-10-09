import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import lambda_function as lf


class FindPeopleByEmailTests(unittest.TestCase):
    def test_person_email_condition_exact_match_only(self):
        calls = []
        exact = {"id": 1310160682, "email": "dheeraj@srdinnovation.com"}
        substring = {"id": 1, "email": "xdheeraj@srdinnovation.com", "email2": "", "home_email": None}

        def fake_api(method, path, jwt=None, **kw):
            calls.append(path)
            return {"status": 200, "data": {"entries": [substring, exact]}}

        orig = lf.call_pipeline_api
        lf.call_pipeline_api = fake_api
        try:
            got = lf.find_people_by_email("Dheeraj@SRDinnovation.com", "jwt")
        finally:
            lf.call_pipeline_api = orig
        self.assertEqual(got, [exact])
        self.assertIn("conditions[person_email]=", calls[0])
        self.assertNotIn("conditions[email]=", calls[0])


if __name__ == "__main__":
    unittest.main()
