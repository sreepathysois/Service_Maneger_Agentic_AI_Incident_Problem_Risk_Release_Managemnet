import os
import base64
import requests
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

# ITSM Project + Type
PROJECT_ID = 35          # ITSM Project
TYPE_ID = 1              # Task (used as Incident)

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
# Agents
# -----------------------------
def determine_severity(alert: dict) -> str:
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "").lower()
    severity_label = labels.get("severity", "").lower()

    # P1 conditions
    if severity_label == "critical":
        return "P1"
    if severity_label == "warning":
        return "P3"
    if "payment" in alertname:
        return "P1"

    # P3 conditions
    if "traffic" in alertname or "orders" in alertname:
        return "P3"

    # Default
    return "P2"


def determine_sla(severity: str) -> dict:
    return SEVERITY_SLA_POLICY.get(severity, SEVERITY_SLA_POLICY["P2"])


# -----------------------------
# Helpers
# -----------------------------
def create_incident(title: str, description: str):
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

    print("OpenProject status:", resp.status_code)
    print("OpenProject response:", resp.text)

    resp.raise_for_status()
    return resp.json()


def notify_mattermost(message: str):
    if not MATTERMOST_WEBHOOK_URL:
        print("Mattermost webhook not configured, skipping")
        return

    resp = requests.post(
        MATTERMOST_WEBHOOK_URL,
        json={"text": message},
        timeout=5
    )

    print("Mattermost status:", resp.status_code)
    resp.raise_for_status()


# -----------------------------
# API Endpoint
# -----------------------------
@app.route("/alert", methods=["POST"])
def receive_alert():
    data = request.json

    try:
        alert = data["alerts"][0]

        summary = alert.get("annotations", {}).get(
            "summary", "Unknown Incident"
        )

        severity = determine_severity(alert)
        sla = determine_sla(severity)

        title = f"[{severity}] {summary}"

        description = (
            f"Alert Summary: {summary}\n\n"
            f"Severity: {severity}\n"
            f"SLA Response: {sla['response']}\n"
            f"SLA Resolution: {sla['resolution']}\n\n"
            f"Raw Alert Payload:\n{alert}"
        )

        incident = create_incident(title, description)

        notify_mattermost(
            f"🚨 **{severity} Incident Created**\n"
            f"• {summary}\n"
            f"• SLA Response: {sla['response']}\n"
            f"• SLA Resolution: {sla['resolution']}\n"
            f"• ITSM ID: {incident.get('id')}"
        )

        return jsonify({
            "status": "processed",
            "incident_id": incident.get("id"),
            "severity": severity
        })

    except Exception as e:
        print("ERROR:", str(e))
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

