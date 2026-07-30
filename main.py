"""CLI entry point for the SQL agent."""

import sys

from llm_client import LLMClient
from mcp_client import TOOLBOX_URL, is_toolbox_reachable


def check_toolbox() -> None:
    """Fail fast with a clear message if the MCP toolbox server isn't reachable."""
    if not is_toolbox_reachable():
        sys.exit(
            f"Could not reach the MCP toolbox at {TOOLBOX_URL}.\n"
            "Start it first with: ./toolbox.exe --config=toolBox/tools.yaml serve --port=5000"
        )


def ask(client: LLMClient, question: str) -> str:
    return client.run_with_tools([{"role": "user", "content": question}])


def main() -> None:
    check_toolbox()
    client = LLMClient()

    question = " ".join(sys.argv[1:])
    if question:
        print(ask(client, question))
        return

    print("SQL agent ready. Ask a question about the database (Ctrl+C to exit).")
    while True:
        try:
            question = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if question:
            print(ask(client, question))


if __name__ == "__main__":
    main()
