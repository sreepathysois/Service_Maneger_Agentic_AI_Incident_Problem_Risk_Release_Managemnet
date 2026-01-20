import json
import re
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# -----------------------------
# Configuration
# -----------------------------
OLLAMA_URL = "http://172.16.18.235:11434/api/generate"
OLLAMA_MODEL = "mistral"

REQUEST_TIMEOUT = 120  # seconds (first token can be slow)

# -----------------------------
# Helpers
# -----------------------------
def extract_json(text: str) -> dict:
    """
    Extract the first valid JSON object from LLM output.
    This is REQUIRED for Ollama safety.
    """
    if not text or not text.strip():
        raise ValueError("Empty LLM response")

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON found in LLM response: {text}")

    return json.loads(match.group())


def decide_with_ollama(
    incident_id: int,
    severity: str,
    service: str,
    root_cause: str,
    problem_id=None
) -> dict:
    """
    Deterministic decision logic via Ollama
    """

    prompt = f"""
You are an SRE decision agent.

Incident Details:
- Incident ID: {incident_id}
- Severity: {severity}
- Service: {service}
- Problem ID: {problem_id}

Root Cause Analysis:
{root_cause}

Decision Rules:
- AUTO_REMEDIATE only if safe and reversible
- MANUAL_ACTION if human approval required
- NO_ACTION if informational or expected

Return ONLY valid JSON.
NO explanation outside JSON.

JSON format:
{{
  "decision": "AUTO_REMEDIATE | MANUAL_ACTION | NO_ACTION",
  "confidence": 0-100,
  "recommended_actions": ["..."],
  "reason": "short explanation"
}}
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        },
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    payload = response.json()
    raw_text = payload.get("response", "")

    print("\n🔎 RAW OLLAMA OUTPUT:\n", raw_text, "\n")

    return extract_json(raw_text)


# -----------------------------
# API
# -----------------------------
@app.route("/decide", methods=["POST"])
def decide():
    data = request.json or {}

    try:
        result = decide_with_ollama(
            incident_id=data.get("incident_id"),
            severity=data.get("severity", "P3"),
            service=data.get("service"),
            root_cause=(data.get("root_cause") or "")[:500],  # truncate
            problem_id=data.get("problem_id")
        )

        return jsonify({
            "incident_id": data.get("incident_id"),
            "decision": result.get("decision", "NO_ACTION"),
            "confidence": result.get("confidence", 0),
            "recommended_actions": result.get("recommended_actions", []),
            "reason": result.get("reason", "")
        })

    except Exception as e:
        # 🔴 HARD FAIL-SAFE (never break pipeline)
        return jsonify({
            "incident_id": data.get("incident_id"),
            "decision": "NO_ACTION",
            "confidence": 0,
            "recommended_actions": [],
            "reason": f"Ollama decision failed: {str(e)}"
        })


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
    print("🤖 Decision Agent started (Ollama-native)")
    app.run(host="0.0.0.0", port=9000)

