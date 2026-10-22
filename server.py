"""Local incident workbench with guarded ServiceNow approval actions."""

from __future__ import annotations

import base64
import hmac
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = ROOT / "data"
REFERENCE_FILE = DATA / "incident_reference.json"
LOCK = threading.Lock()
STATE_MUTATION_LOCK = threading.Lock()
APP_NAME = "Robot Shop"


def load_config():
    config_path = ROOT / "config.env"
    if config_path.exists():
        for raw_line in config_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip("\"'")
            os.environ.setdefault(key.strip(), value)


load_config()
APP_NAMESPACE = os.getenv("APP_NAMESPACE", "guru")
STATE_FILE = Path(os.getenv("STATE_FILE", str(DATA / "state.json")))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_records():
    with LOCK:
        if not STATE_FILE.exists():
            return []
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []


def write_records(records):
    with LOCK:
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.replace(STATE_FILE)


def integrations_status():
    return {
        "servicenow": bool(os.getenv("SN_INSTANCE_URL") and os.getenv("SN_USERNAME") and os.getenv("SN_PASSWORD")),
        "llm": bool(os.getenv("LLM_API_KEY")),
        "application": bool(os.getenv("ROBOT_SHOP_URL")),
        "namespace": APP_NAMESPACE,
    }


def sample_records():
    now = utc_now()
    return [
        {
            "id": "sample-robot-shop-01",
            "number": "DEMO-1042",
            "demo": True,
            "created_at": now,
            "short_description": "Checkout latency elevated in Robot Shop",
            "application": APP_NAME,
            "category": "Performance",
            "impact": "2",
            "urgency": "2",
            "priority": "3",
            "status": "In progress",
            "sync_status": "demo",
            "description": "Checkout requests are slower than the expected service level. This is a demonstration incident, not a live alert.",
            "evidence": ["Demo record: no live Kubernetes evidence was collected."],
            "root_cause": "Pending investigation. Validate checkout service latency and its dependencies against live telemetry.",
            "knowledge_title": "Robot Shop checkout latency investigation",
            "knowledge_body": "Draft only. Check the checkout service, its upstream dependencies, and recent deployment changes before publishing a resolution.",
            "rca_review": "pending",
            "kb_review": "pending",
            "problem_number": "",
            "kb_number": "",
        },
        {
            "id": "sample-robot-shop-02",
            "number": "DEMO-1041",
            "demo": True,
            "created_at": now,
            "short_description": "Robot Shop product page intermittent 5xx responses",
            "application": APP_NAME,
            "category": "Availability",
            "impact": "3",
            "urgency": "1",
            "priority": "3",
            "status": "New",
            "sync_status": "demo",
            "description": "Users reported occasional errors while loading the product page. This is a demonstration incident, not a live alert.",
            "evidence": ["Demo record: no live Kubernetes evidence was collected."],
            "root_cause": "Unconfirmed. Compare pod restarts and warning events with the reported error window.",
            "knowledge_title": "Robot Shop product page 5xx triage",
            "knowledge_body": "Draft only. Confirm the failing component and recovery steps with production evidence before publication.",
            "rca_review": "pending",
            "kb_review": "pending",
            "problem_number": "",
            "kb_number": "",
        },
    ]


def priority_for(impact, urgency):
    with REFERENCE_FILE.open(encoding="utf-8") as reference_file:
        data = json.load(reference_file)
    return data["priority_matrix"][f"{impact}-{urgency}"]


