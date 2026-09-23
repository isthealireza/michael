# gate/Dockerfile — michael-gate, the only service on the public internet.
FROM python:3.13-slim

WORKDIR /opt/michael
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY web ./web

RUN pip install --no-cache-dir uv \
    && uv pip install --system "/opt/michael[gate]"

COPY gate/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Never root. The gate parses attacker-controlled input by design.
RUN useradd --uid 10000 --no-create-home gate
USER 10000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
