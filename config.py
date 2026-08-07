"""Settings, and the one place .env is loaded.

Every module reads its configuration from here rather than calling os.getenv at
import time, so .env is guaranteed to be loaded before any value is read.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

# Explicit path, not dotenv's default discovery: discovery falls back to the
# current working directory under `python -c`, a REPL, or a frozen build, and
# then silently finds nothing.
load_dotenv(PROJECT_ROOT / ".env")

TOOLBOX_URL = os.getenv("TOOLBOX_URL", "http://127.0.0.1:5000/mcp")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Rows a single execute-sql call may return before it is truncated. Uncapped
# SELECTs would otherwise push an entire table into the model's context.
SQL_MAX_ROWS = int(os.getenv("SQL_MAX_ROWS", "500"))

# Prior chat messages replayed to the model per request.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

PORT = int(os.getenv("PORT", "8000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG") == "1"
