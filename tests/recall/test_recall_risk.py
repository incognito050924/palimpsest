"""TDD for the Risk recall entry point (wi_260702tad, ac-1/ac-3).

Closes the inferred-recall gap for Risk: the loader (kg/risk.py) lands Risk nodes +
inferred RISKS edges, but there was no way to recall them (RISKS is excluded from
ordinary traversal). ``recall_risk(risk_id)`` returns the code the Risk flags as
grounded, bounded ``items`` via a SEPARATE entry point — the exact mirror of
``recall_community``. Provider-free (combinatorial: one Cypher query + dict build).

Loads a Risk additively onto the shared recall graph (MERGE-on-id, namespace-isolated
``risk:`` id, so it never perturbs the structural fixture or other recall tests).
Live Neo4j via conftest.
"""

import pytest

from palimpsest.ir import Risk
from palimpsest.kg import load_risks, risk_id
from palimpsest.recall import recall_risk

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
RISK_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"  # matches conftest PROV

RESULT_KEYS = {"items", "sources", "summaries", "risks", "decisions", "relations", "gaps", "confidence", "expand_handle"}


@pytest.fixture
def risk_recall_db(recall_db):
    """The shared recall graph with one grounded Risk loaded on top (additive,
    idempotent). Returns (driver, the loaded Risk's id)."""
    flags = sorted([CTRL, SVC])
    risk = Risk(
        title="God object: CommuteController does too much",
        flags=tuple(flags),
        generator="fixture-risk-generator",
        model="fixture-model-v1",
        source_commit=RISK_COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    assert load_risks(recall_db, [risk]).loaded == 1
    return recall_db, risk_id(risk.title, risk.source_commit, flags)


def test_recall_by_risk_returns_grounded_bounded_flagged_code(risk_recall_db):
    driver, rid = risk_recall_db
    out = recall_risk(driver, rid)

    assert set(out) == RESULT_KEYS            # separated channels, no merged prose
    ids = {it["id"] for it in out["items"]}
    assert CTRL in ids and SVC in ids         # the code the Risk flags
    for it in out["items"]:
        assert it["relation"] == "RISKS"      # reached via the inferred RISKS edge
        s = it["sources"]
        assert s["source_commit"] and s["path"] and s["start_line"] is not None  # grounded
    assert out["confidence"] == 1.0           # every flagged node grounded

    # Grounding kept as a separate channel that mirrors items.
    assert {s["id"] for s in out["sources"]} == ids


def test_recall_by_risk_is_bounded_by_limit(risk_recall_db):
    driver, rid = risk_recall_db
    out = recall_risk(driver, rid, limit=1)
    assert len(out["items"]) == 1  # bounded


def test_recall_unknown_risk_is_honest_gap(risk_recall_db):
    driver, _ = risk_recall_db
    out = recall_risk(driver, "risk:0000nonexistent")
    assert out["items"] == []
    assert out["gaps"]  # explicit gap, not a confident empty answer
