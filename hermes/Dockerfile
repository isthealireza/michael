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
# The migrations, and the role script they assume ran first. Without these the
# image carries `michael migrate` but nothing for it to apply: the command can
# only ever report, and it reports every applied migration as "(not on disk)"
# and drifted, because the ledger has rows and the directory has no files.
# Measured on the deployed container before this line existed. A migration
# that cannot be applied where the database lives is not a migration.
COPY db ./db

RUN uv venv /opt/michael/.venv --python 3.13 \
    && uv pip install --python /opt/michael/.venv/bin/python "/opt/michael[mcp]" \
    && /opt/michael/.venv/bin/python -c "import michael, mcp; print('michael', michael.__version__)"

# Ingestion writes originals and draft templates; both must be writable by the
# unprivileged user the supervision tree drops to.
RUN mkdir -p /opt/michael/sources /opt/michael/templates/drafts \
    && chown -R 10000:10000 /opt/michael

# NOTE: deleting the bundled headless Chromium here does NOT shrink the image.
# Layers are additive, so an rm in a later layer leaves the bytes in the base
# layer and only adds another. The uncompressed image stays ~4.07GB; the
# compressed size that registries transfer is ~1.0GB. If Railway rejects the
# image on size, the fix is a slimmer base image, not a delete here.

# Rendered at boot from the environment, for hosts where no .env exists.
COPY hermes/config.template.yaml hermes/render_runtime_config.py /opt/michael/hermes/
COPY hermes/railway-entrypoint.sh /usr/local/bin/railway-entrypoint.sh
RUN chmod +x /usr/local/bin/railway-entrypoint.sh

WORKDIR /opt/data

# The wrapper renders config, then execs /init - s6-overlay's PID 1 - which
# must stay first in the chain.
ENTRYPOINT ["/usr/local/bin/railway-entrypoint.sh"]
CMD ["gateway", "run"]
