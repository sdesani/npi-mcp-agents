"""Entry point. Same env vars and same demo query as loop.py, for comparison.

    MCP_SERVER_URL=http://localhost:8000 OLLAMA_MODEL=qwen2.5:3b python -m agents.main
"""

from __future__ import annotations

import asyncio
import os

from agents.mcp_client import MCPClient
from agents.orchestrator import Orchestrator

DEMO_QUERY = ("I need to refer a patient to an orthopedic surgeon in Kansas. "
              "Find options and tell me which are eligible.")


async def main() -> None:
    server_url = os.environ.get("MCP_SERVER_URL", "").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    if not server_url:
        raise SystemExit("Set MCP_SERVER_URL, e.g. MCP_SERVER_URL=http://localhost:8000")

    print(f"MCP server: {server_url}   model: {model}\n")
    orchestrator = Orchestrator(MCPClient(server_url), model)
    await orchestrator.run(DEMO_QUERY)


if __name__ == "__main__":
    asyncio.run(main())
