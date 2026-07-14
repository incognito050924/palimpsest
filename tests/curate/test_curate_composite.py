"""facet-3 producer hardening + composite CLI chain (wi_260714ns9 M1/M2 + hardening).

Two concerns, both hermetic (the LLM is a stub — no key, no network, no Neo4j):

* **Synthesis-only guards (M1/M2, F-Q6)** — the producer receives PRE-SELECTED tuples
  (it never selects); its output must stay a grounded OBSERVATION, never a verdict/
  recommendation. So: the free-form ``prompt`` override is gone (no judgment prompt can
  be injected into this flow), generated claims carrying verdict/recommendation
  language are refused, and the honesty form is validated (confidence in [0,1],
  non-empty gap).
* **Composite CLI chain** — ``refactor-candidates`` runs the recall-side deterministic
  composite, produces one payload per tuple with the stubbed generator, and
  ATOMICALLY materialises each to git-SoT (temp + os.replace), idempotently.
"""

import json

import pytest

import palimpsest.cli as cli
import palimpsest.curate as curate_pkg
from palimpsest.cli import main
from palimpsest.curate import CurateRequest, produce
from palimpsest.ir import Summary

TARGET = "com.ecoletree.commute.CommuteController#punchIn()"
KLASS = "com.ecoletree.commute.CommuteController"

BASE = dict(
    target_id=TARGET,
    grounding_ids=(TARGET, KLASS),
    facts="typed_cross_package_calls=2; typed_same_package_calls=0; "
    "typed_unresolved_calls=0; name_resolved_calls=1",
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    created_at="2026-07-12T09:00:00+09:00",
    generator="ditto-curator",
    model="claude-opus-4-8",
)


def _req(**over):
    return CurateRequest(**{**BASE, **over})


def _llm(claims, gap="Only the two counted call kinds are described.", confidence=0.7):
    return lambda _p: json.dumps({"claims": claims, "gap": gap, "confidence": confidence})


# --- M1/M2: the free-form prompt override is gone (no judgment injection) ------

def test_curate_request_has_no_prompt_override():
    """The ``prompt`` field is removed from CurateRequest: this flow cannot be handed a
    judgment-style prompt, so the producer always builds its own neutral synthesis
    prompt (F-Q6 — the LLM synthesises, it is never steered to judge/select)."""
    assert "prompt" not in CurateRequest.__dataclass_fields__


def test_built_prompt_is_neutral_synthesis_not_a_verdict_ask():
    """The payload records the producer-built prompt; it asks for a grounded, NEUTRAL
    description and explicitly forbids recommending/judging (synthesis-only framing)."""
    payload = produce(
        _req(),
        generate=_llm([{"text": "Calls into two packages.", "source_refs": [TARGET]}]),
    )
    prompt = payload["prompt"].lower()
    assert TARGET.lower() in prompt
    assert "neutral" in prompt              # asks for neutral observations
    assert "do not recommend" in prompt     # explicitly forbids a recommendation/verdict
    assert "gap" in prompt and "confidence" in prompt   # the honesty form is requested


# --- M1/M2: verdict/recommendation language in claims is refused --------------

@pytest.mark.parametrize(
    "bad_text",
    [
        "This method should be refactored to reduce coupling.",
        "The class is a code smell and must be split.",
        "Poorly designed: too many cross-package calls.",
        "Recommend extracting the cross-package logic.",
    ],
)
def test_produce_refuses_verdict_language_in_claims(bad_text):
    """A generated claim carrying verdict/recommendation language breaks the
    synthesis-only framing (a candidate is an observation, not a judgment) and is
    refused — the whole payload, so no laundered verdict reaches git-SoT."""
    with pytest.raises(ValueError):
        produce(_req(), generate=_llm([{"text": bad_text, "source_refs": [TARGET]}]))


def test_produce_accepts_neutral_observation_claims():
    """A neutral, grounded observation passes (the guard must not over-reject ordinary
    structural description)."""
    payload = produce(
        _req(),
        generate=_llm(
            [
                {"text": "Makes two typed calls into a different package.", "source_refs": [TARGET]},
                {"text": "Coupled to its declaring controller.", "source_refs": [KLASS]},
            ]
        ),
    )
    assert len(payload["claims"]) == 2


# --- M1/M2: honesty-form input validation ------------------------------------

@pytest.mark.parametrize("bad_conf", [-0.1, 1.5, "high", None])
def test_produce_rejects_confidence_out_of_range(bad_conf):
    with pytest.raises(ValueError):
        produce(
            _req(),
            generate=_llm([{"text": "Neutral fact.", "source_refs": [TARGET]}], confidence=bad_conf),
        )


@pytest.mark.parametrize("bad_gap", ["", "   ", None])
def test_produce_rejects_empty_gap(bad_gap):
    with pytest.raises(ValueError):
        produce(
            _req(),
            generate=_llm([{"text": "Neutral fact.", "source_refs": [TARGET]}], gap=bad_gap),
        )


# --- hermetic composite CLI chain: recall(canned) -> produce -> materialise ----

