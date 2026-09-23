# gate/Dockerfile — michael-gate, the only service on the public internet.
FROM python:3.13-slim

WORKDIR /opt/michael
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY web ./web

# --frozen: install EXACTLY what uv.lock records, and fail rather than
# silently re-resolve if pyproject.toml and uv.lock have drifted. I7: the
# prior `uv pip install --system "/opt/michael[gate]"` ignored uv.lock
# entirely, so the image running in production was never provably the
# dependency set the test suite ran against (e.g. it could float onto a
# websockets 13.x whose legacy client takes a different keyword argument
# than the one michael.gate.app.relay() passes -- see the `gate` extra's
# comment in pyproject.toml).
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev --extra gate

ENV PATH="/opt/michael/.venv/bin:${PATH}"

COPY gate/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Never root. The gate parses attacker-controlled input by design.
RUN useradd --uid 10000 --no-create-home gate
USER 10000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
