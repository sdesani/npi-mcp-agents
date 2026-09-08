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
from npi_mcp.models import (
    NPI_NUMBER,
    US_STATE_CODE,
    NPIValidationResult,
    ProviderRecord,
    ProviderStatusReport,
    SpecialtyMatchResult,
    SpecialtyRecord,
)
from npi_mcp.nppes_client import NPPESClient, NPPESError, close_http_client
from npi_mcp.taxonomies import resolve_specialty, supported_keywords
from npi_mcp.validation import validate_npi

logger = logging.getLogger(__name__)

# The MCP sub-app owns the "/mcp" path itself and is mounted at the root, so
# the endpoint is exactly /mcp with no trailing-slash redirect.
mcp = FastMCP(
    name="npi-registry",
    instructions=(
        "Tools for looking up US healthcare providers in the public NPPES NPI Registry. "
        "Use search_provider when you know a name, lookup_by_npi when you already have a "
        "10-digit NPI, and get_specialties when only the provider's specialties matter. "
        "validate_npi_format checks an NPI offline before you spend a lookup on it, "
        "check_provider_status judges referral readiness, and find_providers_by_specialty "
        "turns a plain-English specialty into providers in a given state."
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


@mcp.tool()
def validate_npi_format(npi_number: str) -> NPIValidationResult:
    """Check whether a number is a structurally valid NPI, offline and instantly.

    This performs NO network call. It verifies that the value is exactly 10
    digits and that the final digit matches the CMS Luhn checksum (the '80840'
    prefix variant that CMS uses to issue NPIs).

    Call this BEFORE `lookup_by_npi`, `get_specialties`, or
    `check_provider_status` whenever the NPI came from an unverified source --
    user free text, an OCR scan, a fax, a spreadsheet, or your own inference. A
    mistyped NPI is indistinguishable from an unregistered one once it reaches
    NPPES, so validating first turns a wasted API round trip and an ambiguous
    "not found" into an immediate, specific explanation of what is wrong.

    Parameters:
      npi_number: The candidate NPI as a string, e.g. "1234567893". Surrounding
        whitespace is ignored. Any other non-digit character makes it invalid.

    Returns a result with:
      npi_number: the value as supplied.
      is_valid: True only when the value is 10 digits with a correct check digit.
      check_digit_valid: whether the checksum matched; False when the value was
        too malformed for the check to run at all.
      reason: a human-readable explanation. When the checksum fails, this states
        the check digit that was expected and the corrected NPI it implies, so
        you can suggest the likely intended number.

    A True result proves only that the number is well-formed -- it does NOT mean
    the NPI is registered to a real provider. Confirm existence with
    `lookup_by_npi`. This tool never raises; a malformed input is reported as a
    result with is_valid=False.
    """
    return validate_npi(npi_number)


@mcp.tool()
async def check_provider_status(npi_number: str) -> ProviderStatusReport:
    """Judge whether a provider is ready to receive a patient referral.

    This is a composite check, not raw data. It runs five checks in order and
    reports every problem it finds as an actionable concern:
      1. The NPI passes offline checksum validation (stops here if it does not).
      2. The NPI exists in NPPES, distinguishing "not registered" from "NPPES
         was unreachable" -- these need different follow-up from you.
      3. The enumeration status is 'A' (active), not deactivated.
      4. At least one taxonomy is flagged primary, so the provider's principal
         specialty is actually known.
      5. The practice location has a usable street address: address_1, city,
         and state all present.

    Prefer this over `lookup_by_npi` when the question is a judgment -- "can I
    refer a patient here?", "is this NPI safe to bill against?", "is this record
    complete enough to use?" -- rather than a request for the underlying fields.
    Use `lookup_by_npi` when you want the raw record and will draw your own
    conclusions.

    Parameters:
      npi_number: The provider's 10-digit NPI as a string, e.g. "1234567893".

    Returns a report with:
      npi_number, provider_name, primary_specialty: identity, where known.
      is_eligible_for_referral: True only when every check above passed.
      concerns: one specific message per failed check, empty when eligible.

    This tool never raises. Every failure -- bad checksum, unknown NPI, NPPES
    outage -- comes back as a report with is_eligible_for_referral=False and the
    reason in `concerns`, so you can explain the outcome or retry.
    """
    validation = validate_npi(npi_number)
    if not validation.is_valid:
        return ProviderStatusReport(
            npi_number=npi_number,
            is_eligible_for_referral=False,
            concerns=[f"NPI failed format validation: {validation.reason}"],
        )

    npi = validation.npi_number
    try:
        record = await _client.lookup(npi)
    except NPPESError as exc:
        # An upstream outage is not the same as an unregistered NPI: the caller
        # should retry rather than tell the user the provider does not exist.
        return ProviderStatusReport(
            npi_number=npi,
            is_eligible_for_referral=False,
            concerns=[
                f"Could not verify this NPI because the NPPES registry was unreachable: {exc}. "
                "Eligibility is unknown, not denied -- retry before acting on this result."
            ],
        )

    if record is None:
        return ProviderStatusReport(
            npi_number=npi,
            is_eligible_for_referral=False,
            concerns=[
                f"NPI {npi} has a valid checksum but is not registered in NPPES. Confirm the "
                "number with the provider, or search by name with search_provider."
            ],
        )

    concerns: list[str] = []

    if (record.status or "").upper() != "A":
        reported = record.status or "not reported"
        concerns.append(
            f"Provider enumeration status is '{reported}', not 'A' (active). The NPI may be "
            "deactivated or retired; do not refer until active status is confirmed."
        )

    primary = next((s for s in record.specialties if s.primary), None)
    if primary is None:
        if record.specialties:
            listed = ", ".join(s.description or s.code or "unnamed" for s in record.specialties)
            concerns.append(
                "No taxonomy is flagged as primary, so the provider's principal specialty is "
                f"ambiguous. Declared taxonomies: {listed}. Confirm the specialty directly."
            )
        else:
            concerns.append(
                "Provider has no declared taxonomy in NPPES, so their specialty is unknown. "
                "Confirm the scope of practice before referring."
            )

    location = next(
        (a for a in record.addresses if (a.purpose or "").upper() == "LOCATION"),
        None,
    )
    if location is None:
        concerns.append(
            "No practice location address is on file (only a mailing address, if any). A "
            "referral needs a physical practice address -- confirm where the provider sees "
            "patients."
        )
    else:
        missing = [
            label
            for label, value in (
                ("street address", location.address_1),
                ("city", location.city),
                ("state", location.state),
            )
            if not (value or "").strip()
        ]
        if missing:
            concerns.append(
                "Practice location address is incomplete -- missing "
                f"{', '.join(missing)}. Obtain the full address before sending a referral."
            )

    return ProviderStatusReport(
        npi_number=npi,
        provider_name=record.display_name,
        primary_specialty=(primary.description if primary else None),
        is_eligible_for_referral=not concerns,
        concerns=concerns,
    )


@mcp.tool()
async def find_providers_by_specialty(
    specialty_keyword: str,
    state: US_STATE_CODE,
    last_name_hint: str = "",
) -> SpecialtyMatchResult:
    """Find providers in a state who practice a given specialty, by plain-English name.

    NPPES itself has no concept of "a cardiologist" -- it only understands NUCC
    taxonomy codes. This tool supplies that translation: it maps an everyday
    clinical keyword to the set of taxonomy codes that count as that specialty,
    queries NPPES, then keeps only providers who actually hold one of those
    codes.

    Use this when you know WHAT KIND of provider is needed but not who: "find a
    pediatrician in Kansas", "which dermatologists practice in Missouri". Use
    `search_provider` instead when you have a name, and `get_specialties` when
    you have an NPI and want that one provider's specialties.

    Parameters:
      specialty_keyword: A plain-English specialty. Recognized keywords are
        cardiology, orthopedic, pediatrics, neurology, family medicine,
        psychiatry, dermatology, and oncology. Common variants ("cardiologist",
        "primary care", "cancer", "mental health") also resolve. Matching is
        case-insensitive.
      state: A TWO-LETTER US state or territory code -- "MO", "KS", "CA". Never
        a full state name. Required, because a nationwide specialty search would
        match far too many providers to be useful.
      last_name_hint: Optional surname to narrow the search when you have a
        partial name, e.g. "Nguyen". Leave as "" to search the whole state.

    Returns a result with:
      matched_taxonomy_codes: the codes the keyword resolved to.
      providers: matching provider records, each with their specialties.
      result_count and message: a human-readable summary.

    If the keyword is not recognized, this returns an EMPTY result whose message
    lists every supported keyword -- it does not raise, so re-call it with one of
    the listed keywords. Upstream failures are reported the same way. An empty
    provider list means no match in that state, which is a real answer, not an
    error.
    """
    resolved = resolve_specialty(specialty_keyword)
    if resolved is None:
        return SpecialtyMatchResult(
            specialty_keyword=specialty_keyword,
            state=state.upper() if state else None,
            message=(
                f"'{specialty_keyword}' is not a recognized specialty keyword. Supported "
                f"keywords are: {', '.join(supported_keywords())}. Re-run this tool with one "
                "of those, or use search_provider if you are looking for a specific person."
            ),
        )

    canonical, mapping = resolved
    codes = set(mapping.codes)
    hint = last_name_hint.strip() or None

    try:
        candidates = await _client.search_by_taxonomy(
            taxonomy_search_term=mapping.search_term,
            state=state,
            last_name=hint,
            limit=200,
        )
    except NPPESError as exc:
        return SpecialtyMatchResult(
            specialty_keyword=specialty_keyword,
            matched_taxonomy_codes=sorted(codes),
            state=state.upper(),
            message=(
                f"Could not search NPPES for '{canonical}' providers because the registry was "
                f"unreachable: {exc}. No conclusion can be drawn about availability -- retry."
            ),
        )

    matches = [p for p in candidates if any(s.code in codes for s in p.specialties)]

    scope = f"in {state.upper()}"
    if hint:
        scope += f" with last name matching '{hint}'"

    if not matches:
        message = (
            f"No providers {scope} hold a taxonomy code for '{canonical}'. NPPES returned "
            f"{len(candidates)} nearby candidate(s) that did not match the exact codes. Try a "
            "neighboring state, drop last_name_hint, or use a broader keyword."
        )
    else:
        message = (
            f"Found {len(matches)} provider(s) {scope} holding a '{canonical}' taxonomy code, "
            f"out of {len(candidates)} candidate(s) NPPES returned."
        )

    return SpecialtyMatchResult(
        specialty_keyword=specialty_keyword,
        matched_taxonomy_codes=sorted(codes),
        state=state.upper(),
        providers=matches,
        result_count=len(matches),
        message=message,
    )


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
