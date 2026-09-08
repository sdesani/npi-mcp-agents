"""MCP transport: one session, shared by every agent."""

from __future__ import annotations

import json

import httpx

HTTP_TIMEOUT = 120.0
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class MCPClient:
    """Talks JSON-RPC to an MCP server over streamable HTTP.

    The session is opened lazily on first use and reused, so all agents share
    one connection to the server rather than each doing their own handshake.
    """

    def __init__(self, server_url: str) -> None:
        self.server_url = server_url.rstrip("/")
        self._session_id: str | None = None
        self._tool_cache: list[dict] | None = None

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        """Read a JSON-RPC reply that may arrive as SSE rather than plain JSON."""
        lines = [ln for ln in response.text.splitlines() if ln.startswith("data:")]
        return json.loads(lines[-1][5:].strip()) if lines else response.json()

    async def _rpc(self, method: str, params: dict | None = None) -> dict:
        """Send one JSON-RPC call, opening an MCP session on first use."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            if self._session_id is None:
                # Streamable HTTP rejects any call before initialize with 400.
                handshake = await client.post(f"{self.server_url}/mcp", headers=HEADERS, json={
                    "jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "npi-agents", "version": "1.0"}}})
                handshake.raise_for_status()
                self._session_id = handshake.headers["mcp-session-id"]
                await client.post(
                    f"{self.server_url}/mcp",
                    headers={**HEADERS, "mcp-session-id": self._session_id},
                    json={"jsonrpc": "2.0", "method": "notifications/initialized"})

            reply = await client.post(
                f"{self.server_url}/mcp",
                headers={**HEADERS, "mcp-session-id": self._session_id},
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
            reply.raise_for_status()
            return self._decode(reply)

    async def get_mcp_tools(self) -> list[dict]:
        """Fetch the server's tools and translate them into Ollama's tool format."""
        if self._tool_cache is None:
            payload = await self._rpc("tools/list")
            self._tool_cache = [{"type": "function", "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("inputSchema", {}),
            }} for tool in payload["result"]["tools"]]
        return self._tool_cache

    async def tools_for_ollama(self, allowlist: list[str] | None = None) -> list[dict]:
        """Return the converted tools, narrowed to `allowlist` when given.

        This is the whole mechanism behind specialist agents: an agent that
        cannot see a tool cannot call it, so scope is enforced here rather than
        left to the model's judgment. A None allowlist means every tool.
        """
        tools = await self.get_mcp_tools()
        if allowlist is None:
            return tools
        permitted = set(allowlist)
        return [t for t in tools if t["function"]["name"] in permitted]

    async def call_mcp_tool(self, name: str, arguments: dict) -> str:
        """Run one tool and return its text. Errors come back as text, never raised."""
        try:
            payload = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        except httpx.HTTPError as exc:
            return f"Tool call failed -- the MCP server was unreachable: {exc}"

        if "error" in payload:
            return f"Tool error: {payload['error'].get('message', payload['error'])}"

        result = payload.get("result", {})
        text = "\n".join(
            block.get("text", "") for block in result.get("content", []) if block.get("type") == "text")
        # Tools returning only structured output have no text blocks; serialize those.
        return text or json.dumps(result.get("structuredContent", result))
