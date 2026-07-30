"""HTTP client for the MCP toolbox server (toolbox.exe serve), used to execute SQL tools."""

import itertools
import json
import os
import re

import requests

TOOLBOX_URL = os.getenv("TOOLBOX_URL", "http://127.0.0.1:5000/mcp")

_ids = itertools.count(1)

# execute-sql runs arbitrary SQL against a real database (toolbox reports it with
# destructiveHint: true), so writes are blocked here rather than trusting the model.
_READ_ONLY_STARTS = ("select", "with", "explain", "show")
_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|merge|vacuum|replace|into)\b",
    re.IGNORECASE,
)


class ToolboxError(RuntimeError):
    """Raised when the MCP toolbox server returns a JSON-RPC error."""


def _check_read_only(sql: str) -> None:
    statements = [s for s in sql.split(";") if s.strip()]
    if len(statements) != 1:
        raise ToolboxError("execute-sql only allows a single read-only statement")

    statement = statements[0].strip()
    if not statement.lower().startswith(_READ_ONLY_STARTS):
        raise ToolboxError("execute-sql is restricted to read-only queries (SELECT/WITH/EXPLAIN/SHOW)")

    if _WRITE_KEYWORDS.search(statement):
        raise ToolboxError("execute-sql rejected: statement contains a write/DDL keyword")


def is_toolbox_reachable(timeout: float = 3) -> bool:
    """Check whether the MCP toolbox server is up, without raising."""
    try:
        requests.post(
            TOOLBOX_URL,
            json={"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}},
            timeout=timeout,
        )
        return True
    except requests.exceptions.ConnectionError:
        return False


def call_tool(name: str, arguments: dict) -> list:
    """Call an MCP tool (list-tables, list-relationships, execute-sql, ...) and return its rows."""
    if name == "execute-sql":
        _check_read_only(arguments.get("sql", ""))

    response = requests.post(
        TOOLBOX_URL,
        json={
            "jsonrpc": "2.0",
            "id": next(_ids),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    if "error" in payload:
        raise ToolboxError(payload["error"]["message"])

    result = payload["result"]
    if result.get("isError"):
        raise ToolboxError("; ".join(item["text"] for item in result["content"]))

    return [json.loads(item["text"]) for item in result["content"]]


if __name__ == "__main__":
    print(call_tool("list-tables", {}))
