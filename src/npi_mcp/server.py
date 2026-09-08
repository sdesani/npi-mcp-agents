"""FastMCP server exposing NPPES NPI Registry lookups as MCP tools.

The MCP endpoint is mounted at ``/mcp`` on a FastAPI app that also serves
``/health``.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from npi_mcp import __version__
from npi_mcp.health import router as health_router
from npi_mcp.models import NPI_NUMBER, US_STATE_CODE, ProviderRecord, SpecialtyRecord
from npi_mcp.nppes_client import NPPESClient, NPPESError, close_http_client

logger = logging.getLogger(__name__)

# The MCP sub-app owns the "/mcp" path itself and is mounted at the root, so
# the endpoint is exactly /mcp with no trailing-slash redirect.
mcp = FastMCP(
    name="npi-registry",
    instructions=(
        "Tools for looking up US healthcare providers in the public NPPES NPI Registry. "
        "Use search_provider when you know a name, lookup_by_npi when you already have a "
        "10-digit NPI, and get_specialties when only the provider's specialties matter."
    ),
    streamable_http_path="/mcp",
)

_client = NPPESClient()


@mcp.tool()
async def search_provider(
    first_name: str | None = None,
    last_name: str | None = None,
    state: US_STATE_CODE | None = None,
    limit: int = 20,
) -> list[ProviderRecord]:
    """Find US healthcare providers in the NPPES NPI Registry by name and/or state.

    Use this tool when you know something about *who* the provider is (a name, a
    state) but you do NOT yet know their NPI number. This is the discovery tool:
    it is the only way to turn a name into an NPI. Once you have an NPI, prefer
    `lookup_by_npi` (full record) or `get_specialties` (specialties only), which
    are exact and return a single provider instead of a candidate list.

    Parameters:
      first_name: The provider's given/first name, e.g. "Maria". Optional. A
        trailing "*" acts as a wildcard, e.g. "Mar*" matches Maria and Marcus.
      last_name: The provider's family/last name, e.g. "Okonkwo". For an
        organization (hospital, clinic, lab), pass the organization name here
        instead, e.g. "Childrens Mercy" -- when last_name is given without a
        first_name, both individual surnames and organization names are
        searched and the results merged. Optional; supports a trailing "*"
        wildcard, which is often needed to match a long legal business name.
      state: A TWO-LETTER US state or territory code — "MO", "KS", "CA", "NY".
        Do NOT pass a full state name such as "Missouri"; NPPES will reject it.
        Restricts results to providers with an address in that state.
      limit: Maximum number of records to return, 1-200. Defaults to 20.

    At least one of first_name, last_name, or state must be supplied, and NPPES
    requires at least two characters for name searches. A state alone can match
    a very large number of providers, so combine it with a name when possible.

    Returns a list of provider records (possibly empty if nothing matched). Each
    record contains the NPI number, whether the NPI is for an individual
    ("NPI-1") or an organization ("NPI-2"), name and credential, status,
    addresses, and declared specialties. Results are candidate matches, not a
    verified single answer — inspect the list before acting on one entry.
    """
    if not any((first_name, last_name, state)):
        raise ValueError(
            "Provide at least one of first_name, last_name, or state. "
            "NPPES cannot run an unconstrained search."
        )

    try:
        return await _client.search(
            first_name=first_name,
            last_name=last_name,
            state=state.upper() if state else None,
            limit=limit,
        )
    except NPPESError as exc:
        raise ValueError(str(exc)) from exc


@mcp.tool()
async def lookup_by_npi(npi_number: NPI_NUMBER) -> ProviderRecord:
    """Fetch the complete NPPES record for one provider using their exact NPI number.

    Use this tool whenever you already have a 10-digit National Provider
    Identifier and want everything NPPES knows about that provider: legal name,
    credential, individual-vs-organization type, active status, enumeration and
    last-updated dates, practice and mailing addresses with phone numbers, and
    all declared specialties.

    Prefer this over `search_provider` when the NPI is known — it is an exact
    lookup that returns exactly one provider rather than a list of candidates.
    Prefer `get_specialties` instead if the only thing you need is the
    provider's specialty/taxonomy list and the address and demographic detail
    would be noise.

    Parameters:
      npi_number: The provider's National Provider Identifier as a 10-digit
        string, e.g. "1234567893". Digits only — no spaces, dashes, or prefix.

    Returns a single provider record. Raises an error if the NPI is not present
    in the NPPES registry, which means the number is invalid, deactivated, or
    mistyped rather than that the provider merely lacks data.
    """
    try:
        record = await _client.lookup(npi_number)
    except NPPESError as exc:
        raise ValueError(str(exc)) from exc

    if record is None:
        raise ValueError(f"No provider found in the NPPES registry for NPI {npi_number}.")
    return record


@mcp.tool()
async def get_specialties(npi_number: NPI_NUMBER) -> list[SpecialtyRecord]:
    """List the specialties (NUCC taxonomies) a provider has declared, by NPI number.

    Use this tool to answer questions about what kind of medicine a provider
    practices — "is this doctor a cardiologist?", "what is this NPI's primary
    specialty?", "which state licenses back this provider's taxonomies?" — when
    you already have the 10-digit NPI.

    This returns a focused subset of what `lookup_by_npi` returns. Prefer this
    tool when only the specialty matters, and prefer `lookup_by_npi` when you
    also need the provider's name, addresses, or status. If you have a name but
    no NPI, call `search_provider` first to obtain the NPI.

    Parameters:
      npi_number: The provider's National Provider Identifier as a 10-digit
        string, e.g. "1234567893". Digits only.

    Returns a list of specialty records, each with the NUCC taxonomy code (e.g.
    "207RC0000X"), a human-readable description (e.g. "Internal Medicine,
    Cardiovascular Disease"), a `primary` flag marking the provider's principal
    specialty, and the state and license number backing that taxonomy. The list
    may be empty for a provider who has declared no taxonomy; that is a valid
    result, not an error. Raises an error if the NPI is not in the registry.
    """
    try:
        record = await _client.lookup(npi_number)
    except NPPESError as exc:
        raise ValueError(str(exc)) from exc

    if record is None:
        raise ValueError(f"No provider found in the NPPES registry for NPI {npi_number}.")
    return record.specialties


def create_app() -> FastAPI:
    """Build the FastAPI application serving /mcp and /health."""
    mcp_app = mcp.streamable_http_app()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The MCP session manager must run for the duration of the app.
        async with mcp.session_manager.run():
            try:
                yield
            finally:
                await close_http_client()

    app = FastAPI(
        title="NPI Registry MCP Server",
        version=__version__,
        description="MCP tools wrapping the public NPPES NPI Registry API.",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    # Mounted last so /health and the OpenAPI routes above take precedence.
    app.mount("/", mcp_app)
    return app


app = create_app()


def main() -> None:
    """Console-script entry point: run the server with uvicorn."""
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run("npi_mcp.server:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
