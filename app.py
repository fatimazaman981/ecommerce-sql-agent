"""Flask HTTP API wrapping the SQL agent (see main.py for the CLI equivalent)."""

from flask import Flask, jsonify, request
from flask_cors import CORS

from llm_client import LLMClient
from mcp_client import call_tool, is_toolbox_reachable

app = Flask(__name__)
CORS(app)

client = LLMClient()


def ask(question: str, history: list[dict]) -> tuple[str, str | None]:
    """Run the agent's tool-calling loop, capturing the last SQL statement it executed."""
    executed_sql: list[str] = []

    def tracking_executor(name: str, arguments: dict):
        if name == "execute-sql":
            executed_sql.append(arguments.get("sql", ""))
        return call_tool(name, arguments)

    messages = history + [{"role": "user", "content": question}]
    answer = client.run_with_tools(messages, tool_executor=tracking_executor)
    return answer, (executed_sql[-1] if executed_sql else None)


@app.post("/ask")
def ask_endpoint():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in body.get("history", [])
        if m.get("role") in ("user", "assistant") and m.get("content")
    ]
    if not question:
        return jsonify(answer=None, sql=None, error="Missing 'question' in request body"), 400

    try:
        answer, sql = ask(question, history)
        return jsonify(answer=answer, sql=sql, error=None)
    except Exception as exc:
        return jsonify(answer=None, sql=None, error=str(exc)), 500


@app.get("/health")
def health_endpoint():
    if is_toolbox_reachable():
        return jsonify(status="ok", toolbox="reachable")
    return jsonify(status="degraded", toolbox="unreachable"), 503


if __name__ == "__main__":
    app.run(port=8000, debug=True)
