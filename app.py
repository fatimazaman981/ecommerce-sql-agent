"""Flask API and static host for the SQL agent (see main.py for the CLI equivalent)."""

import logging

from flask import Flask, jsonify, request, send_from_directory

from config import FLASK_DEBUG, MAX_HISTORY_MESSAGES, PORT, PROJECT_ROOT
from llm_client import LLMClient
from mcp_client import call_tool, is_toolbox_reachable

# Anchored to the project root: a relative path would follow the working
# directory app.py was launched from, and crash outright where it isn't writable.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(PROJECT_ROOT / "agent.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("sql-agent")

# chat.html is served from here, so the UI is same-origin: no CORS, no hardcoded API host.
app = Flask(__name__, static_folder=None)
client = LLMClient()


def ask(question: str, history: list[dict]) -> tuple[str, str | None]:
    """Run the agent's tool-calling loop, capturing the last SQL it actually executed."""
    executed_sql: list[str] = []

    def tracking_executor(name: str, arguments: dict):
        result = call_tool(name, arguments)
        if name == "execute-sql":  # recorded only once the call has succeeded
            executed_sql.append(arguments.get("sql", ""))
        return result

    messages = history[-MAX_HISTORY_MESSAGES:] + [{"role": "user", "content": question}]
    answer = client.run_with_tools(messages, tool_executor=tracking_executor)
    return answer, (executed_sql[-1] if executed_sql else None)


@app.get("/")
def index():
    return send_from_directory(app.root_path, "chat.html")


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
    except Exception as exc:
        log.exception("ask failed: %r", question)
        return jsonify(answer=None, sql=None, error=f"{type(exc).__name__}: {exc}"), 500

    log.info("ask ok: question=%r sql=%r", question, sql)
    return jsonify(answer=answer, sql=sql, error=None)


@app.get("/health")
def health_endpoint():
    if is_toolbox_reachable():
        return jsonify(status="ok", toolbox="reachable")
    return jsonify(status="degraded", toolbox="unreachable"), 503


if __name__ == "__main__":
    app.run(port=PORT, debug=FLASK_DEBUG)
