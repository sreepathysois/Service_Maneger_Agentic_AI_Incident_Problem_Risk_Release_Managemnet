import os
import base64
import docker
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Docker Client
# -----------------------------
docker_client = docker.from_env()

# -----------------------------
# Config
# -----------------------------
OPENPROJECT_URL = os.getenv("OPENPROJECT_URL", "http://openproject/api/v3")
OPENPROJECT_API_TOKEN = os.getenv("OPENPROJECT_API_TOKEN")

# Map services → containers
SERVICE_CONTAINER_MAP = {
    "ecommerce": "ecommerce-app",
    "payment": "ecommerce-app"
}

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
# OpenProject Helpers
# -----------------------------
def close_incident(incident_id: int):
    wp = requests.get(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS
    ).json()

    payload = {
        "lockVersion": wp["lockVersion"],
        "_links": {
            "status": {"href": "/api/v3/statuses/3"}  # Closed
        }
    }

    requests.patch(
        f"{OPENPROJECT_URL}/work_packages/{incident_id}",
        headers=HEADERS,
        json=payload
    ).raise_for_status()

# -----------------------------
# Remediation Actions
# -----------------------------
def restart_container(container_name: str):
    container = docker_client.containers.get(container_name)
    container.restart()
    return f"Container {container_name} restarted"

# -----------------------------
# API
# -----------------------------
@app.route("/remediate", methods=["POST"])
def remediate():
    data = request.json

    try:
        incident_id = data["incident_id"]
        service = data["service"]
        root_cause = data["root_cause"]
        severity = data["severity"]

        # 🔒 Guardrails
        if severity == "P1":
            return jsonify({"status": "skipped", "reason": "P1 requires approval"})

        container_name = SERVICE_CONTAINER_MAP.get(service)
        if not container_name:
            return jsonify({"status": "no_playbook"})

        # -----------------------------
        # Playbook Logic
        # -----------------------------
        if "traffic" in root_cause.lower():
            action = restart_container(container_name)

        elif "payment" in root_cause.lower():
            action = restart_container(container_name)

        else:
            return jsonify({"status": "no_playbook"})

        # -----------------------------
        # Close Incident
        # -----------------------------
        close_incident(incident_id)

        return jsonify({
            "status": "remediated",
            "incident_id": incident_id,
            "action": action
        })

    except Exception as e:
        return jsonify({
            "status": "failed",
            "error": str(e)
        }), 500

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7000)

