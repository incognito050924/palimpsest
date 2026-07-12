"""AC2/AC8 — the isolated generative producer, exercised hermetically.

The LLM is a plain callable injected as ``generate`` (prompt -> raw text), so no
network/key is touched. The producer's job: turn one grounded request + one LLM
response into exactly ONE well-formed payload carrying the honesty form
(grounding + gap + confidence), with honest, non-self-attributed provenance.
"""

import json

import pytest

from palimpsest.curate import CurateRequest, produce
from palimpsest.ir import Summary

TARGET = "com.ecoletree.commute.CommuteController#punchIn()"
KLASS = "com.ecoletree.commute.CommuteController"

REQUEST = CurateRequest(
    target_id=TARGET,
    grounding_ids=(TARGET, KLASS),
    facts="punchIn() records the go-to-work punch; declared in CommuteController.",
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    created_at="2026-07-12T09:00:00+09:00",
    generator="ditto-curator",       # honest: not palimpsest itself
    model="claude-opus-4-8",         # the real generation model
)

# One deterministic LLM response (the mock). Grounds both claims in real ids,
# states a gap, reports a confidence.
LLM_JSON = json.dumps(
    {
        "claims": [
            {"text": "Handles the go-to-work punch-in flow.", "source_refs": [TARGET]},
            {"text": "Coupled to its declaring controller.", "source_refs": [KLASS]},
        ],
        "gap": "Leave/holiday punch variants are not covered.",
        "confidence": 0.82,
    }
)


def test_produce_yields_exactly_one_payload_with_grounding_gap_confidence():
    payload = produce(REQUEST, generate=lambda _prompt: LLM_JSON)

    # exactly one payload (one request -> one summary), a single dict
    assert isinstance(payload, dict)

    # grounding: every claim carries >=1 source_ref, and every ref is a REAL
    # candidate id (no hallucinated ref laundered in)
    assert payload["claims"]
    for claim in payload["claims"]:
        assert claim["source_refs"]
        for ref in claim["source_refs"]:
            assert ref in REQUEST.grounding_ids
    # gap + confidence both present and non-null (the honesty form)
    assert payload["gap"] is not None
    assert payload["confidence"] is not None


def test_produce_payload_is_load_schema_valid():
    """The produced payload round-trips through the EXISTING load schema
    (Summary.from_dict) — the loader is reused unchanged (AC3 bridge)."""
    payload = produce(REQUEST, generate=lambda _prompt: LLM_JSON)
    s = Summary.from_dict(payload)  # must not raise
    assert s.target_id == TARGET
    assert s.generator == "ditto-curator"
    assert s.model == "claude-opus-4-8"
    # content-verdict stays external (E4): the producer never emits one.
    assert s.semantic_verdict is None


def test_produce_records_honest_nonself_provenance():
    """AC8: generator/model name the real external model, never palimpsest."""
    payload = produce(REQUEST, generate=lambda _prompt: LLM_JSON)
    assert payload["generator"] != "palimpsest"
    assert payload["model"] != "palimpsest"


def test_produce_rejects_self_attributed_provenance():
    """AC8 (honesty guard): a payload that claims palimpsest generated it is
    refused — curate must not self-attribute (ADR-20260706 §결정5)."""
    self_attributed = CurateRequest(
        target_id=TARGET,
        grounding_ids=(TARGET,),
        facts="...",
        source_commit="c20b7332",
        created_at="2026-07-12T09:00:00+09:00",
        generator="palimpsest",
        model="claude-opus-4-8",
    )
    with pytest.raises(ValueError):
        produce(self_attributed, generate=lambda _prompt: LLM_JSON)


def test_produce_drops_hallucinated_refs_and_requires_grounding():
    """A ref outside the real candidate set is not laundered in; a summary with
    no surviving grounded claim is refused (never ungrounded prose)."""
    ungrounded = json.dumps(
        {
            "claims": [{"text": "invented", "source_refs": ["com.x.DoesNotExist"]}],
            "gap": "everything",
            "confidence": 0.1,
        }
    )
    with pytest.raises(ValueError):
        produce(REQUEST, generate=lambda _prompt: ungrounded)
