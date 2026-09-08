"""Multi-agent layer over the NPI MCP server.

Same loop mechanics as loop.py; the difference is that specialist agents each
see only a narrow slice of the server's tools, and an orchestrator that holds no
tools of its own decides which specialists to run.
"""

__all__ = ["MCPClient", "Agent", "AgentResult", "Orchestrator"]
