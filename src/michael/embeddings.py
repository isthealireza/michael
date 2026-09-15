"""Embeddings, via any OpenAI-compatible endpoint.

OpenRouter serves an OpenAI-compatible embeddings endpoint, so this may reuse
the OpenRouter key; it may also point at a local server. If it is unavailable
the caller fails closed rather than quietly retrieving on the lexical arm alone.
"""

from __future__ import annotations

import httpx

from michael.config import settings

TIMEOUT_SECONDS = 60.0
MAX_BATCH = 64

#: How many times to shrink and retry a batch the provider rejects as too
#: long. A character budget is only a proxy for a token budget, and the ratio
#: is not constant: amendment tables and endnotes tokenise at close to one
#: character per token, so a fixed cap cannot be both safe and generous.
OVERSIZE_RETRIES = 5


def _is_too_long(body: str) -> bool:
    """True when a 400 is the provider complaining about input length."""
    lowered = body.lower()
    return "maximum input length" in lowered or "maximum context length" in lowered


def _truncate(text: str, max_chars: int) -> str:
    """Cap one embedding input at the provider's context limit.

    Only the text *sent to the embedder* is capped. The provision stored in the
    database keeps its full, verbatim text, because that is what gets quoted and
    cited; a section longer than the cap is simply embedded from its opening,
    and the BM25 arm still indexes every word of it.

    Cut on a whitespace boundary where one is near, so the input does not end
    mid-word.
    """
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    cut = window.rfind(" ")
    return window[:cut] if cut > max_chars - 200 else window


class EmbeddingError(RuntimeError):
    """The embedding provider was unreachable or returned an unusable body."""


def embed(texts: list[str], *, client: httpx.Client | None = None) -> list[list[float]]:
    """Embed ``texts``, preserving order.

    Raises :class:`EmbeddingError` rather than returning partial results: a
    provision indexed without an embedding would be invisible to the vector arm
    with no signal that anything was wrong.
    """
    if not texts:
        return []

    config = settings()
    config.require_embeddings()

    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    vectors: list[list[float]] = []
    try:
        for start in range(0, len(texts), MAX_BATCH):
            raw = texts[start : start + MAX_BATCH]
            budget = config.embedding_max_chars
            for attempt in range(OVERSIZE_RETRIES + 1):
                batch = [_truncate(text, budget) for text in raw]
                response = client.post(
                    f"{config.embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {config.embedding_api_key}"},
                    json={"model": config.embedding_model, "input": batch},
                )
                if response.status_code == 200:
                    break
                # Shrink and retry only for a length complaint; every other
                # failure is raised immediately rather than retried blindly.
                if (
                    response.status_code == 400
                    and _is_too_long(response.text)
                    and attempt < OVERSIZE_RETRIES
                ):
                    budget = max(500, budget // 2)
                    continue
                raise EmbeddingError(
                    f"embedding provider returned HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )
            payload = response.json()
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(batch):
                raise EmbeddingError("embedding response did not contain one vector per input")
            for item in sorted(data, key=lambda row: int(row.get("index", 0))):
                vector = item.get("embedding")
                if not isinstance(vector, list) or len(vector) != config.embedding_dim:
                    raise EmbeddingError(
                        f"expected {config.embedding_dim}-dimensional vectors; "
                        f"EMBEDDING_DIM does not match {config.embedding_model}"
                    )
                vectors.append([float(v) for v in vector])
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"embedding provider unreachable: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    return vectors


def embed_one(text: str, *, client: httpx.Client | None = None) -> list[float]:
    """Embed a single string."""
    return embed([text], client=client)[0]
