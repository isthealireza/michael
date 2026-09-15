# Hermes Agent v0.21.0 with Michael's MCP server available in-container.
#
# Hermes runs stdio MCP servers as subprocesses inside its own container, so the
# command must exist at a container path. Michael is installed into the image
# rather than mounted from Windows, whose .venv holds Windows binaries a Linux
# container cannot execute.
#
# The base image already ships Python 3.13 and uv, so no apt layer is needed.
#
#   docker compose -f hermes/docker-compose.yml up -d --build
FROM nousresearch/hermes-agent:v2026.8.31

USER root
WORKDIR /opt/michael

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY templates ./templates
COPY domains.yaml MICHAEL.md ./

RUN uv venv /opt/michael/.venv --python 3.13 \
    && uv pip install --python /opt/michael/.venv/bin/python "/opt/michael[mcp]" \
    && /opt/michael/.venv/bin/python -c "import michael, mcp; print('michael', michael.__version__)"

# Ingestion writes originals and draft templates; both must be writable by the
# unprivileged user the supervision tree drops to.
RUN mkdir -p /opt/michael/sources /opt/michael/templates/drafts \
    && chown -R 10000:10000 /opt/michael

# Leave ENTRYPOINT alone: /init is s6-overlay's PID 1 and must stay first.
WORKDIR /opt/data