def collect_application_evidence():
    application_url = os.getenv("ROBOT_SHOP_URL", "").strip()
    if not application_url:
        return ["Robot Shop LoadBalancer URL is not configured; no live application health evidence was collected."]
    parsed_url = urlparse(application_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
        return ["Robot Shop LoadBalancer URL is invalid; expected an http:// or https:// URL without embedded credentials."]
    request = Request(application_url, method="HEAD", headers={"User-Agent": "ROBO-SHOP-Incident-Workbench/1.0"})
    try:
        with urlopen(request, timeout=8) as response:
            return [f"Robot Shop LoadBalancer probe: HTTP {response.status}; endpoint responded to HEAD."]
    except HTTPError as error:
        return [f"Robot Shop LoadBalancer probe: HTTP {error.code}; endpoint returned an HTTP error."]
    except (URLError, TimeoutError, OSError) as error:
        return [f"Robot Shop LoadBalancer probe failed: {str(error)[:240]}"]


def fallback_analysis(payload, evidence):
    evidence_text = " ".join(evidence)
    if any("phase=Failed" in item or "phase=Pending" in item or "restarts=" in item and not item.endswith("restarts=0.") for item in evidence):
        cause = "The available cluster snapshot shows a pod health or restart signal. This is a lead, not a confirmed root cause; correlate affected components and timestamps with application telemetry and recent changes."
    else:
        cause = "The current evidence does not establish a root cause. Check application logs, dependency health, and recent deployments for the reported time window before treating any hypothesis as confirmed."
    return {
        "incident_description": f"{payload['details'].strip()}\n\nMonitored application: {APP_NAME}. Workbench namespace: {APP_NAMESPACE}. Category: {payload['category']}. Impact: {payload['impact']}; urgency: {payload['urgency']}; calculated priority: P{payload['priority']}.",
        "evidence_summary": evidence_text,
        "root_cause_analysis": cause,
        "knowledge_title": f"{APP_NAME}: {payload['short_description'].strip()[:90]}",
        "knowledge_draft": "Draft for human review.\n\nSymptoms: " + payload["details"].strip() + "\n\nEvidence reviewed: " + evidence_text + "\n\nRecommended checks: inspect the affected service logs, compare health and restart signals during the incident window, and verify dependencies and recent releases. Do not publish a confirmed cause or resolution until validated by the responder.",
    }


def llm_analysis(payload, evidence):
    fallback = fallback_analysis(payload, evidence)
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return fallback
    endpoint = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions").rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    request_body = {
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are an incident response assistant. Use only supplied facts. Separate evidence from hypotheses; never claim a root cause is confirmed without proof. Return JSON with string keys incident_description, evidence_summary, root_cause_analysis, knowledge_title, knowledge_draft. KB text must be a reviewable draft with symptoms, verified evidence, safe diagnostic steps, and a resolution section marked unconfirmed when unknown."},
            {"role": "user", "content": json.dumps({"incident": payload, "read_only_kubernetes_evidence": evidence})},
        ],
    }
    request = Request(
        endpoint,
        data=json.dumps(request_body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=35) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        content = response_data["choices"][0]["message"]["content"]
        generated = json.loads(content)
        for field in fallback:
            if not isinstance(generated.get(field), str) or not generated[field].strip():
                generated[field] = fallback[field]
        return generated
    except (HTTPError, URLError, TimeoutError, KeyError, IndexError, json.JSONDecodeError, OSError):
        fallback["llm_note"] = "LLM request failed; a clearly labeled rule-based draft was used instead."
        return fallback


def servicenow_request(table, record):
    instance = os.getenv("SN_INSTANCE_URL", "").rstrip("/")
    username = os.getenv("SN_USERNAME", "")
    password = os.getenv("SN_PASSWORD", "")
    if not (instance and username and password):
        return None
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = Request(
        f"{instance}/api/now/table/{table}?sysparm_display_value=true",
        data=json.dumps(record).encode("utf-8"),
        headers={"Authorization": f"Basic {credentials}", "Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8")).get("result", {})
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ServiceNow rejected the {table} request ({error.code}): {detail}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(f"ServiceNow connection failed for {table}: {error}") from error


def create_incident_in_servicenow(record):
    result = servicenow_request("incident", {
        "short_description": record["short_description"],
        "description": record["description"],
        "impact": record["impact"],
        "urgency": record["urgency"],
        "priority": record["priority"],
        "category": record["category"],
    })
    if result is None:
        return {"number": record["number"], "sys_id": "", "demo": True}
    return {"number": result.get("number", ""), "sys_id": result.get("sys_id", ""), "demo": False}


def create_review_record(record, kind):
    if kind == "rca":
        result = servicenow_request("problem", {
            "short_description": f"RCA: {record['short_description']}",
            "description": record["root_cause"],
            "cause_notes": record["root_cause"],
            "workaround": "Refer to the linked incident and validate the proposed investigation before implementing a permanent fix.",
        })
    else:
        result = servicenow_request("kb_knowledge", {
            "short_description": record["knowledge_title"],
            "text": record["knowledge_body"],
            "workflow_state": "draft",
        })
    if result is None:
        return {"number": "DEMO-" + uuid.uuid4().hex[:6].upper(), "sys_id": "", "demo": True}
    return {"number": result.get("number", ""), "sys_id": result.get("sys_id", ""), "demo": False}


def json_response(handler, status, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "IncidentWorkbench/1.0"

    def log_message(self, format_string, *args):
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def authenticate(self):
        auth_enabled = os.getenv("APP_AUTH_ENABLED", "false").lower() == "true"
        expected_username = os.getenv("APP_USERNAME", "")
        expected_password = os.getenv("APP_PASSWORD", "")
        if not auth_enabled and not expected_username and not expected_password:
            return True
        if not expected_username or not expected_password:
            self.send_error(503, "Dashboard authentication is enabled but credentials are not configured.")
            return False
        authorization = self.headers.get("Authorization", "")
        scheme, _, encoded = authorization.partition(" ")
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8") if scheme.lower() == "basic" else ""
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        username, separator, password = decoded.partition(":")
        valid = (
            bool(separator)
            and hmac.compare_digest(username, expected_username)
            and hmac.compare_digest(password, expected_password)
        )
        if valid:
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ROBO-SHOP Incident Desk", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        return False

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            json_response(self, 200, {"status": "ok"})
            return
        if not self.authenticate():
            return
        if path == "/api/state":
            records = read_records()
            json_response(self, 200, {
                "incidents": records if records else sample_records(),
                "integrations": integrations_status(),
                "demo_data": not records or all(item.get("demo", False) for item in records),
            })
            return
        if path == "/api/reference":
            with REFERENCE_FILE.open(encoding="utf-8") as reference_file:
                json_response(self, 200, json.load(reference_file))
            return
        target = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
        if not target.resolve().is_relative_to(STATIC.resolve()) or not target.is_file():
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8" if target.suffix == ".html" else "text/javascript; charset=utf-8" if target.suffix == ".js" else "text/css; charset=utf-8"
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size > 64_000:
            raise ValueError("Request is too large.")
        return json.loads(self.rfile.read(size).decode("utf-8"))

    def do_POST(self):
        if not self.authenticate():
            return
        path = urlparse(self.path).path
        try:
            payload = self.read_json_body()
            if path == "/api/incidents":
                self.create_incident(payload)
                return
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[0] == "api" and parts[1] == "incidents" and parts[3] == "approve":
                with STATE_MUTATION_LOCK:
                    self.approve_record(parts[2], parts[4])
                return
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "incidents" and parts[3] == "sync":
                with STATE_MUTATION_LOCK:
                    self.sync_incident(parts[2])
                return
            json_response(self, 404, {"error": "Route not found."})
        except (ValueError, json.JSONDecodeError) as error:
            json_response(self, 400, {"error": str(error) or "Invalid JSON request."})
        except RuntimeError as error:
            json_response(self, 502, {"error": str(error)})

    def create_incident(self, payload):
        fields = ("short_description", "details", "category", "impact", "urgency")
        if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in fields):
            json_response(self, 400, {"error": "Complete all required incident fields."})
            return
        if len(payload["short_description"]) > 160 or len(payload["details"]) > 4000:
            json_response(self, 400, {"error": "Summary or details exceed the allowed length."})
            return
        if payload["impact"] not in {"1", "2", "3"} or payload["urgency"] not in {"1", "2", "3"}:
            json_response(self, 400, {"error": "Impact and urgency must be 1, 2, or 3."})
            return
        with REFERENCE_FILE.open(encoding="utf-8") as reference_file:
            reference = json.load(reference_file)
        if payload["category"] not in reference["categories"]:
            json_response(self, 400, {"error": "Select a supported incident category."})
            return
        payload["priority"] = priority_for(payload["impact"], payload["urgency"])
        evidence = collect_application_evidence()
        generated = llm_analysis(payload, evidence)
        record = {
            "id": uuid.uuid4().hex,
            "number": "LOCAL-" + uuid.uuid4().hex[:6].upper(),
            "created_at": utc_now(),
            "short_description": payload["short_description"].strip(),
            "application": APP_NAME,
            "category": payload["category"],
            "impact": payload["impact"],
            "urgency": payload["urgency"],
            "priority": payload["priority"],
            "status": "New",
            "description": generated["incident_description"],
            "evidence": evidence + [generated["evidence_summary"]],
            "root_cause": generated["root_cause_analysis"],
            "knowledge_title": generated["knowledge_title"],
            "knowledge_body": generated["knowledge_draft"],
            "rca_review": "pending",
            "kb_review": "pending",
            "problem_number": "",
            "kb_number": "",
        }
        try:
            sync_result = create_incident_in_servicenow(record)
            record["number"] = sync_result["number"] or "SN incident created"
            record["servicenow_sys_id"] = sync_result["sys_id"]
            record["sync_status"] = "demo" if sync_result["demo"] else "synced"
            record["demo"] = sync_result["demo"]
        except RuntimeError as error:
            record["number"] = "LOCAL-" + uuid.uuid4().hex[:6].upper()
            record["sync_status"] = "failed"
            record["sync_error"] = str(error)
        with STATE_MUTATION_LOCK:
            records = read_records()
            records.insert(0, record)
            write_records(records)
        json_response(self, 201, {"incident": record, "integrations": integrations_status(), "llm_note": generated.get("llm_note", "")})

    def approve_record(self, incident_id, kind):
        if kind not in {"rca", "kb"}:
            json_response(self, 404, {"error": "Review type must be rca or kb."})
            return
        records = read_records()
        record = next((item for item in records if item["id"] == incident_id), None)
        if record is None:
            if incident_id in {"sample-robot-shop-01", "sample-robot-shop-02"}:
                records = sample_records()
                record = next(item for item in records if item["id"] == incident_id)
            else:
                json_response(self, 404, {"error": "Incident not found."})
                return
        status_key = "rca_review" if kind == "rca" else "kb_review"
        number_key = "problem_number" if kind == "rca" else "kb_number"
        if record.get(status_key) == "approved" and record.get(number_key):
            json_response(self, 409, {"error": "This review has already created its ServiceNow record."})
            return
        if record.get(status_key) != "pending":
            json_response(self, 409, {"error": "This item is not waiting for review."})
            return
        created = create_review_record(record, kind)
        record[status_key] = "approved"
        record[number_key] = created["number"] or "Created in ServiceNow"
        record["problem_sys_id" if kind == "rca" else "kb_sys_id"] = created["sys_id"]
        record["problem_mode" if kind == "rca" else "kb_mode"] = "demo" if created["demo"] else "servicenow"
        write_records(records)
        json_response(self, 200, {"incident": record})

    def sync_incident(self, incident_id):
        records = read_records()
        record = next((item for item in records if item["id"] == incident_id), None)
        if record is None:
            json_response(self, 404, {"error": "Incident not found."})
            return
        if record.get("sync_status") == "synced":
            json_response(self, 409, {"error": "Incident is already synchronized."})
            return
        result = create_incident_in_servicenow(record)
        record["number"] = result["number"] or record["number"]
        record["servicenow_sys_id"] = result["sys_id"]
        record["sync_status"] = "demo" if result["demo"] else "synced"
        record.pop("sync_error", None)
        write_records(records)
        json_response(self, 200, {"incident": record})


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8080"))
    print(f"Incident Workbench running at http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()