# npi-mcp

An MCP server that wraps the public [NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api)
(version 2.1, no authentication required) and exposes it as MCP tools.

## Tools

| Tool | Use when |
| --- | --- |
| `search_provider(first_name, last_name, state, limit)` | You have a name and/or 2-letter state but no NPI. |
| `lookup_by_npi(npi_number)` | You have the 10-digit NPI and want the full record. |
| `get_specialties(npi_number)` | You have the NPI and only need the specialty list. |

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
- NPPES matches individuals on `last_name` and organizations on
  `organization_name`, and ANDs its query parameters. When `last_name` is given
  without `first_name`, the client issues both queries concurrently and merges
  the deduplicated results.
