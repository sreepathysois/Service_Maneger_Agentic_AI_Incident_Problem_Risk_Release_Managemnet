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
# OpenProject Update (LOCK SAFE)
# -----------------------------
def update_incident_with_rca(incident_id: int, rca: str):
    wp = requests.get(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        timeout=10
    )
    wp.raise_for_status()

    work_package = wp.json()
    lock_version = work_package.get("lockVersion", 0)
    existing_desc = work_package.get("description", {}).get("raw", "")

    payload = {
        "lockVersion": lock_version,
        "description": {
            "raw": (
                f"{existing_desc}\n\n"
                f"---\n\n"
                f"### 🧠 Root Cause Analysis\n"
                f"{rca}"
            )
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
        summary = data.get("summary", "")

        # 1️⃣ Run RCA
        rca = determine_root_cause(service)

        # 2️⃣ Update Incident with RCA
        update_incident_with_rca(incident_id, rca)

        # 3️⃣ Ask LangGraph Decision Agent
        decision_resp = requests.post(
            DECISION_AGENT_URL,
            json={
                "incident_id": incident_id,
                "severity": severity,
                "service": service,
                "root_cause": rca,
                "problem_id": problem_id
            },
            timeout=8
        )
        decision_resp.raise_for_status()
        decision = decision_resp.json().get("decision", "NO_ACTION")

        # 4️⃣ Notify
        notify_mattermost(
            f"🧠 **RCA Completed**\n"
            f"• Incident ID: {incident_id}\n"
            f"• Service: {service}\n"
            f"• Decision: {decision}\n\n"
            f"Root Cause:\n{rca}"
        )

        return jsonify({
            "status": "completed",
            "incident_id": incident_id,
            "root_cause": rca,
            "decision": decision
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

