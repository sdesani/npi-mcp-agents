FROM python:3.11-slim

# Keep the image lean and logs unbuffered so Spaces/tunnel logs stream live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# uv, copied from its official distroless image (no pip bootstrap needed).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Dependency layer: only pyproject.toml is copied, so editing src/ below does
# not invalidate this layer. Runtime dependencies only -- no dev extras.
COPY pyproject.toml ./
RUN uv pip install --system --no-cache -r pyproject.toml

# Application layer: changes here rebuild only from this point on. README.md is
# copied here rather than above because pyproject declares it as the project
# readme -- the build needs it, but editing it must not bust the deps layer.
COPY README.md ./
COPY src/ ./src/
RUN uv pip install --system --no-cache --no-deps .

# Hugging Face Spaces runs containers as a non-root user; matching that here
# keeps behavior identical locally and on Spaces.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 0.0.0.0 so the port is reachable from outside the container, whether that is
# the Spaces proxy or a local Cloudflare tunnel.
CMD ["uvicorn", "npi_mcp.server:app", "--host", "0.0.0.0", "--port", "8000"]
