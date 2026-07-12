"""The real generator behind the ``curate`` optional dependency (Anthropic SDK).

This is the ONLY path in palimpsest that calls a model provider. It is imported
LAZILY (never at package import — the SDK import lives inside the function body)
so a default install stays provider-free and ``import palimpsest.curate`` pulls
in no generative library. The Anthropic SDK lives in the ``curate`` optional
group (``pip install 'palimpsest[curate]'``).

⚠ Not covered by the hermetic test suite: every test injects a stub ``generate``.
This path needs network + an API key, so it is verified in production use, not in
CI. Keep the request shape aligned with the current Messages API.
"""

from __future__ import annotations

# The structured shape the producer parses back (claims + gap + confidence). Sent
# as a json_schema output format so the model must return valid, parseable JSON.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "source_refs"],
                "additionalProperties": False,
            },
        },
        "gap": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["claims", "gap", "confidence"],
    "additionalProperties": False,
}


def default_generate(prompt: str, *, model: str = "claude-opus-4-8", max_tokens: int = 4096) -> str:
    """Call the model and return its raw JSON text (the producer parses it).

    ``model`` is the real model invoked and MUST match the provenance ``model``
    the payload records (honesty — ADR-20260706 §결정5). Credentials resolve from
    the environment (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile)."""
    from anthropic import Anthropic  # lazy: only when a real generation runs

    client = Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        output_config={"format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    return next(block.text for block in response.content if block.type == "text")
