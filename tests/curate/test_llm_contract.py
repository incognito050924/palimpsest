"""Config drift guard — the Messages-API request shape `default_generate` constructs.

``_llm.py`` is the ONE provider-touching path and is NOT covered by the hermetic suite
(every other test injects a stub ``generate``). A silent Anthropic SDK drift (renamed
param, changed output-format shape) would only surface in production. This pins the
request shape WITHOUT network/key by injecting a fake ``anthropic`` module that captures
the ``messages.create`` kwargs — so a shape change breaks CI, not a live run.

Hermetic: a fake ``anthropic`` is placed in ``sys.modules`` before the lazy import runs
(the SDK is an OPTIONAL ``curate`` dependency and is intentionally absent here — its
absence is itself the isolation invariant the other probes assert)."""

import sys

import pytest

from palimpsest.curate._llm import _RESPONSE_SCHEMA, default_generate

_RESPONSE_JSON = '{"claims": [], "gap": "none", "confidence": 0.5}'


class _FakeMessages:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)

        class _Block:
            type = "text"
            text = _RESPONSE_JSON

        class _Resp:
            content = [_Block()]

        return _Resp()


class _FakeAnthropic:
    captured: dict = {}

    def __init__(self, *a, **k):
        self.messages = _FakeMessages(_FakeAnthropic.captured)


@pytest.fixture
def fake_anthropic(monkeypatch):
    import types

    mod = types.ModuleType("anthropic")
    mod.Anthropic = _FakeAnthropic
    _FakeAnthropic.captured = {}
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return _FakeAnthropic


def test_default_generate_builds_current_messages_api_shape(fake_anthropic):
    """The request carries the pinned model, an int max_tokens, the json_schema output
    format bound to _RESPONSE_SCHEMA, and a single user message — and the raw text block
    is returned verbatim for the producer to parse."""
    out = default_generate("summarise X", model="claude-opus-4-8", max_tokens=1234)
    assert out == _RESPONSE_JSON

    cap = fake_anthropic.captured
    assert cap["model"] == "claude-opus-4-8"
    assert isinstance(cap["max_tokens"], int) and cap["max_tokens"] == 1234
    # the structured-output contract: json_schema format bound to the response schema
    fmt = cap["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"] is _RESPONSE_SCHEMA
    # a single user-role message carrying the prompt
    assert cap["messages"] == [{"role": "user", "content": "summarise X"}]


def test_default_generate_model_defaults_to_pinned_model(fake_anthropic):
    """The default model is the pinned provenance model (must match what the payload
    records — honesty, ADR-20260706 §결정5)."""
    default_generate("p")
    assert fake_anthropic.captured["model"] == "claude-opus-4-8"
