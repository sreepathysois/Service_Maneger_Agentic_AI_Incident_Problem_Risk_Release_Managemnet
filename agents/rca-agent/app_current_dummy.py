import os
import time
import base64
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL")

# 🧠 Decision Agent (LangGraph)
DECISION_AGENT_URL = os.getenv(
    "DECISION_AGENT_URL",
    "http://decision-agent:9000/decide"
)

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
# Loki Query Helper
# -----------------------------
def query_loki(service: str, event: str, minutes: int = 5):
    end = int(time.time() * 1_000_000_000)
    start = end - (minutes * 60 * 1_000_000_000)

    query = f'{{service="{service}", event="{event}"}}'

    resp = requests.get(
        f"{LOKI_URL}/loki/api/v1/query_range",
        params={
            "query": query,
            "start": str(start),
            "end": str(end),
            "limit": 20
        },
        timeout=10
    )

    resp.raise_for_status()
    return resp.json()["data"]["result"]

# -----------------------------
# RCA Logic
# -----------------------------
def determine_root_cause(service: str) -> str:
    try:
        if service == "payment":
            logs = query_loki("payment", "payment_failure")
            if logs:
                return (
                    "Payment failures observed in application logs.\n"
                    "Likely root cause: Payment gateway timeout or downstream dependency failure."
                )

        if service == "ecommerce":
            logs = query_loki("ecommerce", "page_visit")
            if logs:
                return (
                    "High volume of Orders page visits detected.\n"
                    "Likely root cause: Traffic spike due to promotion or user surge."
                )

    except Exception as e:
        return f"RCA analysis failed while querying logs: {str(e)}"

    return "No clear root cause identified from logs."

# -----------------------------
# OpenProject Helpers
# -----------------------------
def get_work_package(incident_id: int):
    resp = requests.get(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        timeout=10
    )
    resp.raise_for_status()
    return resp.json()

def update_work_package_description(incident_id: int, new_block: str):
    wp = get_work_package(incident_id)
    lock_version = wp.get("lockVersion", 0)
    existing_desc = wp.get("description", {}).get("raw", "")

    payload = {
        "lockVersion": lock_version,
        "description": {
            "raw": f"{existing_desc}\n\n---\n\n{new_block}"
        }
    }

    requests.patch(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        json=payload,
        timeout=10
    ).raise_for_status()

def close_incident(incident_id: int):
    wp = get_work_package(incident_id)
    lock_version = wp.get("lockVersion", 0)

    payload = {
        "lockVersion": lock_version,
        "_links": {
            "status": {"href": "/api/v3/statuses/5"}  # Closed
        }
    }

    requests.patch(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        json=payload,
        timeout=10
    ).raise_for_status()

# -----------------------------
# Mattermost Notify
# -----------------------------
def notify_mattermost(message: str):
    if not MATTERMOST_WEBHOOK_URL:
        return

    requests.post(
        MATTERMOST_WEBHOOK_URL,
        json={"text": message},
        timeout=5
    )

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

        # 1️⃣ RCA
        rca = determine_root_cause(service)

        update_work_package_description(
            incident_id,
            f"### 🧠 Root Cause Analysis\n{rca}"
        )

        # 🔔 RCA ALERT (THIS WAS MISSING)
        notify_mattermost(
            f"🧠 **RCA Completed**\n"
            f"• Incident ID: {incident_id}\n"
            f"• Service: {service}\n\n"
            f"Root Cause:\n{rca}"
        )

        # 2️⃣ Decision Agent
        decision_resp = requests.post(
            DECISION_AGENT_URL,
            json={
                "incident_id": incident_id,
                "service": service,
                "severity": severity,
                "root_cause": rca,
                "problem_id": problem_id
            },
            timeout=10
        )
        decision_resp.raise_for_status()
        decision_payload = decision_resp.json()

        decision = decision_payload.get("decision", "NO_ACTION")
        confidence = decision_payload.get("confidence", 0)
        actions = decision_payload.get("recommended_actions", [])
        reason = decision_payload.get("reason", "")

        # 3️⃣ Notify Decision
        notify_mattermost(
            f"🤖 **Decision Agent Verdict**\n"
            f"• Decision: {decision}\n"
            f"• Confidence: {confidence}\n"
            f"• Recommended Actions: {', '.join(actions) if actions else 'None'}\n\n"
            f"Reason:\n{reason}"
        )

        # 4️⃣ Dummy Remediation
        remediation_status = "skipped"
        if decision == "AUTO_REMEDIATE":
            time.sleep(2)
            remediation_status = "success"

            update_work_package_description(
                incident_id,
                "### 🛠 Remediation Executed\n"
                "Dummy remediation completed successfully.\n"
                "Service assumed to be stabilized."
            )

            close_incident(incident_id)

            notify_mattermost(
                f"🛠 **Remediation Executed**\n"
                f"• Incident ID: {incident_id}\n"
                f"• Status: SUCCESS\n\n"
                f"✅ Incident Closed Automatically"
            )

        return jsonify({
            "status": "completed",
            "incident_id": incident_id,
            "decision": decision,
            "confidence": confidence,
            "remediation": remediation_status
        })

    except Exception as e:
        print("RCA ERROR:", str(e))
        return jsonify({
            "status": "error",
            "message": str(e)
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
    app.run(host="0.0.0.0", port=6000)

