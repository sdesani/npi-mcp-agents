"""The two specialists. Each sees only the tools its job needs."""

from __future__ import annotations

import os

from agents.base import Agent

MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")

DISCOVERY = Agent(
    name="DISCOVERY",
    system_prompt=(
        "You are a provider discovery agent. Your only job is to FIND candidate "
        "healthcare providers using your search tools.\n"
        "- Use find_providers_by_specialty when given a specialty and a state. State must be a "
        "2-letter code (Kansas is KS, Missouri is MO).\n"
        "- Use search_provider when given a person's name.\n"
        "- Return ONLY a compact list of candidates, at most 5, each as: NPI - NAME.\n"
        "- Do NOT assess eligibility, licensure, or quality. That is another agent's job.\n"
        "- Do NOT write prose, greetings, advice, or explanation for an end user. "
        "Output the list and nothing else."
    ),
    tool_allowlist=["search_provider", "find_providers_by_specialty"],
    model=MODEL,
)

VERIFICATION = Agent(
    name="VERIFICATION",
    system_prompt=(
        "You are a referral verification agent. You are given candidate NPI numbers and must "
        "determine whether each is eligible to receive a patient referral.\n"
        "- You MUST call check_provider_status on EACH candidate NPI before saying anything "
        "about that provider. The context you were given contains NO eligibility information -- "
        "it is only a list of candidates. Answering from context alone is a failure.\n"
        "- Call the tool first. Only after you have tool results may you write your report.\n"
        "- Report each provider's concerns VERBATIM from the tool output. Do not paraphrase, "
        "soften, or summarize a concern.\n"
        "- If a tool returns an error or an NPI is not found, say exactly that. NEVER guess, "
        "infer, or fill in an eligibility answer the tools did not give you.\n"
        "- Report per NPI: the name, eligible yes/no, and any concerns."
    ),
    tool_allowlist=[
        "validate_npi_format",
        "lookup_by_npi",
        "check_provider_status",
        "get_specialties",
    ],
    model=MODEL,
)

SPECIALISTS = {agent.name: agent for agent in (DISCOVERY, VERIFICATION)}
