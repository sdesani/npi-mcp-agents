"""Async client for the public NPPES NPI Registry API (version 2.1, no auth)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from npi_mcp.models import AddressRecord, ProviderRecord, SpecialtyRecord

logger = logging.getLogger(__name__)

NPPES_BASE_URL = "https://npiregistry.cms.hhs.gov/api/"
NPPES_API_VERSION = "2.1"

TIMEOUT_SECONDS = 10.0
MAX_CONNECTIONS = 20

# Shared at module level so every tool call reuses the same connection pool.
_http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(TIMEOUT_SECONDS),
    limits=httpx.Limits(max_connections=MAX_CONNECTIONS, max_keepalive_connections=MAX_CONNECTIONS),
    headers={"Accept": "application/json", "User-Agent": "npi-mcp/0.1.0"},
    follow_redirects=True,
)


def get_http_client() -> httpx.AsyncClient:
    """Return the process-wide httpx client used for all upstream calls."""
    return _http_client


async def close_http_client() -> None:
    """Close the shared client. Called from the FastAPI lifespan shutdown hook."""
    if not _http_client.is_closed:
        await _http_client.aclose()


class NPPESError(RuntimeError):
    """Raised when NPPES rejects a request or is unreachable."""


class NPPESClient:
    """Thin async wrapper over the NPPES NPI Registry search endpoint.

    NPPES exposes a single search endpoint; a single-NPI lookup is just a
    search with the ``number`` parameter set.
    """

    def __init__(self, client: httpx.AsyncClient | None = None, base_url: str = NPPES_BASE_URL) -> None:
        self._client = client or get_http_client()
        self._base_url = base_url

    # ---------------------------------------------------------------- public

    async def search(
        self,
        *,
        first_name: str | None = None,
        last_name: str | None = None,
        state: str | None = None,
        limit: int = 20,
    ) -> list[ProviderRecord]:
        """Search providers by name and/or state. Returns normalized records.

        NPPES matches individuals on ``last_name`` and organizations on
        ``organization_name`` -- and it ANDs its parameters, so one request can
        never match both. When the caller supplies a surname with no given
        name, the term is equally likely to be an organization, so we issue
        both queries concurrently and merge the results, individuals first.
        """
        base: dict[str, Any] = {"limit": limit}
        if state:
            base["state"] = state.upper()

        individual = dict(base)
        if first_name:
            individual["first_name"] = first_name
        if last_name:
            individual["last_name"] = last_name

        queries = [self._get(individual)]
        if last_name and not first_name:
            queries.append(self._get({**base, "organization_name": last_name}))

        payloads = await asyncio.gather(*queries)

        records: list[ProviderRecord] = []
        seen: set[str] = set()
        for payload in payloads:
            for raw in payload.get("results", []):
                record = self._to_provider(raw)
                if record.npi_number in seen:
                    continue
                seen.add(record.npi_number)
                records.append(record)
        return records[:limit]

    async def search_by_taxonomy(
        self,
        *,
        taxonomy_search_term: str,
        state: str | None = None,
        last_name: str | None = None,
        limit: int = 50,
    ) -> list[ProviderRecord]:
        """Search providers by taxonomy description, narrowed by state/name.

        NPPES filters on ``taxonomy_description`` as a text match, which is
        broader than a code match; callers are expected to apply the exact
        code filter to the returned records.
        """
        params: dict[str, Any] = {"taxonomy_description": taxonomy_search_term, "limit": limit}
        if state:
            params["state"] = state.upper()
        if last_name:
            params["last_name"] = last_name

        payload = await self._get(params)
        return [self._to_provider(raw) for raw in payload.get("results", [])]

    async def lookup(self, npi_number: str) -> ProviderRecord | None:
        """Fetch a single provider by NPI. Returns None when nothing matches."""
        payload = await self._get({"number": npi_number, "limit": 1})
        results = payload.get("results", [])
        if not results:
            return None
        return self._to_provider(results[0])

    # --------------------------------------------------------------- private

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {"version": NPPES_API_VERSION, **params}
        try:
            response = await self._client.get(self._base_url, params=query)
            response.raise_for_status()
            payload = response.json()
        except httpx.TimeoutException as exc:
            raise NPPESError(f"NPPES request timed out after {TIMEOUT_SECONDS:g}s") from exc
        except httpx.HTTPStatusError as exc:
            raise NPPESError(
                f"NPPES returned HTTP {exc.response.status_code} for the request"
            ) from exc
        except httpx.HTTPError as exc:
            raise NPPESError(f"Could not reach NPPES: {exc}") from exc
        except ValueError as exc:  # malformed JSON body
            raise NPPESError("NPPES returned a response that was not valid JSON") from exc

        if not isinstance(payload, dict):
            raise NPPESError("NPPES returned an unexpected response shape")

        # NPPES reports validation problems with HTTP 200 and an "Errors" key.
        errors = payload.get("Errors")
        if errors:
            details = "; ".join(
                str(err.get("description", err)) if isinstance(err, dict) else str(err) for err in errors
            )
            raise NPPESError(f"NPPES rejected the query: {details}")

        return payload

    @staticmethod
    def _to_specialty(raw: dict[str, Any]) -> SpecialtyRecord:
        return SpecialtyRecord.model_validate(
            {
                "code": raw.get("code"),
                "desc": raw.get("desc"),
                "taxonomy_group": raw.get("taxonomy_group") or None,
                "primary": bool(raw.get("primary", False)),
                "state": raw.get("state"),
                "license": raw.get("license"),
            }
        )

    @classmethod
    def _to_provider(cls, raw: dict[str, Any]) -> ProviderRecord:
        basic: dict[str, Any] = raw.get("basic") or {}
        addresses = [
            AddressRecord.model_validate(addr)
            for addr in (raw.get("addresses") or [])
            if isinstance(addr, dict)
        ]
        specialties = [
            cls._to_specialty(tax) for tax in (raw.get("taxonomies") or []) if isinstance(tax, dict)
        ]
        enumeration_type = raw.get("enumeration_type")
        if enumeration_type not in ("NPI-1", "NPI-2"):
            enumeration_type = None

        return ProviderRecord(
            npi_number=str(raw.get("number", "")),
            enumeration_type=enumeration_type,
            first_name=basic.get("first_name"),
            last_name=basic.get("last_name"),
            middle_name=basic.get("middle_name"),
            credential=basic.get("credential"),
            organization_name=basic.get("organization_name") or basic.get("name"),
            # NPPES 2.1 reports this as "sex"; older payloads used "gender".
            gender=basic.get("sex") or basic.get("gender"),
            status=basic.get("status"),
            enumeration_date=basic.get("enumeration_date"),
            last_updated=basic.get("last_updated"),
            sole_proprietor=basic.get("sole_proprietor"),
            addresses=addresses,
            specialties=specialties,
        )
