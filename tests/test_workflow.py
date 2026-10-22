import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server


class IncidentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_file_patch = patch.object(server, "STATE_FILE", Path(self.temp_dir.name) / "state.json")
        self.state_file_patch.start()
        self.evidence_patch = patch.object(server, "collect_application_evidence", return_value=["Test-only evidence."])
        self.evidence_patch.start()
        self.analysis_patch = patch.object(server, "llm_analysis", return_value={
            "incident_description": "Test incident description.",
            "evidence_summary": "Test-only evidence.",
            "root_cause_analysis": "Unconfirmed test hypothesis.",
            "knowledge_title": "Test knowledge draft",
            "knowledge_draft": "Draft for human review.",
        })
        self.analysis_patch.start()
        self.incident_create = patch.object(server, "create_incident_in_servicenow", return_value={
            "number": "INC1001", "sys_id": "incident-sys-id", "demo": False,
        })
        self.incident_create.start()
        self.review_create = patch.object(server, "create_review_record", side_effect=[
            {"number": "PRB1001", "sys_id": "problem-sys-id", "demo": False},
            {"number": "KB1001", "sys_id": "kb-sys-id", "demo": False},
        ])
        self.review_create.start()
        self.app_config_patch = patch.dict(os.environ, {
            "APP_AUTH_ENABLED": "false",
            "APP_USERNAME": "",
            "APP_PASSWORD": "",
        })
        self.app_config_patch.start()
        self.httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        patch.stopall()
        self.temp_dir.cleanup()

    def post_json(self, path, payload):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def create_incident(self):
        return self.post_json("/api/incidents", {
            "short_description": "Checkout latency",
            "details": "Checkout requests exceed the response objective.",
            "category": "Performance",
            "impact": "2",
            "urgency": "2",
        })

    def test_incident_submission_does_not_create_problem_or_knowledge(self):
        status, response = self.create_incident()

        self.assertEqual(status, 201)
        self.assertEqual(response["incident"]["number"], "INC1001")
        self.assertEqual(response["incident"]["priority"], "3")
        server.create_incident_in_servicenow.assert_called_once()
        server.create_review_record.assert_not_called()

    def test_problem_and_knowledge_are_created_only_after_approval(self):
        _, created = self.create_incident()
        incident_id = created["incident"]["id"]
        server.create_review_record.assert_not_called()

        problem_status, problem = self.post_json(f"/api/incidents/{incident_id}/approve/rca", {})
        self.assertEqual(problem_status, 200)
        self.assertEqual(problem["incident"]["problem_number"], "PRB1001")
        server.create_review_record.assert_called_once_with(unittest.mock.ANY, "rca")

        kb_status, knowledge = self.post_json(f"/api/incidents/{incident_id}/approve/kb", {})
        self.assertEqual(kb_status, 200)
        self.assertEqual(knowledge["incident"]["kb_number"], "KB1001")
        self.assertEqual(server.create_review_record.call_count, 2)

        duplicate_status, _ = self.post_json(f"/api/incidents/{incident_id}/approve/rca", {})
        self.assertEqual(duplicate_status, 409)
        self.assertEqual(server.create_review_record.call_count, 2)

    def test_priority_reference_covers_all_impact_urgency_pairs(self):
        priorities = [server.priority_for(impact, urgency) for impact in "123" for urgency in "123"]
        self.assertEqual(priorities, ["1", "2", "3", "2", "3", "4", "3", "4", "5"])

    def test_dashboard_and_api_require_configured_basic_auth(self):
        credentials = base64.b64encode(b"operator:long-test-password").decode("ascii")
        with patch.dict(os.environ, {
            "APP_AUTH_ENABLED": "true",
            "APP_USERNAME": "operator",
            "APP_PASSWORD": "long-test-password",
        }):
            with self.assertRaises(HTTPError) as unauthorized:
                urlopen(self.base_url + "/api/reference")
            self.assertEqual(unauthorized.exception.code, 401)

            request = Request(self.base_url + "/api/reference", headers={"Authorization": f"Basic {credentials}"})
            with urlopen(request) as response:
                self.assertEqual(response.status, 200)

    def test_robot_shop_probe_uses_head_and_returns_http_evidence(self):
        self.evidence_patch.stop()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.status = 200
        with patch.dict(os.environ, {"ROBOT_SHOP_URL": "http://robot-shop.example.test/"}):
            with patch.object(server, "urlopen", return_value=response) as urlopen_mock:
                evidence = server.collect_application_evidence()

        self.assertIn("HTTP 200", evidence[0])
        self.assertEqual(urlopen_mock.call_args.args[0].method, "HEAD")


if __name__ == "__main__":
    unittest.main()