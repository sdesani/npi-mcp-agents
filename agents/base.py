"""The Agent: loop.py's loop, scoped to one tool allowlist and one role."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import ollama

from agents.mcp_client import MCPClient

MAX_ITERATIONS = 6


@dataclass
class AgentResult:
    """What one agent produced, and how much work it took to get there."""

    agent_name: str
    output: str
    tool_calls_made: list[dict] = field(default_factory=list)
    iterations: int = 0
    hit_limit: bool = False


@dataclass
class Agent:
    """A model, a system prompt, and a narrow slice of the MCP tool surface."""

    name: str
    system_prompt: str
    tool_allowlist: list[str]
    model: str = "qwen2.5:3b"

    async def run(self, task: str, context: dict | None = None, *, client: MCPClient) -> AgentResult:
        """Run the loop.py loop with only this agent's tools available."""
        tools = await client.tools_for_ollama(self.tool_allowlist)
        messages: list = [{"role": "system", "content": self.system_prompt}]
        if context:
            messages.append({"role": "user", "content": _format_context(context)})
        messages.append({"role": "user", "content": task})

        chat = ollama.AsyncClient()
        calls_made: list[dict] = []

        for iteration in range(1, MAX_ITERATIONS + 1):
            print(f"[{self.name}][MODEL] {self.model} (round {iteration}/{MAX_ITERATIONS})", flush=True)
            reply = await chat.chat(model=self.model, messages=messages, tools=tools)
            messages.append(reply.message)

            if not reply.message.tool_calls:
                return AgentResult(self.name, reply.message.content or "", calls_made, iteration, False)

            for call in reply.message.tool_calls:
                name = call.function.name
                arguments = dict(call.function.arguments or {})
                output = await client.call_mcp_tool(name, arguments)
                preview = " ".join(output.split())[:120]
                print(f"[{self.name}][TOOL] {name}({json.dumps(arguments)}) -> {preview}", flush=True)
                calls_made.append({"tool": name, "arguments": arguments, "result": output})
                messages.append({"role": "tool", "name": name, "content": output})

        gathered = "\n".join(f"{c['tool']} -> {c['result']}" for c in calls_made)
        summary = (f"Stopped after {MAX_ITERATIONS} rounds without a final answer. "
                   f"Findings so far:\n{gathered}")
        print(f"[{self.name}] hit the {MAX_ITERATIONS}-round limit", flush=True)
        return AgentResult(self.name, summary, calls_made, MAX_ITERATIONS, True)


def _format_context(context: dict) -> str:
    """Render upstream agent results as plain text the next agent can read."""
    parts = []
    for key, value in context.items():
        body = value.output if isinstance(value, AgentResult) else str(value)
        parts.append(f"--- from {key} ---\n{body}")
    return "Context from earlier steps:\n\n" + "\n\n".join(parts)
