import os
import base64
import time
import requests
from typing import Dict
from collections import defaultdict
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL")
RCA_AGENT_URL = os.getenv("RCA_AGENT_URL", "http://rca-agent:6000/rca")

PROJECT_ID = 35
TYPE_ID = 1  # Task

# -----------------------------
# Auth
# -----------------------------
basic_auth = base64.b64encode(f"apikey:{OPENPROJECT_API_TOKEN}".encode()).decode()

HEADERS = {
    "Authorization": f"Basic {basic_auth}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# -----------------------------
# SLA Policy
# -----------------------------
SEVERITY_SLA_POLICY = {
    "P1": {"response": "15 minutes", "resolution": "4 hours"},
    "P2": {"response": "30 minutes", "resolution": "8 hours"},
    "P3": {"response": "2 hours", "resolution": "24 hours"},
}

# -----------------------------
# Deduplication
# -----------------------------
CORRELATION_WINDOW_SECONDS = 15 * 60
correlation_cache: Dict[str, Dict] = {}

# -----------------------------
# Problem Management
# -----------------------------
PROBLEM_THRESHOLD = 3
PROBLEM_WINDOW_SECONDS = 24 * 60 * 60

problem_tracker = defaultdict(list)
problem_registry = {}

# -----------------------------
# Helpers
# -----------------------------
def determine_severity(alert):
    sev = alert.get("labels", {}).get("severity", "").lower()
    name = alert.get("labels", {}).get("alertname", "").lower()
    if sev == "critical" or "payment" in name:
        return "P1"
    if sev == "warning":
        return "P3"
    return "P2"


def determine_sla(severity):
    return SEVERITY_SLA_POLICY.get(severity, SEVERITY_SLA_POLICY["P2"])


def correlation_key(alert):
    l = alert.get("labels", {})
    return f"{l.get('alertname')}:{l.get('service')}"


def existing_incident(key):
    entry = correlation_cache.get(key)
    if not entry:
        return None
    if time.time() - entry["timestamp"] > CORRELATION_WINDOW_SECONDS:
        del correlation_cache[key]
        return None
    return entry["incident_id"]


def store_incident(key, incident_id):
    correlation_cache[key] = {
        "incident_id": incident_id,
        "timestamp": time.time()
    }


def create_work_item(title, description):
    payload = {
        "subject": title,
        "description": {"raw": description},
        "_links": {
            "project": {"href": f"/api/v3/projects/{PROJECT_ID}"},
            "type": {"href": f"/api/v3/types/{TYPE_ID}"}
        }
    }
    r = requests.post(
        f"{OPENPROJECT_URL}/work_packages",
        headers=HEADERS,
        json=payload,
        timeout=10
    )
    r.raise_for_status()
    return r.json()


def notify_mm(msg):
    if MATTERMOST_WEBHOOK_URL:
        requests.post(MATTERMOST_WEBHOOK_URL, json={"text": msg}, timeout=5)


# -----------------------------
# Problem Logic (TRIGGERS RCA)
# -----------------------------
def check_or_create_problem(key, summary, service):
    now = time.time()

    problem_tracker[key] = [
        t for t in problem_tracker[key]
        if now - t <= PROBLEM_WINDOW_SECONDS
    ]
    problem_tracker[key].append(now)

    if key in problem_registry:
        return problem_registry[key]

    if len(problem_tracker[key]) >= PROBLEM_THRESHOLD:
        problem = create_work_item(
            f"[PROBLEM] Repeated issue – {summary}",
            f"Issue occurred {len(problem_tracker[key])} times.\nKey: {key}"
        )
        problem_id = problem["id"]
        problem_registry[key] = problem_id

        notify_mm(f"🧠 **Problem Created**\n• {summary}\n• ID: {problem_id}")

        # 🔥 RCA TRIGGER (CORRECT PLACE)
        requests.post(
            RCA_AGENT_URL,
            json={
                "problem_id": problem_id,
                "service": service,
                "summary": summary
            },
            timeout=3
        )

        return problem_id

    return None


# -----------------------------
# Alert Receiver
# -----------------------------
@app.route("/alert", methods=["POST"])
def receive_alert():
    alert = request.json["alerts"][0]
    summary = alert.get("annotations", {}).get("summary", "Unknown")
    service = alert.get("labels", {}).get("service", "ecommerce")

    severity = determine_severity(alert)
    sla = determine_sla(severity)
    key = correlation_key(alert)

    problem_id = check_or_create_problem(key, summary, service)
    existing = existing_incident(key)

    if existing:
        notify_mm(f"🔁 Duplicate alert\n• {summary}\n• Incident {existing}")
        return jsonify({"status": "duplicate"})

    incident = create_work_item(
        f"[{severity}] {summary}",
        f"SLA: {sla['response']} / {sla['resolution']}\n\n{alert}"
    )

    store_incident(key, incident["id"])

    notify_mm(
        f"🚨 **{severity} Incident Created**\n"
        f"• {summary}\n• ID: {incident['id']}"
    )

    return jsonify({"status": "created"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

