from flask import Flask, request, jsonify
from graph import build_graph

app = Flask(__name__)
graph = build_graph()

@app.route("/decide", methods=["POST"])
def decide():
    data = request.json

    state = {
        "incident_id": data["incident_id"],
        "severity": data["severity"],
        "service": data["service"],
        "root_cause": data["root_cause"],
        "problem_id": data.get("problem_id"),
        "decision": "NO_ACTION"
    }

    result = graph.invoke(state)

    return jsonify({
        "incident_id": result["incident_id"],
        "decision": result["decision"]
    })

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000)

