"""Embedding inputs are capped; stored provision text never is."""

from __future__ import annotations

import json

import httpx
import pytest

from michael.config import settings
from michael.embeddings import EmbeddingError, _truncate, embed


def test_short_text_is_untouched() -> None:
    assert _truncate("a short section", 100) == "a short section"


def test_long_text_is_capped() -> None:
    text = "word " * 10_000
    out = _truncate(text, 1_000)
    assert len(out) <= 1_000


def test_the_cut_lands_on_a_word_boundary() -> None:
    out = _truncate("word " * 10_000, 1_000)
    assert not out.endswith("wor")
    assert out.endswith("word") or out.endswith(" ")


def test_text_without_spaces_is_still_capped() -> None:
    """A pathological input with no whitespace must not defeat the cap."""
    out = _truncate("x" * 5_000, 1_000)
    assert len(out) == 1_000


def _client(captured: list[dict[str, object]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        captured.append(payload)
        dim = settings().embedding_dim
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.1] * dim} for i in range(len(payload["input"]))
                ]
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_oversized_provisions_are_capped_before_the_request_is_sent() -> None:
    """The 8192-token limit is enforced before the provider sees the input.

    The cap is a character budget standing in for a token budget, so it has to
    be conservative: dense legal text tokenises at under 3 chars/token.
    """
    captured: list[dict[str, object]] = []
    huge = "section text " * 50_000
    embed([huge], client=_client(captured))

    inputs = captured[0]["input"]
    assert isinstance(inputs, list)
    sent = str(inputs[0])
    assert len(sent) <= settings().embedding_max_chars
    assert len(sent) < len(huge)


def test_a_dimension_mismatch_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    with pytest.raises(EmbeddingError, match="EMBEDDING_DIM"):
        embed(["x"], client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_a_short_response_fails_closed_rather_than_returning_partial_vectors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    with pytest.raises(EmbeddingError, match="one vector per input"):
        embed(["a", "b"], client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_an_oversize_rejection_is_retried_with_a_smaller_budget() -> None:
    """A character cap is only a proxy for a token cap, so the batch shrinks.

    Endnote and amendment tables tokenise at close to one character per token,
    so no fixed character budget is both safe and generous. The client shrinks
    what it sends until the provider accepts it.
    """
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        longest = max(len(t) for t in payload["input"])
        attempts.append(longest)
        if longest > 4_000:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Invalid 'input[52]': maximum input length is 8192 tokens."
                    }
                },
            )
        dim = settings().embedding_dim
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "embedding": [0.1] * dim} for i in range(len(payload["input"]))
                ]
            },
        )

    out = embed(["x" * 60_000], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(out) == 1
    assert len(attempts) > 1, "the oversize rejection was not retried"
    assert attempts == sorted(attempts, reverse=True), "each retry must send less, not more"
    assert attempts[-1] <= 4_000


def test_a_non_length_400_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(400, json={"error": {"message": "invalid model"}})

    with pytest.raises(EmbeddingError, match="HTTP 400"):
        embed(["x"], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(calls) == 1, "a non-length failure must not be retried"


def test_retries_are_bounded() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(
            400, json={"error": {"message": "maximum input length is 8192 tokens"}}
        )

    with pytest.raises(EmbeddingError):
        embed(["x" * 60_000], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(calls) <= 7, "retrying must terminate"
