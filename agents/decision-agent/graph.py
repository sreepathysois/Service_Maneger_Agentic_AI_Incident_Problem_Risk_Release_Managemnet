import json
from typing import TypedDict, List, Optional

from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage


# -----------------------------
# Decision State
# -----------------------------
class DecisionState(TypedDict):
    incident_id: int
    service: str
    severity: str
    root_cause: str
    problem_id: Optional[int]

    decision: Optional[str]
    confidence: Optional[float]
    recommended_actions: Optional[List[str]]
    reason: Optional[str]


# -----------------------------
# LLM
# -----------------------------
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.0,          # deterministic for ops
    max_tokens=400
)


# -----------------------------
# Decision Node
# -----------------------------
def decision_node(state: DecisionState) -> DecisionState:
    system_prompt = """
You are an AIOps Decision Agent.

You MUST return ONLY valid JSON.
Do NOT return markdown.
Do NOT add commentary.

Allowed decisions:
- AUTO_REMEDIATE
- ESCALATE
- NO_ACTION

Required JSON schema:
{
  "decision": "AUTO_REMEDIATE | ESCALATE | NO_ACTION",
  "confidence": number between 0.0 and 1.0,
  "recommended_actions": list of strings,
  "reason": non-empty string
}
"""

    human_prompt = f"""
Incident ID: {state['incident_id']}
Service: {state['service']}
Severity: {state['severity']}
Problem ID: {state.get('problem_id')}

Root Cause:
{state['root_cause']}

Decide the best action.
"""

    # 🔍 DEBUG PROMPT
    print("\n===== DECISION AGENT PROMPT =====")
    print(human_prompt)
    print("================================\n")

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_prompt)
    ])

    # 🔍 DEBUG RAW RESPONSE
    print("\n===== DECISION AGENT RAW RESPONSE =====")
    print(response.content)
    print("======================================\n")

    try:
        result = json.loads(response.content)

        state["decision"] = result["decision"]
        state["confidence"] = float(result["confidence"])
        state["recommended_actions"] = result.get("recommended_actions", [])
        state["reason"] = result["reason"]

    except Exception as e:
        print("❌ JSON PARSE ERROR:", str(e))

        # HARD fallback (never silent)
        state["decision"] = "NO_ACTION"
        state["confidence"] = 0.0
        state["recommended_actions"] = []
        state["reason"] = (
            "Decision agent failed to produce valid JSON. "
            "Manual review required."
        )

    return state


# -----------------------------
# Graph Builder
# -----------------------------
def build_graph():
    graph = StateGraph(DecisionState)

    graph.add_node("decision", decision_node)

    graph.set_entry_point("decision")
    graph.add_edge("decision", END)

    return graph.compile()

