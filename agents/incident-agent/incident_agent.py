import os
import base64
import time
import requests
from typing import Dict
from collections import defaultdict
from flask import Flask, request, jsonify

# -----------------------------
# Flask App
# -----------------------------
app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL")
RCA_AGENT_URL = os.getenv("RCA_AGENT_URL", "http://rca-agent:6000/rca")

PROJECT_ID = 35      # ITSM Project
TYPE_ID = 1          # Task (Incident + Problem)

# -----------------------------
# OpenProject Auth
# -----------------------------
basic_auth = base64.b64encode(
    f"apikey:{OPENPROJECT_API_TOKEN}".encode()
).decode()

HEADERS = {
    "Authorization": f"Basic {basic_auth}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# -----------------------------
# Severity & SLA Policy
# -----------------------------
SEVERITY_SLA_POLICY = {
    "P1": {"response": "15 minutes", "resolution": "4 hours"},
    "P2": {"response": "30 minutes", "resolution": "8 hours"},
    "P3": {"response": "2 hours", "resolution": "24 hours"},
}

# -----------------------------
# Deduplication / Correlation
# -----------------------------
CORRELATION_WINDOW_SECONDS = 15 * 60  # 15 minutes
correlation_cache: Dict[str, Dict] = {}

# -----------------------------
# Problem Management
# -----------------------------
PROBLEM_THRESHOLD = 3
PROBLEM_WINDOW_SECONDS = 24 * 60 * 60  # 24 hours

problem_tracker = defaultdict(list)   # correlation_key -> timestamps
problem_registry = {}                 # correlation_key -> problem_id

# -----------------------------
# Agents
# -----------------------------
def determine_severity(alert: dict) -> str:
    labels = alert.get("labels", {})
    severity_label = labels.get("severity", "").lower()
    alertname = labels.get("alertname", "").lower()

    if severity_label == "critical":
        return "P1"
    if severity_label == "warning":
        return "P3"
    if "payment" in alertname:
        return "P1"

    return "P2"


def determine_sla(severity: str) -> dict:
    return SEVERITY_SLA_POLICY.get(severity, SEVERITY_SLA_POLICY["P2"])


# -----------------------------
# Correlation Helpers
# -----------------------------
def get_correlation_key(alert: dict) -> str:
    labels = alert.get("labels", {})
    return f"{labels.get('alertname','unknown')}:{labels.get('service','unknown')}"


def check_existing_incident(key: str):
    entry = correlation_cache.get(key)

    if not entry:
        return None

    if time.time() - entry["timestamp"] > CORRELATION_WINDOW_SECONDS:
        del correlation_cache[key]
        return None

    return entry["incident_id"]


def store_incident(key: str, incident_id: int):
    correlation_cache[key] = {
        "incident_id": incident_id,
        "timestamp": time.time()
    }


# -----------------------------
# Problem Agent
# -----------------------------
def check_or_create_problem(correlation_key: str, summary: str):
    now = time.time()

    # Cleanup old occurrences
    problem_tracker[correlation_key] = [
        t for t in problem_tracker[correlation_key]
        if now - t <= PROBLEM_WINDOW_SECONDS
    ]

    # Record occurrence
    problem_tracker[correlation_key].append(now)

    # Problem already exists
    if correlation_key in problem_registry:
        return problem_registry[correlation_key]

    # Threshold reached → create Problem
    if len(problem_tracker[correlation_key]) >= PROBLEM_THRESHOLD:
        title = f"[PROBLEM] Repeated issue – {summary}"
        desc = (
            f"This issue occurred {len(problem_tracker[correlation_key])} times "
            f"in the last 24 hours.\n\n"
            f"Correlation Key: {correlation_key}"
        )

        problem = create_work_item(title, desc)
        problem_id = problem.get("id")

        problem_registry[correlation_key] = problem_id

        notify_mattermost(
            f"🧠 **Problem Created**\n"
            f"• {summary}\n"
            f"• Problem ID: {problem_id}"
        )

        return problem_id

    return None


# -----------------------------
# OpenProject Helpers
# -----------------------------
def create_work_item(title: str, description: str):
    payload = {
        "subject": title,
        "description": {"raw": description},
        "_links": {
            "project": {"href": f"/api/v3/projects/{PROJECT_ID}"},
            "type": {"href": f"/api/v3/types/{TYPE_ID}"}
        }
    }

    resp = requests.post(
        f"{OPENPROJECT_URL}/work_packages",
        json=payload,
        headers=HEADERS,
        timeout=10
    )

    resp.raise_for_status()
    return resp.json()


def notify_mattermost(message: str):
    if not MATTERMOST_WEBHOOK_URL:
        return

    requests.post(
        MATTERMOST_WEBHOOK_URL,
        json={"text": message},
        timeout=5
    )


# -----------------------------
# Alert Receiver
# -----------------------------
@app.route("/alert", methods=["POST"])
def receive_alert():
    data = request.json

    try:
        alert = data["alerts"][0]
        summary = alert.get("annotations", {}).get("summary", "Unknown Incident")

        severity = determine_severity(alert)
        sla = determine_sla(severity)

        correlation_key = get_correlation_key(alert)

        # -----------------------------
        # PROBLEM CHECK (ALWAYS)
        # -----------------------------
        problem_id = check_or_create_problem(correlation_key, summary)

        # -----------------------------
        # DEDUPLICATION CHECK
        # -----------------------------
        existing_incident = check_existing_incident(correlation_key)

        if existing_incident:
            notify_mattermost(
                f"🔁 **Duplicate Alert**\n"
                f"• {summary}\n"
                f"• Linked Incident ID: {existing_incident}"
                + (f"\n• Problem ID: {problem_id}" if problem_id else "")
            )
            return jsonify({
                "status": "duplicate",
                "incident_id": existing_incident,
                "problem_id": problem_id
            })

        # -----------------------------
        # CREATE NEW INCIDENT
        # -----------------------------
        title = f"[{severity}] {summary}"
        description = (
            f"Severity: {severity}\n"
            f"SLA Response: {sla['response']}\n"
            f"SLA Resolution: {sla['resolution']}\n\n"
            f"Alert Payload:\n{alert}"
        )

        incident = create_work_item(title, description)
        incident_id = incident.get("id")

        store_incident(correlation_key, incident_id)

        notify_mattermost(
            f"🚨 **{severity} Incident Created**\n"
            f"• {summary}\n"
            f"• SLA: {sla['response']} / {sla['resolution']}\n"
            f"• Incident ID: {incident_id}"
            + (f"\n• Problem ID: {problem_id}" if problem_id else "")
        )

        # -----------------------------
        # TRIGGER RCA (FIRST INCIDENT ONLY)
        # -----------------------------
        try:
            service = alert.get("labels", {}).get("service", "ecommerce")

            requests.post(
                RCA_AGENT_URL,
                json={
                    "incident_id": incident_id,
                    "service": service,
                    "summary": summary
                },
                timeout=2
            )
        except Exception as e:
            print("RCA trigger failed:", str(e))

        return jsonify({
            "status": "processed",
            "incident_id": incident_id,
            "severity": severity,
            "problem_id": problem_id
        })

    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

