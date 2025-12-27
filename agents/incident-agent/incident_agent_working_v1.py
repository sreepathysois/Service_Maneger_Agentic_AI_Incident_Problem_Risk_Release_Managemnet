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
PROJECT_ID = 35          # <-- YOUR ITSM PROJECT ID
TYPE_ID = 1              # Task (used as Incident)

# -----------------------------
# OpenProject Auth (CORRECT)
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
        print("Mattermost webhook not configured, skipping notification")
        return

    resp = requests.post(
        MATTERMOST_WEBHOOK_URL,
        json={"text": message},
        timeout=5
    )

    print("Mattermost status:", resp.status_code)
    print("Mattermost response:", resp.text)

    resp.raise_for_status()


# -----------------------------
# API Endpoint (Alert Receiver)
# -----------------------------
@app.route("/alert", methods=["POST"])
def receive_alert():
    data = request.json

    try:
        alert = data["alerts"][0]
        title = alert.get("annotations", {}).get(
            "summary", "Unknown Incident"
        )

        incident = create_incident(
            title=title,
            description=str(data)
        )

        notify_mattermost(
            f"🚨 **Incident Created**\n"
            f"• Title: {title}\n"
            f"• ID: {incident.get('id')}\n"
            f"• Project: ITSM"
        )

        return jsonify({
            "status": "processed",
            "incident_id": incident.get("id")
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

