"""Groq-backed LLM client with tool-calling, wired for the MCP toolbox's SQL tools."""

import json
import logging
import re
from typing import Any, Callable, Optional

from groq import Groq

from config import GROQ_API_KEY, GROQ_MODEL
from mcp_client import call_tool

log = logging.getLogger(__name__)

# Tool schemas mirroring toolBox/tools.yaml, exposed to the model for function calling.
TOOLBOX_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list-tables",
            "description": "Lists all tables in the database",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe-table",
            "description": "Lists the columns, data types and nullability of one table",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {"type": "string", "description": "Name of the table"},
                },
                "required": ["table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list-relationships",
            "description": "Lists foreign key relationships between tables",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute-sql",
            "description": "Executes a read-only SQL query against the database",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "The SQL statement to execute"},
                },
                "required": ["sql"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "You are a SQL analyst answering questions about a Postgres database.\n\n"
    "Schema: you do not know it. Call list-tables, then describe-table on each table "
    "you intend to query, before writing any SQL. Use list-relationships for joins. "
    "Never guess a column name.\n\n"
    "Text filters: stored values are often cased differently from the user's wording "
    "('Delivered' vs 'delivered'), and = would silently match zero rows. Use ILIKE for "
    "every text comparison unless the user asks for exact matching. If a filtered "
    "COUNT/SUM/AVG comes back 0 or NULL, run SELECT DISTINCT on that column to see the "
    "real values before answering.\n\n"
    "Ties: for 'which X is highest/most' questions, order descending without LIMIT 1 and "
    "report every row that shares the top value.\n\n"
    "Answer with the data alone. Mention tables, columns or foreign keys only when the "
    "user asked about the schema itself."
)

# Heuristic backstop: an aggregation that came back 0/NULL while filtering with exact
# match against a quoted string is almost always the case-mismatch bug, not a true zero.
_EXACT_TEXT_MATCH = re.compile(r"=\s*'[^']*'")


def _flag_suspicious_zero(sql: str, result: Any) -> Any:
    """Attach a warning to an all-zero single-row result produced by an exact text match."""
    if not isinstance(result, list) or len(result) != 1:
        return result
    row = result[0]
    if not isinstance(row, dict) or not row:
        return result
    if not all(v is None or v == 0 for v in row.values()):
        return result
    if not _EXACT_TEXT_MATCH.search(sql):
        return result

    return {
        **row,
        "_warning": (
            "This result is 0/NULL and the query used exact match (=) against a quoted "
            "string. The actual column value may have different casing or whitespace — "
            "check with SELECT DISTINCT and retry using ILIKE before reporting this."
        ),
    }


class LLMClient:
    """Thin wrapper around the Groq chat completions API with tool-calling support."""

    def __init__(self, api_key: Optional[str] = None, model: str = GROQ_MODEL):
        key = api_key or GROQ_API_KEY
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set — add it to .env")
        # Fingerprint only, never the key: enough to tell a stale or shadowed value
        # apart from the one in .env when debugging a 401.
        log.info("Groq client: model=%s key=%s...%s (len %d)", model, key[:4], key[-4:], len(key))
        self.client = Groq(api_key=key)
        self.model = model

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.0,
    ):
        """Send messages to the model and return the assistant's response message."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message

    def run_with_tools(
        self,
        messages: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Any] = call_tool,
        tools: list[dict[str, Any]] = TOOLBOX_TOOLS,
        max_turns: int = 8,
    ) -> str:
        """
        Run a chat loop that lets the model call tools until it produces a final answer.

        `tool_executor(tool_name, arguments)` is invoked for each tool call the model
        requests (e.g. dispatched to the MCP toolbox) and must return a JSON-serializable
        result.
        """
        messages = list(messages)
        if not messages or messages[0].get("role") != "system":
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})

        for turn in range(max_turns):
            # On the last turn the tools are withheld, forcing the model to answer from
            # what it has gathered rather than failing the request outright.
            last_turn = turn == max_turns - 1
            message = self.chat(messages, tools=None if last_turn else tools)

            if last_turn or not message.tool_calls:
                return message.content or "I couldn't answer that from the database."

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": call.type,
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in message.tool_calls
                    ],
                }
            )

            for call in message.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                    result = tool_executor(call.function.name, arguments)
                    if call.function.name == "execute-sql":
                        result = _flag_suspicious_zero(arguments.get("sql", ""), result)
                except Exception as exc:  # let the model see the error and retry
                    result = {"error": str(exc)}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result, default=str),
                    }
                )


if __name__ == "__main__":
    client = LLMClient()
    print(client.run_with_tools([{"role": "user", "content": "What tables exist?"}]))
