---
title: NPI Registry MCP Server
emoji: 🩺
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
app_port: 8000
---

# NPI Registry MCP Server

An MCP (Model Context Protocol) server that wraps the public
[NPPES NPI Registry API](https://npiregistry.cms.hhs.gov/api) — the US registry
of healthcare providers — and exposes it to LLM clients as callable tools. No
API key is required; NPPES is a free public endpoint.

Beyond straight API passthrough, the server adds a derived-intelligence layer:
offline NPI checksum validation, a composite referral-eligibility assessment,
and plain-English specialty search that NPPES itself cannot answer.

## Tools

| # | Tool | What it does |
| --- | --- | --- |
| 1 | `search_provider(first_name, last_name, state, limit)` | Finds providers by name and/or 2-letter state. The only way to turn a name into an NPI. Searches individual surnames and organization names. |
| 2 | `lookup_by_npi(npi_number)` | Returns the complete NPPES record for one 10-digit NPI: name, credential, status, addresses, specialties. |
| 3 | `get_specialties(npi_number)` | Returns just the NUCC taxonomies for an NPI — code, description, primary flag, license state. |
| 4 | `validate_npi_format(npi_number)` | Offline CMS Luhn checksum validation, no network call. Run it before spending a lookup on unverified input; it names the expected check digit when validation fails. |
| 5 | `check_provider_status(npi_number)` | Composite referral-eligibility judgment across five checks: checksum, registry existence, active status, primary taxonomy, and a complete practice address. Returns actionable concerns. |
| 6 | `find_providers_by_specialty(specialty_keyword, state, last_name_hint)` | Maps a plain-English specialty — cardiology, orthopedic, pediatrics, neurology, family medicine, psychiatry, dermatology, oncology — to NUCC taxonomy codes, then returns matching providers in a state. |

Tools 4–6 never raise. Every failure path — bad checksum, unknown NPI, NPPES
outage, unrecognized keyword — returns a structured result the calling model can
read and recover from.

## Run with Docker

```bash
docker build -t npi-mcp .
docker run --rm -p 8000:8000 npi-mcp
curl http://localhost:8000/health
```

## Run locally

```bash
pip install -e .
npi-mcp                     # or: uvicorn npi_mcp.server:app
```

## Configuration

| Variable | Purpose |
| --- | --- |
| `MCP_ALLOWED_HOSTS` | Comma-separated public hostnames this server is reached by, e.g. `my-space.hf.space,my-tunnel.trycloudflare.com`. |

FastMCP enables DNS-rebinding protection by default and answers **421 Misdirected
Request** to any `Host` header it was not told about. `localhost` and `127.0.0.1`
(with any port) are always allowed, so local development needs no configuration;
set `MCP_ALLOWED_HOSTS` for any other hostname. Each entry `H` is added to
`allowed_hosts`, and `https://H` and `http://H` to `allowed_origins`. Entries may
be given as bare hostnames or full URLs. The resolved list is logged at startup.

```bash
docker run --rm -p 8000:8000 \
  -e MCP_ALLOWED_HOSTS="my-space.hf.space" npi-mcp
```

## Endpoints

| Path | Purpose |
| --- | --- |
| `/mcp` | MCP streamable-HTTP endpoint. |
| `/health` | Liveness probe: status, version, uptime. |
| `/` | Root probe returning server name, version, and MCP endpoint path. |

## Deployment notes

- **Hugging Face Spaces:** the frontmatter above selects the Docker SDK and
  routes traffic to `app_port: 8000`. Push this repo to a Space and it builds
  and serves unchanged. Set `MCP_ALLOWED_HOSTS` to the Space hostname
  (`<user>-<space>.hf.space`) in the Space's variables, or requests fail with 421.
- **Cloudflare tunnel:** `cloudflared tunnel --url http://localhost:8000` puts
  the same container behind a public hostname. The server binds `0.0.0.0`, so the
  only per-target difference is `MCP_ALLOWED_HOSTS` -- which is why the hostname
  is read at runtime rather than baked into the image, since a quick tunnel gets
  a new hostname on every run.

## Implementation notes

- Requires `mcp<2`: version 2.x renamed `FastMCP` to `MCPServer` and moved it
  out of `mcp.server.fastmcp`.
- `find_providers_by_specialty` is bounded by the NPPES 200-record page cap, so
  in a populous state it returns a sample of matching providers, not a census.
- NPPES matches individuals on `last_name` and organizations on
  `organization_name`, and ANDs its query parameters. When `last_name` is given
  without `first_name`, the client issues both queries concurrently and merges
  the deduplicated results.
