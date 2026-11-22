import asyncio
import base64
import json
import os
import selectors
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from madadpass.app import create_app
from madadpass.assistance import extract_application, extract_local, inspect_document
from madadpass.seed import DEMO_PASSWORD, document_sample


def uid():
    return str(uuid4())


class InProcessSelector(selectors.SelectSelector):
    def __init__(self):
        super().__init__()
        self.wakeup = Event()

    def select(self, timeout=None):
        self.wakeup.wait(timeout)
        self.wakeup.clear()
        return []


class InProcessLoop(asyncio.SelectorEventLoop):
    """Wake ASGI worker threads without a local socket pair."""

    def __init__(self):
        super().__init__(InProcessSelector())

    def _make_self_pipe(self):
        pass

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        self._selector.wakeup.set()


def run_asgi(coroutine):
    with asyncio.Runner(loop_factory=InProcessLoop) as runner:
        return runner.run(coroutine)


class HttpResult:
    def __init__(self, status, raw):
        self.status = status
        self.raw = raw

    def json(self):
        return json.loads(self.raw)


class LocalASGI:
    """Exercise the real router, middleware and handlers without a TCP socket."""

    def __init__(self, app):
        self.app = app

    async def start(self):
        async with self.app.router.lifespan_context(self.app):
            pass

    def call(self, method, path, body=None, token=None, key=None):
        async def request():
            headers = [(b"host", b"localhost"), (b"content-type", b"application/json")]
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            if key:
                headers.append((b"x-api-key", key.encode()))
            scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "http_version": "1.1",
                     "method": method, "path": path, "raw_path": path.encode(), "headers": headers, "scheme": "http",
                     "query_string": b"", "client": ("local-test", 0), "server": ("localhost", 80)}
            messages = []
            first = True

            async def receive():
                nonlocal first
                if first:
                    first = False
                    return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
                await asyncio.Event().wait()

            async def send(message):
                messages.append(message)

            await self.app(scope, receive, send)
            response = next(item for item in messages if item["type"] == "http.response.start")
            raw = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
            return HttpResult(response["status"], raw)

        return run_asgi(request())


