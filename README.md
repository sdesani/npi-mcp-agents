# npi-mcp

An MCP server that wraps the public [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api)
(version 2.1, no authentication required) and exposes it as MCP tools.

## Tools

| Tool | Use when |
| --- | --- |
| `search_provider(first_name, last_name, state, limit)` | You have a name and/or 2-letter state but no NPI. |
| `lookup_by_npi(npi_number)` | You have the 10-digit NPI and want the full record. |
| `get_specialties(npi_number)` | You have the NPI and only need the specialty list. |
| `validate_npi_format(npi_number)` | Offline checksum check; run before spending a lookup. |
| `check_provider_status(npi_number)` | Composite referral-eligibility judgment. |
| `find_providers_by_specialty(specialty_keyword, state, last_name_hint)` | Plain-English specialty -> providers in a state. |

### Derived tools

Tools 4-6 implement logic NPPES does not provide:

- **`validate_npi_format`** is synchronous and makes no network call. It runs the
  CMS Luhn variant: prepend `80840` to the first 9 digits, run standard Luhn,
  compare with the 10th digit.
- **`check_provider_status`** runs five ordered checks (checksum, existence,
  active status, primary taxonomy, complete practice address) and reports each
  failure as an actionable string in `concerns`.
- **`find_providers_by_specialty`** maps a clinical keyword to NUCC taxonomy
  codes via `taxonomies.py`, queries NPPES, then filters to providers actually
  holding one of those codes.

These three never raise: every failure path -- bad checksum, unknown NPI, NPPES
outage, unrecognized keyword -- returns a structured result the calling model can
read and recover from.

## Run

```bash
pip install -e .
npi-mcp                     # or: uvicorn npi_mcp.server:app
```

- MCP endpoint: `http://127.0.0.1:8000/mcp` (streamable HTTP)
- Health: `http://127.0.0.1:8000/health` -> `{"status","version","uptime_seconds"}`

## Notes

- Requires `mcp<2`: version 2.x renamed `FastMCP` to `MCPServer` and moved it out
  of `mcp.server.fastmcp`.
- `find_providers_by_specialty` is bounded by the NPPES 200-record page cap, so
  in a populous state it returns a sample of matching providers, not a census.
- NPPES matches individuals on `last_name` and organizations on
  `organization_name`, and ANDs its query parameters. When `last_name` is given
  without `first_name`, the client issues both queries concurrently and merges
  the deduplicated results.
