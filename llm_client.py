"""Groq-backed LLM client with tool-calling, wired for the MCP toolbox's SQL tools."""

import json
import os
from typing import Any, Callable, Optional

from dotenv import load_dotenv
from groq import Groq

from mcp_client import call_tool

load_dotenv()

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

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
            "name": "list-relationships",
            "description": "Lists foreign key relationships between tables",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute-sql",
            "description": "Executes a raw SQL query against the database",
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
    "You are a SQL analyst. Before writing a query involving tables you haven't already "
    "inspected in this conversation, call list-tables and list-relationships to confirm "
    "real column/table names and foreign keys instead of guessing. Text filters should be "
    "case-insensitive (use ILIKE) unless the user specifies exact case."
)


class LLMClient:
    """Thin wrapper around the Groq chat completions API with tool-calling support."""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL):
        self.client = Groq(api_key=api_key or os.environ["GROQ_API_KEY"])
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
        max_turns: int = 5,
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

        for _ in range(max_turns):
            message = self.chat(messages, tools=tools)

            if not message.tool_calls:
                return message.content

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
                arguments = json.loads(call.function.arguments or "{}")
                try:
                    result = tool_executor(call.function.name, arguments)
                except Exception as exc:  # let the model see the error and retry
                    result = {"error": str(exc)}
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(result),
                    }
                )

        raise RuntimeError("max_turns exceeded without a final answer")


if __name__ == "__main__":
    client = LLMClient()
    answer = client.run_with_tools(
        [{"role": "user", "content": "How many tables are in the database, and what are they called?"}]
    )
    print(answer)
