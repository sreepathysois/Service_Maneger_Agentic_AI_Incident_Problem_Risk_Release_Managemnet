import os
import time
import base64
import requests
import traceback
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Config
# -----------------------------
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")
MATTERMOST_WEBHOOK_URL = os.getenv("MATTERMOST_WEBHOOK_URL")
DECISION_AGENT_URL = os.getenv("DECISION_AGENT_URL", "http://decision-agent:9000/decide")
REMEDIATION_AGENT_URL = os.getenv("REMEDIATION_AGENT_URL", "http://remediation-agent:7000/remediate")

basic_auth = base64.b64encode(f"apikey:{OPENPROJECT_API_TOKEN}".encode()).decode()

HEADERS = {
    "Authorization": f"Basic {basic_auth}",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

# -----------------------------
# RCA Logic
# -----------------------------
def determine_root_cause(service):
    if service == "payment":
        return "Payment failures detected. Likely gateway timeout."
    if service == "ecommerce":
        return "Traffic spike due to promotion or user surge."
    return "No clear root cause identified."


def get_wp(wp_id):
    r = requests.get(f"{OPENPROJECT_URL}/work_packages/{wp_id}", headers=HEADERS)
    r.raise_for_status()
    return r.json()


def update_wp(wp_id, block):
    wp = get_wp(wp_id)
    payload = {
        "lockVersion": wp["lockVersion"],
        "description": {
            "raw": f"{wp.get('description',{}).get('raw','')}\n\n---\n\n{block}"
        }
    }
    requests.patch(
        f"{OPENPROJECT_URL}/work_packages/{wp_id}",
        headers=HEADERS,
        json=payload
    ).raise_for_status()


def notify_mm(msg):
    if MATTERMOST_WEBHOOK_URL:
        requests.post(MATTERMOST_WEBHOOK_URL, json={"text": msg}, timeout=5)


# -----------------------------
# RCA API (PROBLEM DRIVEN)
# -----------------------------
@app.route("/rca", methods=["POST"])
def run_rca():
    try:
        data = request.json
        problem_id = data["problem_id"]
        service = data["service"]

        rca = determine_root_cause(service)

        update_wp(problem_id, f"### 🧠 Root Cause Analysis\n{rca}")

        notify_mm(
            f"🧠 **RCA Completed**\n"
            f"Problem ID: {problem_id}\nService: {service}\n\n{rca}"
        )

        decision = requests.post(
            DECISION_AGENT_URL,
            json={
                "problem_id": problem_id,
                "service": service,
                "severity": "P2",
                "root_cause": rca
            },
            timeout=30
        ).json()

        notify_mm(
            f"🤖 **Decision Verdict**\n"
            f"Decision: {decision['decision']}\n"
            f"Confidence: {decision['confidence']}%\n"
            f"Actions: {decision['recommended_actions']}\n\n"
            f"Reason: {decision['reason']}"
        )

        if decision["decision"] == "AUTO_REMEDIATE":
            requests.post(
                REMEDIATION_AGENT_URL,
                json={"problem_id": problem_id, "service": service},
                timeout=20
            )
            notify_mm(f"🛠 Remediation triggered for Problem {problem_id}")

        return jsonify({"status": "ok"})

    except Exception:
        traceback.print_exc()
        return jsonify({"status": "error"}), 200


@app.route("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=6000)

