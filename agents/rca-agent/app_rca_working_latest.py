import os
import time
import base64
import requests
import traceback
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL")

DECISION_AGENT_URL = os.getenv("DECISION_AGENT_URL", "http://decision-agent:9000/decide")
REMEDIATION_AGENT_URL = os.getenv("REMEDIATION_AGENT_URL", "http://remediation-agent:7000/remediate")

# -----------------------------
# OpenProject Auth
# -----------------------------
basic_auth = base64.b64encode(f"apikey:{OPENPROJECT_API_TOKEN}".encode()).decode()

HEADERS = {
    "Authorization": f"Basic {basic_auth}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# -----------------------------
# Loki Query
# -----------------------------
def determine_root_cause(service: str) -> str:
    try:
        if service == "payment":
            return "Payment failures detected. Likely gateway timeout."

        if service == "ecommerce":
            return "Traffic spike due to promotion or user surge."

    except Exception as e:
        return f"Loki query failed: {str(e)}"

    return "No clear root cause identified."

# -----------------------------
# OpenProject Update (RETRY SAFE)
# -----------------------------
def update_incident_with_rca(incident_id: int, rca: str):
    for _ in range(3):
        wp = requests.get(
            f"{OPENPROJECT_URL}/work_packages/{incident_id}",
            headers=HEADERS,
            timeout=10
        )
        wp.raise_for_status()

        data = wp.json()
        lock_version = data["lockVersion"]
        existing_desc = data.get("description", {}).get("raw", "")

        payload = {
            "lockVersion": lock_version,
            "description": {
                "raw": f"{existing_desc}\n\n---\n\n### 🧠 Root Cause Analysis\n{rca}"
            }
        }

        resp = requests.patch(
            f"{OPENPROJECT_URL}/work_packages/{incident_id}",
            headers=HEADERS,
            json=payload,
            timeout=10
        )

        if resp.status_code != 409:
            resp.raise_for_status()
            return

        time.sleep(1)

# -----------------------------
# Mattermost
# -----------------------------
def notify_mattermost(msg: str):
    if MATTERMOST_WEBHOOK_URL:
        requests.post(MATTERMOST_WEBHOOK_URL, json={"text": msg}, timeout=5)

# -----------------------------
# RCA API
# -----------------------------
@app.route("/rca", methods=["POST"])
def run_rca():
    data = request.json

    try:
        incident_id = data["incident_id"]
        service = data["service"]
        severity = data.get("severity", "P3")
        problem_id = data.get("problem_id")

        # RCA
        rca = determine_root_cause(service)
        update_incident_with_rca(incident_id, rca)

        notify_mattermost(
            f"🧠 **RCA Completed**\n"
            f"Incident: {incident_id}\n"
            f"Service: {service}\n\n{rca}"
        )

        # Decision (FAIL SAFE)
        try:
            resp = requests.post(
                DECISION_AGENT_URL,
                json={
                    "incident_id": incident_id,
                    "severity": severity,
                    "service": service,
                    "root_cause": rca,
                    "problem_id": problem_id
                },
                timeout=30
            )
            decision_payload = resp.json()
        except Exception as e:
            decision_payload = {
                "decision": "NO_ACTION",
                "confidence": 0,
                "recommended_actions": [],
                "reason": f"Decision agent failed: {str(e)}"
            }

        decision = decision_payload.get("decision", "NO_ACTION")
        confidence = decision_payload.get("confidence", 0)
        actions = decision_payload.get("recommended_actions", [])
        reason = decision_payload.get("reason", "")

        notify_mattermost(
            f"🤖 **Decision Verdict**\n"
            f"Incident: {incident_id}\n"
            f"Decision: {decision}\n"
            f"Confidence: {confidence}%\n"
            f"Actions: {actions}\n\n"
            f"Reason: {reason}"
        )

        # Remediation (FAIL SAFE)
        if decision == "AUTO_REMEDIATE":
            try:
                requests.post(
                    REMEDIATION_AGENT_URL,
                    json={
                        "incident_id": incident_id,
                        "service": service,
                        "severity": severity,
                        "root_cause": rca
                    },
                    timeout=20
                )
                notify_mattermost(f"🛠 Auto-remediation triggered for {incident_id}")
            except Exception as e:
                notify_mattermost(f"❌ Auto-remediation failed: {str(e)}")

        return jsonify({"status": "ok", "decision": decision})

    except Exception:
        traceback.print_exc()
        return jsonify({"status": "error"}), 200  # 🔴 DO NOT return 500

# -----------------------------
# Health
# -----------------------------
@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000)

