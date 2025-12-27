import os
import base64
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Config
# -----------------------------
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL")

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
# Helpers
# -----------------------------
def close_incident_with_note(incident_id: int, note: str):
    # Fetch work package
    wp_resp = requests.get(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        timeout=10
    )
    wp_resp.raise_for_status()
    wp = wp_resp.json()

    lock_version = wp["lockVersion"]
    existing_desc = wp.get("description", {}).get("raw", "")

    payload = {
        "lockVersion": lock_version,
        "description": {
            "raw": (
                f"{existing_desc}\n\n"
                f"---\n\n"
                f"### 🛠 Auto-Remediation\n"
                f"{note}"
            )
        },
        "_links": {
            "status": {"href": "/api/v3/statuses/3"}  # Closed
        }
    }

    requests.patch(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        json=payload,
        timeout=10
    ).raise_for_status()


def notify_mattermost(message: str):
    if not MATTERMOST_WEBHOOK_URL:
        return

    requests.post(
        MATTERMOST_WEBHOOK_URL,
        json={"text": message},
        timeout=5
    )

# -----------------------------
# API
# -----------------------------
@app.route("/remediate", methods=["POST"])
def remediate():
    data = request.json

    try:
        incident_id = data["incident_id"]
        service = data["service"]
        severity = data["severity"]
        root_cause = data["root_cause"]

        # 🔒 Guardrail
        if severity == "P1":
            return jsonify({
                "status": "skipped",
                "reason": "P1 requires manual remediation"
            })

        # 🧪 Dummy remediation
        remediation_note = (
            f"Remediation executed automatically.\n\n"
            f"Service: {service}\n"
            f"Root Cause: {root_cause}\n"
            f"Action Taken: Simulated remediation (demo mode)\n"
            f"Result: Service stabilized"
        )

        close_incident_with_note(incident_id, remediation_note)

        notify_mattermost(
            f"✅ **Auto-Remediation Completed**\n"
            f"• Incident ID: {incident_id}\n"
            f"• Service: {service}\n"
            f"• Status: Closed"
        )

        return jsonify({
            "status": "remediated",
            "incident_id": incident_id,
            "action": "simulated_remediation"
        })

    except Exception as e:
        return jsonify({
            "status": "failed",
            "error": str(e)
        }), 500

# -----------------------------
# Health
# -----------------------------
@app.route("/health")
def health():
    return {"status": "ok"}

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000)