class _NullDriverCM:
    """A no-op driver context manager — the composite recall is monkeypatched, so the
    command never touches Neo4j in the hermetic tier."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def _candidate_item(target, callee):
    """A canned recall item shaped like recall_refactor_candidates' output."""
    return {
        "id": target,
        "kind": "Method",
        "name": target.rsplit("#", 1)[-1],
        "qualified_name": target,
        "relation": "CALLS",
        "depth": 1,
        "sources": {
            "source_commit": "c20b7332d8c60ce73794427a4c28120b085c134d",
            "path": "src/main/java/x/C.java",
            "start_line": 10,
            "end_line": 40,
            "committed_at": "2025-09-03T16:22:54+09:00",
        },
        "grounding_ids": (target, callee),
        "facts": "typed_cross_package_calls=1; typed_same_package_calls=0; "
        "typed_unresolved_calls=0; name_resolved_calls=1",
    }


def _canned_recall(items, gaps=("standing gap",)):
    return {
        "items": list(items), "sources": [], "summaries": [], "risks": [],
        "decisions": [], "relations": [], "gaps": list(gaps), "confidence": 1.0,
        "expand_handle": None,
    }


RC_ARGV = [
    "refactor-candidates",
    "--generator", "ditto-curator",
    "--model", "claude-opus-4-8",
    "--created-at", "2026-07-12T09:00:00+09:00",
]

RC_LLM = json.dumps(
    {
        "claims": [{"text": "Makes one typed cross-package call.", "source_refs": [TARGET]}],
        "gap": "Only the counted call kinds are described.",
        "confidence": 0.7,
    }
)


def _patch(monkeypatch, recall_result):
    monkeypatch.setattr(cli, "_driver", lambda: _NullDriverCM())
    monkeypatch.setattr(cli, "recall_refactor_candidates", lambda driver, limit: recall_result)
    monkeypatch.setattr(curate_pkg, "default_generate", lambda prompt, **kw: RC_LLM)


def test_composite_cli_materialises_load_schema_valid_payload_per_candidate(tmp_path, monkeypatch):
    """The recall-side composite selects; the producer synthesises; each tuple is frozen
    to git-SoT as a load-schema-valid one-element JSON array (the exact shape `load`
    reads). generate→materialise verified hermetically (no Neo4j, no key)."""
    items = [_candidate_item(TARGET, KLASS)]
    _patch(monkeypatch, _canned_recall(items))
    out = tmp_path / "summaries"
    rc = main([*RC_ARGV, "--out", str(out), "--max", "25"])
    assert rc == 0
    files = sorted(out.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1
    summary = Summary.from_dict(data[0])   # load-schema valid
    assert summary.target_id == TARGET
    assert summary.generator == "ditto-curator" and summary.model == "claude-opus-4-8"
    assert summary.semantic_verdict is None   # no content-verdict frozen


def test_composite_cli_is_idempotent_no_divergent_overwrite(tmp_path, monkeypatch):
    """Re-running the same provenance is a no-op (identical bytes, no second file). A
    DIVERGENT re-run under the same provenance is REFUSED (frozen output, ac-3)."""
    items = [_candidate_item(TARGET, KLASS)]
    _patch(monkeypatch, _canned_recall(items))
    out = tmp_path / "summaries"
    main([*RC_ARGV, "--out", str(out)])
    first = sorted(out.glob("*.json"))[0].read_bytes()

    main([*RC_ARGV, "--out", str(out)])   # identical re-run -> no-op
    files = sorted(out.glob("*.json"))
    assert len(files) == 1 and files[0].read_bytes() == first

    # a DIVERGENT generation for the SAME provenance is refused
    divergent = json.dumps(
        {"claims": [{"text": "A different neutral fact.", "source_refs": [TARGET]}],
         "gap": "x", "confidence": 0.5}
    )
    monkeypatch.setattr(curate_pkg, "default_generate", lambda prompt, **kw: divergent)
    with pytest.raises(ValueError):
        main([*RC_ARGV, "--out", str(out)])
    # the original file is untouched (atomic refusal, no torn write)
    assert sorted(out.glob("*.json"))[0].read_bytes() == first


def test_composite_cli_empty_prints_gaps_writes_nothing(tmp_path, monkeypatch, capsys):
    """0 composite candidates (the documented precision-resolved corpus): the command
    prints the honest gaps and writes NO file — never a silent all-clear, never a stray
    empty payload."""
    _patch(monkeypatch, _canned_recall([], gaps=("no composite; corpus may be precision-resolved",)))
    out = tmp_path / "summaries"
    rc = main([*RC_ARGV, "--out", str(out)])
    assert rc == 0
    assert not out.exists() or not list(out.glob("*.json"))
    printed = capsys.readouterr().out
    assert "0 composite candidates" in printed and "gap:" in printed


def test_composite_cli_max_caps_recall_limit(tmp_path, monkeypatch):
    """--max is forwarded as the composite LIMIT (bounds both the query and the LLM
    fan-out)."""
    seen = {}

    def _capture(driver, limit):
        seen["limit"] = limit
        return _canned_recall([])

    monkeypatch.setattr(cli, "_driver", lambda: _NullDriverCM())
    monkeypatch.setattr(cli, "recall_refactor_candidates", _capture)
    monkeypatch.setattr(curate_pkg, "default_generate", lambda prompt, **kw: RC_LLM)
    main([*RC_ARGV, "--out", str(tmp_path / "s"), "--max", "3"])
    assert seen["limit"] == 3
