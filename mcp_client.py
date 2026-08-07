"""HTTP client for the MCP toolbox server (toolbox.exe serve).

Also owns the read-only boundary: every execute-sql statement is validated here,
regardless of what the model intended, and capped to a bounded number of rows.
"""

import itertools
import json
import re

import requests

from config import SQL_MAX_ROWS, TOOLBOX_URL

_ids = itertools.count(1)

_READ_ONLY_STARTS = ("select", "with", "explain", "show")

# Comments and quoted literals are blanked before validation. Without this, a
# ';' or a keyword inside a literal ("ILIKE '%delete%'") is rejected as a write,
# and conversely a leading comment can hide the real opening keyword.
_COMMENTS_AND_LITERALS = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"", re.DOTALL
)

_WRITE_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|call|"
    r"merge|vacuum|replace|into|refresh|lock|nextval|setval|dblink|pg_sleep|"
    r"pg_read_file|pg_ls_dir|lo_import|lo_export)\b",
    re.IGNORECASE,
)

_HAS_LIMIT = re.compile(r"\blimit\b", re.IGNORECASE)


class ToolboxError(RuntimeError):
    """Raised when a tool call is rejected locally or fails on the toolbox server."""


def sanitize_sql(sql: str) -> str:
    """Validate `sql` as a single read-only statement and return it row-capped.

    Raises ToolboxError if the statement is not allowed to run.
    """
    masked = _COMMENTS_AND_LITERALS.sub(" ", sql)

    statements = [s for s in masked.split(";") if s.strip()]
    if len(statements) != 1:
        raise ToolboxError("execute-sql allows exactly one statement")

    statement = statements[0].strip().lower()
    if not statement.startswith(_READ_ONLY_STARTS):
        raise ToolboxError("execute-sql is restricted to SELECT/WITH/EXPLAIN/SHOW queries")

    keyword = _WRITE_KEYWORDS.search(statement)
    if keyword:
        raise ToolboxError(f"execute-sql rejected: write/DDL keyword '{keyword.group()}'")

    if statement.startswith(("select", "with")) and not _HAS_LIMIT.search(statement):
        return f"SELECT * FROM (\n{sql.strip().rstrip(';')}\n) AS _capped LIMIT {SQL_MAX_ROWS}"
    return sql


def is_toolbox_reachable(timeout: float = 3) -> bool:
    """Check whether the MCP toolbox server is up, without raising."""
    try:
        return requests.post(TOOLBOX_URL, json=_rpc("tools/list", {}), timeout=timeout).ok
    except requests.RequestException:
        return False


def call_tool(name: str, arguments: dict) -> list:
    """Call an MCP tool (list-tables, describe-table, execute-sql, ...) and return its rows."""
    if name == "execute-sql":
        arguments = {**arguments, "sql": sanitize_sql(arguments.get("sql", ""))}

    params = {"name": name, "arguments": arguments}
    try:
        response = requests.post(TOOLBOX_URL, json=_rpc("tools/call", params), timeout=30)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ToolboxError(f"toolbox request failed: {exc}") from exc

    if "error" in payload:
        raise ToolboxError(payload["error"].get("message", "unknown toolbox error"))

    result = payload.get("result") or {}
    items = [item.get("text", "") for item in result.get("content", [])]
    if result.get("isError"):
        raise ToolboxError("; ".join(items) or "tool call failed")

    return [_maybe_json(text) for text in items]


def _rpc(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": next(_ids), "method": method, "params": params}


def _maybe_json(text: str):
    """Toolbox rows arrive as JSON text; plain-text messages are passed through."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


if __name__ == "__main__":
    print(call_tool("list-tables", {}))
