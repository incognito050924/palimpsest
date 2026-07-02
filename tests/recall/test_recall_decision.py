"""TDD for the DesignDecision recall entry point (wi_260702tad, ac-2/ac-3).

Closes the inferred-recall gap for DesignDecision: the loader (kg/decision.py)
lands DesignDecision nodes + inferred DECIDES / SUPERSEDES / ADDRESSES_RISK edges,
but there was no way to recall them (those edges are excluded from ordinary
traversal). ``recall_decision(decision_id)`` returns the decision's targets as
``items``, EACH labelled with its own edge type, via a SEPARATE entry point —
the mirror of ``recall_community`` / ``recall_risk``. Provider-free (one Cypher
query + dict build).

The decision under test exercises all three edge types, so the fixture loads its
SUPERSEDES / ADDRESSES_RISK targets FIRST (separate committed loads): edge targets
resolve against the LIVE graph only (kg/decision.py scope note — no same-batch
resolution). Live Neo4j via conftest.
"""

import pytest

from palimpsest.ir import DesignDecision, Risk
from palimpsest.kg import (
    decision_id,
    load_design_decisions,
    load_risks,
    risk_id,
)
from palimpsest.recall import recall_decision

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"  # matches conftest PROV

RESULT_KEYS = {"items", "sources", "summaries", "gaps", "confidence", "expand_handle"}


def _all_targets(d: DesignDecision):
    return sorted({*d.decides, *d.supersedes, *d.addresses_risks})


def _did(d: DesignDecision) -> str:
    return decision_id(d.title, d.source_commit, _all_targets(d))


@pytest.fixture
def decision_recall_db(recall_db):
    """The shared recall graph with a DesignDecision that DECIDES code, SUPERSEDES
    a prior decision, and ADDRESSES_RISK a Risk — its two entity targets loaded and
    committed first (live-graph resolution). Returns (driver, the decision's id,
    dict of the three expected target ids)."""
    risk = Risk(
        title="God object: CommuteService does too much",
        flags=(SVC,),
        generator="fixture-risk-generator",
        model="fixture-model-v1",
        source_commit=COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    assert load_risks(recall_db, [risk]).loaded == 1
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))

    base = DesignDecision(
        title="Keep punch-in inside CommuteService (superseded)",
        decides=(SVC,),
        supersedes=(),
        addresses_risks=(),
        generator="fixture-decision-generator",
        model="fixture-model-v1",
        source_commit=COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    assert load_design_decisions(recall_db, [base]).loaded == 1
    base_id = _did(base)

    dec = DesignDecision(
        title="Extract the commute punch-in into its own service",
        decides=(CTRL,),
        supersedes=(base_id,),
        addresses_risks=(rid,),
        generator="fixture-decision-generator",
        model="fixture-model-v1",
        source_commit=COMMIT,
        created_at="2026-07-02T10:00:00+09:00",
    )
    assert load_design_decisions(recall_db, [dec]).loaded == 1
    return recall_db, _did(dec), {"DECIDES": CTRL, "SUPERSEDES": base_id, "ADDRESSES_RISK": rid}


def test_recall_by_decision_returns_targets_labelled_by_edge_type(decision_recall_db):
    driver, did, expected = decision_recall_db
    out = recall_decision(driver, did)

    assert set(out) == RESULT_KEYS            # separated channels, no merged prose
    by_id = {it["id"]: it for it in out["items"]}
    assert set(by_id) == set(expected.values())          # all three targets recalled
    for rel, tid in expected.items():
        assert by_id[tid]["relation"] == rel             # each labelled by its edge type

    # The DECIDES *code* target is grounded (commit + file:line); the SUPERSEDES /
    # ADDRESSES_RISK targets are inferred entities (no code span) — confidence
    # reflects that honestly rather than claiming full grounding.
    code = by_id[expected["DECIDES"]]["sources"]
    assert code["source_commit"] and code["path"] and code["start_line"] is not None
    assert 0.0 < out["confidence"] < 1.0


def test_recall_by_decision_is_bounded_by_limit(decision_recall_db):
    driver, did, _ = decision_recall_db
    out = recall_decision(driver, did, limit=1)
    assert len(out["items"]) == 1  # bounded


def test_recall_unknown_decision_is_honest_gap(decision_recall_db):
    driver, _, _ = decision_recall_db
    out = recall_decision(driver, "decision:0000nonexistent")
    assert out["items"] == []
    assert out["gaps"]  # explicit gap, not a confident empty answer
