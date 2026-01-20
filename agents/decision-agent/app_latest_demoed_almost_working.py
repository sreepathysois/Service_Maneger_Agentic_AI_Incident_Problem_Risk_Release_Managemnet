import json
import threading
from flask import Flask, request, jsonify
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

app = Flask(__name__)

# -----------------------------
# LLM (Ollama via OpenAI API)
# -----------------------------
llm = ChatOpenAI(
    model="llama3",
    openai_api_base="http://172.16.18.235:11434/v1",
    openai_api_key="ollama",
    temperature=0,
    request_timeout=120
)

# -----------------------------
# LLM Warm-up (Flask 3.x safe)
# -----------------------------
def warmup_llm():
    try:
        llm.invoke("Warm up")
        print("✅ LLM warm-up completed")
    except Exception as e:
        print("⚠️ LLM warm-up failed:", e)

# Run warmup in background (non-blocking)
threading.Thread(target=warmup_llm, daemon=True).start()

# -----------------------------
# Decision Logic
# -----------------------------
def decide_with_llm(incident_id, severity, service, root_cause, problem_id):
    system_prompt = (
        "You are an SRE decision agent.\n"
        "Decide if an incident should be auto-remediated.\n\n"
        "Rules:\n"
        "- AUTO_REMEDIATE only if action is safe and reversible\n"
        "- MANUAL_ACTION if human approval is needed\n"
        "- NO_ACTION if expected or informational\n\n"
        "Return STRICT JSON only."
    )

    user_prompt = (
        f"Incident ID: {incident_id}\n"
        f"Severity: {severity}\n"
        f"Service: {service}\n"
        f"Problem ID: {problem_id}\n\n"
        f"Root Cause (summary): {root_cause}\n\n"
        "Return JSON ONLY:\n"
        "{\n"
        '  "decision": "AUTO_REMEDIATE | MANUAL_ACTION | NO_ACTION",\n'
        '  "confidence": 0-100,\n'
        '  "recommended_actions": ["..."],\n'
        '  "reason": "short explanation"\n'
        "}"
    )

    try:
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])

        raw = response.content.strip()
        return json.loads(raw)

    except Exception as e:
        # Absolute fail-safe
        return {
            "decision": "NO_ACTION",
            "confidence": 0,
            "recommended_actions": [],
            "reason": f"Decision agent fallback: {str(e)}"
        }

# -----------------------------
# API
# -----------------------------
@app.route("/decide", methods=["POST"])
def decide():
    data = request.json

    result = decide_with_llm(
        incident_id=data.get("incident_id"),
        severity=data.get("severity"),
        service=data.get("service"),
        root_cause=(data.get("root_cause") or "")[:200],  # truncate
        problem_id=data.get("problem_id")
    )

    return jsonify({
        "incident_id": data.get("incident_id"),
        **result
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
    app.run(host="0.0.0.0", port=9000)

