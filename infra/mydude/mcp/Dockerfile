# MyDude — Azure MCP Dev Accelerator container
# Build from the REPO ROOT (the image needs src/ + infra/ + the lockfile):
#   docker build -f infra/mydude/mcp/Dockerfile -t <registry>/mydude-azure-mcp:<tag> .
#
# Runs the governed FastMCP streamable-HTTP server (src/mcp/azure_dev_server.py)
# as a VNet-internal Azure Container App (ingress external:false). The bearer
# token is sourced from Key Vault at runtime under the container's user-assigned
# identity — it is NEVER baked into the image (governance pillar #3).
FROM python:3.11-slim

# Pin the Bicep CLI for reproducible builds; override with --build-arg if needed.
ARG BICEP_VERSION=v0.30.23

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    PYTHONPATH=/app \
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \
    AZURE_MCP_HOST=0.0.0.0 \
    AZURE_MCP_PORT=8080

WORKDIR /app

# ca-certificates: outbound TLS to ARM / Key Vault. curl: fetch the Bicep binary.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Bicep CLI — the governed two-phase deploy tools (plan/apply) compile
# infra/mydude/bicep/main.bicep at runtime. Self-contained binary; the
# globalization-invariant env above avoids the ICU/libicu dependency.
RUN curl -fsSL "https://github.com/Azure/bicep/releases/download/${BICEP_VERSION}/bicep-linux-x64" \
        -o /usr/local/bin/bicep \
    && chmod +x /usr/local/bin/bicep \
    && DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 /usr/local/bin/bicep --version

RUN pip install --no-cache-dir uv

# Dependency layer from the committed lockfile (cached until deps change).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Only the code the governed MCP server needs: the server + backends + the
# governance stack (src/) and the Azure wiring seam + bicep templates (infra/).
# .dockerignore strips __pycache__, tests, node_modules, agent metadata, etc.
COPY src/ ./src/
COPY infra/mydude/ ./infra/mydude/

RUN chmod +x ./infra/mydude/mcp/entrypoint.sh

EXPOSE 8080

# Unauthenticated liveness probe the server serves at /healthz.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/healthz' % os.environ.get('AZURE_MCP_PORT','8080'), timeout=4)" || exit 1

ENTRYPOINT ["./infra/mydude/mcp/entrypoint.sh"]
CMD ["python", "-m", "src.mcp.azure_dev_server"]
