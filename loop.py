"""A minimal agentic loop: a local Ollama model driving the NPI MCP server.

The model gets the server's tools, calls them as it sees fit, and reads the
results back until it can answer. Point MCP_SERVER_URL at any deployment --
localhost, a Space, a Cloudflare tunnel -- without editing this file:
    MCP_SERVER_URL=http://localhost:8000 OLLAMA_MODEL=qwen2.5:3b python loop.py
"""

import asyncio
import json
import os

import httpx
import ollama

MAX_ITERATIONS = 6
HTTP_TIMEOUT = 120.0
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
MCP_SERVER_URL = OLLAMA_MODEL = ""
_session_id: str | None = None


def _decode(response: httpx.Response) -> dict:
    """Read a JSON-RPC reply that may arrive as SSE rather than plain JSON."""
    lines = [ln for ln in response.text.splitlines() if ln.startswith("data:")]
    return json.loads(lines[-1][5:].strip()) if lines else response.json()


async def _rpc(method: str, params: dict | None = None) -> dict:
    """Send one JSON-RPC call, opening an MCP session on first use."""
    global _session_id
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        if _session_id is None:
            # Streamable HTTP rejects any call before initialize with 400.
            handshake = await client.post(f"{MCP_SERVER_URL}/mcp", headers=HEADERS, json={
                "jsonrpc": "2.0", "id": 0, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "npi-loop", "version": "1.0"}}})
            handshake.raise_for_status()
            _session_id = handshake.headers["mcp-session-id"]
            await client.post(f"{MCP_SERVER_URL}/mcp", headers={**HEADERS, "mcp-session-id": _session_id},
                              json={"jsonrpc": "2.0", "method": "notifications/initialized"})

        reply = await client.post(f"{MCP_SERVER_URL}/mcp",
                                  headers={**HEADERS, "mcp-session-id": _session_id},
                                  json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        reply.raise_for_status()
        return _decode(reply)


async def get_mcp_tools() -> list[dict]:
    """Fetch the server's tools and translate them into Ollama's tool format."""
    payload = await _rpc("tools/list")
    return [{"type": "function", "function": {
        "name": tool["name"],
        "description": tool.get("description", ""),
        "parameters": tool.get("inputSchema", {}),
    }} for tool in payload["result"]["tools"]]


async def call_mcp_tool(name: str, arguments: dict) -> str:
    """Run one tool and return its text. Errors come back as text, never raised."""
    try:
        payload = await _rpc("tools/call", {"name": name, "arguments": arguments})
    except httpx.HTTPError as exc:
        return f"Tool call failed -- the MCP server was unreachable: {exc}"

    if "error" in payload:
        return f"Tool error: {payload['error'].get('message', payload['error'])}"

    result = payload.get("result", {})
    text = "\n".join(block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
    # Tools returning only structured output have no text blocks; serialize those.
    return text or json.dumps(result.get("structuredContent", result))


async def chat(user_query: str) -> str:
    """Drive the model until it answers, feeding tool results back each round."""
    tools = await get_mcp_tools()
    messages: list[dict] = [{"role": "user", "content": user_query}]
    client = ollama.AsyncClient()

    for iteration in range(1, MAX_ITERATIONS + 1):
        print(f"[MODEL] {OLLAMA_MODEL} (round {iteration}/{MAX_ITERATIONS})")
        reply = await client.chat(model=OLLAMA_MODEL, messages=messages, tools=tools)
        messages.append(reply.message)

        if not reply.message.tool_calls:
            print(f"\n{reply.message.content}")
            return reply.message.content

        for call in reply.message.tool_calls:
            name = call.function.name
            arguments = dict(call.function.arguments or {})
            output = await call_mcp_tool(name, arguments)
            preview = " ".join(output.split())[:120]  # collapse pretty-printed JSON to one line
            print(f"[TOOL] {name}({json.dumps(arguments)}) -> {preview}")
            messages.append({"role": "tool", "name": name, "content": output})

    gathered = [m["content"] for m in messages if isinstance(m, dict) and m.get("role") == "tool"]
    summary = f"Stopped after {MAX_ITERATIONS} rounds without a final answer. Findings so far:\n" + "\n".join(gathered)
    print(f"\n{summary}")
    return summary


async def main() -> None:
    global MCP_SERVER_URL, OLLAMA_MODEL
    MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "").rstrip("/")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    if not MCP_SERVER_URL:
        raise SystemExit("Set MCP_SERVER_URL, e.g. MCP_SERVER_URL=http://localhost:8000")

    print(f"MCP server: {MCP_SERVER_URL}   model: {OLLAMA_MODEL}\n")
    await chat("I need to refer a patient to an orthopedic surgeon in Kansas. "
               "Find options and tell me which are eligible.")


if __name__ == "__main__":
    asyncio.run(main())
