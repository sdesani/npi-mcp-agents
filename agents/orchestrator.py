"""The orchestrator: plans, delegates, and synthesises. Holds no MCP tools."""

from __future__ import annotations

import json
import re

import ollama

from agents.base import AgentResult
from agents.mcp_client import MCPClient
from agents.specialists import SPECIALISTS

PLAN_PROMPT = """You are a planner. Decide which specialist agents to run, and in what order.

Available specialists:
- DISCOVERY: finds candidate providers by specialty+state or by name. Returns NPIs and names.
  It CANNOT judge eligibility.
- VERIFICATION: takes candidate NPIs and determines referral eligibility for each.
  It CANNOT search for providers.

Rules:
- If the request asks to FIND providers, include DISCOVERY.
- If the request asks whether providers are eligible, suitable, or safe to refer to,
  include VERIFICATION after DISCOVERY.
- A request to find providers AND check eligibility needs BOTH, in that order.

Reply with ONLY a JSON object, no prose.

Example request: "Find a cardiologist in Missouri and tell me if they are eligible."
Example reply:
{"steps": [{"agent": "DISCOVERY", "task": "Find cardiologists in Missouri (MO)."},
           {"agent": "VERIFICATION", "task": "Check referral eligibility for each candidate NPI."}]}"""

SYNTHESIS_PROMPT = """You are writing the final answer for the person who asked the question.

You did not gather any of these facts yourself -- two specialist agents did. Every claim you
make must be attributed to the agent that produced it, e.g. "DISCOVERY found ..." or
"VERIFICATION reports ...". Do not introduce any fact neither agent reported. If the agents
disagree or a check failed, say so plainly."""


class Orchestrator:
    """Runs specialists in sequence, feeding each result forward as context.

    It deliberately has no tool access of its own: it can only reason over what
    the specialists report back, which keeps the division of labour honest.
    """

    def __init__(self, client: MCPClient, model: str) -> None:
        self.client = client
        self.model = model
        self.chat = ollama.AsyncClient()

    async def plan(self, request: str) -> list[dict]:
        """Ask the model which specialists to run. Falls back to the standard order."""
        print("[ORCHESTRATOR] planning...", flush=True)
        reply = await self.chat.chat(model=self.model, messages=[
            {"role": "system", "content": PLAN_PROMPT},
            {"role": "user", "content": request},
        ])
        steps = _extract_steps(reply.message.content or "")
        if not steps:
            # A small model may not return usable JSON; the pipeline still has a
            # sensible default rather than failing the whole request.
            print("[ORCHESTRATOR] plan unparseable, using default DISCOVERY -> VERIFICATION", flush=True)
            steps = [
                {"agent": "DISCOVERY", "task": request},
                {"agent": "VERIFICATION", "task": "Check referral eligibility for each candidate NPI above."},
            ]
        print(f"[ORCHESTRATOR] plan: {' -> '.join(s['agent'] for s in steps)}", flush=True)
        return steps

    async def run(self, request: str) -> str:
        """Plan, run each specialist with prior results as context, then synthesise."""
        steps = await self.plan(request)
        context: dict[str, AgentResult] = {}

        for step in steps:
            agent = SPECIALISTS.get(step["agent"])
            if agent is None:
                print(f"[ORCHESTRATOR] skipping unknown agent {step['agent']!r}", flush=True)
                continue
            task = _with_known_npis(step["task"], context)
            print(f"[ORCHESTRATOR] -> {agent.name}: {task}", flush=True)
            result = await agent.run(task, dict(context), client=self.client)
            limit_note = " (hit iteration limit)" if result.hit_limit else ""
            print(f"[{agent.name}] done in {result.iterations} round(s), "
                  f"{len(result.tool_calls_made)} tool call(s){limit_note}", flush=True)
            context[agent.name] = result

        return await self.synthesise(request, context)

    async def synthesise(self, request: str, context: dict[str, AgentResult]) -> str:
        """Write the final answer, attributing each fact to the agent that found it."""
        print("[ORCHESTRATOR] synthesising final answer...", flush=True)
        if not context:
            return "No specialist produced any result, so there is nothing to report."

        reports = "\n\n".join(
            f"--- {name} reported ---\n{result.output}" for name, result in context.items())
        reply = await self.chat.chat(model=self.model, messages=[
            {"role": "system", "content": SYNTHESIS_PROMPT},
            {"role": "user", "content": f"Original request:\n{request}\n\n{reports}"},
        ])
        answer = reply.message.content or ""
        print(f"\n[ORCHESTRATOR] final answer:\n{answer}")
        return answer


def _with_known_npis(task: str, context: dict[str, AgentResult]) -> str:
    """Name the NPIs an earlier agent found directly in the next agent's task.

    Small models reliably act on identifiers stated in the instruction but tend
    to answer from memory when the same NPIs are only present in background
    context -- which is exactly how unverified eligibility claims get invented.
    """
    seen: list[str] = []
    for result in context.values():
        for npi in re.findall(r"\b\d{10}\b", result.output):
            if npi not in seen:
                seen.append(npi)
    if not seen:
        return task
    return (f"{task}\nThe candidate NPIs are: {', '.join(seen)}. "
            f"Call check_provider_status once for each of these {len(seen)} NPIs.")


def _extract_steps(text: str) -> list[dict]:
    """Pull the steps array out of a model reply that may be wrapped in prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    steps = parsed.get("steps") if isinstance(parsed, dict) else None
    if not isinstance(steps, list):
        return []
    return [s for s in steps if isinstance(s, dict) and s.get("agent") in SPECIALISTS and s.get("task")]