class IntakeTests(unittest.TestCase):
    def test_bilingual_numbers_and_missing_details(self):
        self.assertEqual(extract_local("We have 5 people. Monthly income: PKR 30,000."),
                         {"family_members": 5, "monthly_income": 30000})
        self.assertEqual(extract_local("میرے گھر میں ۵ افراد ہیں۔ ماہانہ آمدنی: ۳۰۰۰۰ روپے۔"),
                         {"family_members": 5, "monthly_income": 30000})
        self.assertEqual(extract_local("Family members: 5. Family members: 8. Need groceries."),
                         {"family_members": None, "monthly_income": None})
        self.assertIsNone(extract_local("Income: 30,00")["monthly_income"])
        self.assertEqual(inspect_document(document_sample(5, 30000), "text/plain", False)["monthly_income"], 30000)

    def test_malformed_provider_json_and_timeout_leave_editable_fallback(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fictional-test-key"}):
            with patch("madadpass.assistance.provider_json", return_value={"candidates": []}) as mock:
                result = extract_application("Household size: 5.", True)
                self.assertEqual(mock.call_count, 2)
            self.assertTrue(result["confirm"])
            self.assertEqual(result["fields"], {"family_members": 5, "monthly_income": None})
            self.assertEqual(result["mode"], "local")
            with patch("madadpass.assistance.urlopen", side_effect=TimeoutError):
                self.assertEqual(extract_application("Income: PKR 30000.", True)["fields"]["monthly_income"], 30000)


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.application = create_app(Path(cls.temp.name) / "test.sqlite3", signing_key="test-fake-signing-key-for-madadpass-only")
        cls.client = LocalASGI(cls.application)
        run_asgi(cls.client.start())
        cls.tokens = {}
        for login in ("citizen", "helper", "officer", "merchant", "admin", "auditor", "officer-cedar"):
            response = cls.client.call("POST", "/api/login", {"login": login, "password": DEMO_PASSWORD})
            cls.tokens[login] = response.json()["token"]

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def call(self, role, method, path, body=None, expected=200):
        result = self.client.call(method, path, body, self.tokens.get(role))
        self.assertEqual(result.status, expected, result.raw[:1500])
        return result

    def test_browser_assets_and_openapi(self):
        self.assertIn(b"MadadPass", self.call(None, "GET", "/").raw)
        self.assertIn(b"DEVELOPER DOCUMENTATION", self.call(None, "GET", "/docs").raw)
        self.assertIn(b"signin", self.call(None, "GET", "/static/style.css").raw)
        spec = self.call(None, "GET", "/openapi.json").json()
        self.assertIn("/api/vouchers/redeem", spec["paths"])
        self.assertIn("MockApiKey", spec["components"]["securitySchemes"])

    def test_consent_withdrawal_blocks_document_and_case_access(self):
        citizen_case = self.call("citizen", "GET", "/api/cases").json()["cases"]
        case = next(row for row in citizen_case if row["applicant"] == "Amina Rahim")
        path = f"/api/cases/{case['id']}"
        record = self.call("helper", "GET", path).json()["case"]
        document = f"{path}/documents/{record['documents'][0]['id']}"
        self.assertIn(b"FICTIONAL", self.call("helper", "GET", document).raw)
        consents = self.call("citizen", "GET", "/api/consents").json()["consents"]
        consent = next(row for row in consents if not row["revoked_at"])
        self.call("citizen", "POST", f"/api/consents/{consent['id']}/revoke")
        self.call("helper", "GET", document, expected=403)
        self.call("helper", "GET", path, expected=403)
        self.call("officer-cedar", "GET", path, expected=404)
        self.assertFalse(self.call("helper", "GET", "/api/cases").json()["cases"])
        events = self.call("auditor", "GET", f"/api/audit/{case['id']}").json()["events"]
        self.assertTrue(any(row["action"] == "access.denied" and row["role"] == "helper" for row in events))

    def test_duplicate_and_missing_input_route_to_people(self):
        cases = self.call("officer", "GET", "/api/cases").json()["cases"]
        duplicates = [case for case in cases if "Ahmed" in case["applicant"] or "Ahmad" in case["applicant"]]
        self.assertEqual(len(duplicates), 2)
        self.assertTrue(all(case["assessment"]["route"] == "human_review" for case in duplicates))
        incomplete = next(case for case in cases if case["applicant"] == "Zoya Ali")
        self.assertEqual(incomplete["assessment"]["route"], "needs_information")
        self.assertIn("monthly income", incomplete["assessment"]["missing"])
        self.call("helper", "GET", f"/api/cases/{incomplete['id']}", expected=403)

    def test_full_lifecycle_and_atomic_redemption(self):
        body = {"applicant": "Sample Harbor Family", "identity_ref": "HH-201", "address": "21 Demo Lane",
                "family_members": 5, "monthly_income": 30000, "confirmed": True, "request_id": uid()}
        record = self.call("citizen", "POST", "/api/cases", body, 201).json()["case"]
        replay = self.call("citizen", "POST", "/api/cases", body).json()
        self.assertTrue(replay["replayed"])
        self.assertEqual(record["id"], replay["case"]["id"])
        self.call("citizen", "POST", "/api/cases", {**body, "monthly_income": 10000}, 409)
        path = f"/api/cases/{record['id']}"
        doc = {"filename": "demo.txt", "mime": "text/plain", "synthetic": True,
               "data": base64.b64encode(document_sample(5, 30000)).decode()}
        record = self.call("citizen", "POST", f"{path}/documents", doc, 201).json()["case"]
        approval = {"action": "approve", "assessment": record["assessment"]["id"], "verified": True, "reason": "Checked the fictional statement and figures."}
        self.call("citizen", "PATCH", f"{path}/decision", approval, 403)
        self.call("officer-cedar", "PATCH", f"{path}/decision", approval, 404)
        self.assertEqual(record["assessment"]["route"], "eligible")
        approved = self.call("officer", "PATCH", f"{path}/decision", approval).json()["case"]
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(approved["decisions"][0]["version"], 1)
        citizen = self.call("citizen", "GET", path).json()["case"]
        voucher_id = citizen["voucher"]["id"]
        voucher_path = f"/api/vouchers/{voucher_id}"
        self.call("merchant", "GET", voucher_path, expected=403)
        token = self.call("citizen", "GET", voucher_path).json()["token"]
        self.assertTrue(self.call("citizen", "GET", voucher_path + "/qr").raw.startswith(b"<svg"))

        def redeem(request_id):
            return self.client.call("POST", "/api/vouchers/redeem", {"token": token, "request_id": request_id}, self.tokens["merchant"])

        attempts = [uid(), uid()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(redeem, attempts))
        self.assertEqual(sorted(result.status for result in outcomes), [201, 409])
        winner = next(i for i, result in enumerate(outcomes) if result.status == 201)
        self.assertTrue(redeem(attempts[winner]).json()["replayed"])
        self.assertEqual(redeem(uid()).status, 409)
        history = self.call("merchant", "GET", "/api/vouchers/history").json()["redemptions"]
        self.assertEqual(len([row for row in history if row["id"] == outcomes[winner].json()["id"]]), 1)
        trace = self.call("auditor", "GET", f"/api/audit/{record['id']}").json()
        self.assertEqual(sum(row["action"] == "voucher.redeemed" for row in trace["events"]), 1)
        self.assertNotIn(token, json.dumps(trace))
        self.assertNotIn(body["applicant"], json.dumps(trace))

    def test_mock_api_key_scope_and_revocation(self):
        result = self.call("admin", "POST", "/api/admin/keys", {"label": "Fictional integration"}).json()
        mock = self.client.call("GET", "/mock/identity/HH-101", key=result["key"])
        self.assertEqual(mock.status, 200)
        self.assertEqual(mock.json()["status"], "matched")
        self.assertEqual(self.client.call("GET", "/api/cases", key=result["key"]).status, 401)
        self.call("admin", "DELETE", f"/api/admin/keys/{result['id']}")
        self.assertEqual(self.client.call("GET", "/mock/data/harbor", key=result["key"]).status, 401)

    def test_rules_change_without_rewriting_history(self):
        rules = {"min_members": 3, "max_income": 21000, "voucher_value": 5500, "evidence_required": True}
        self.call("admin", "POST", "/api/admin/rules/preview", rules)
        cases = self.call("officer", "GET", "/api/cases").json()["cases"]
        old = next(case for case in cases if case["applicant"] == "Amina Rahim")
        old_id = old["assessment"]["id"]
        self.assertEqual(self.call("admin", "PATCH", "/api/admin/rules", {**rules, "base_version": 1}).json()["version"], 2)
        path = f"/api/cases/{old['id']}"
        self.call("officer", "PATCH", f"{path}/decision", {"action": "approve", "assessment": old_id, "verified": True,
                  "reason": "Checked the fictional statement and figures."}, 409)
        refreshed = self.call("officer", "POST", f"/api/ai/assess/{old['id']}").json()["case"]
        self.assertEqual(refreshed["assessment"]["version"], 2)
        self.assertEqual(refreshed["assessment"]["route"], "outside_policy")
        trace = self.call("auditor", "GET", f"/api/audit/{old['id']}").json()
        self.assertTrue(any(item["id"] == old_id and item["version"] == 1 for item in trace["assessments"]))


if __name__ == "__main__":
    unittest.main()
