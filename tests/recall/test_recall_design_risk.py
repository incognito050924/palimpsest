"""TDD for slice 2 (설계위험 감지): recall surfaces attached Risk / DesignDecision
in separate ``risks`` / ``decisions`` channels (wi_260702qe3).

On top of structural-coupling recall, the design risks and decisions BOUND to the
recalled code surface in their own channels — the "위험 표시" half of design-risk
detection. Judgment stays external / provider-free; palimpsest only surfaces what
an external producer loaded. Mirrors the inferred ``summaries`` channel exactly:
a reverse lookup from the recalled code ids to the Risk that ``RISKS``-flags them /
the DesignDecision that ``DECIDES`` them, NEVER merged into ``items``.

This folds in the reverse-lookup + channel-integration deferred by wi_260702tad —
completed here, not pushed to a later slice. Additive, idempotent loads on the
shared recall graph. Live Neo4j via conftest.
"""

import copy

import pytest

from palimpsest.ir import REPO, DesignDecision, Risk
from palimpsest.kg import (
    augment_communities,
    community_id,
    decision_id,
    ingest,
    load_design_decisions,
    load_risks,
    risk_id,
)
from palimpsest.recall import recall, recall_community

COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"  # matches conftest PROV
CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
CTRL_METHOD = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"

RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions",
    "gaps", "confidence", "expand_handle",
}


def _risk(flags, title):
    return Risk(
        title=title, flags=tuple(flags), generator="fixture-risk-gen", model="m1",
        source_commit=COMMIT, created_at="2026-07-02T09:00:00+09:00",
    )


def _decision(decides, title):
    return DesignDecision(
        title=title, decides=tuple(decides), supersedes=(), addresses_risks=(),
        generator="fixture-dec-gen", model="m1",
        source_commit=COMMIT, created_at="2026-07-02T09:00:00+09:00",
    )


@pytest.fixture
def design_risk_db(recall_db):
    """Shared recall graph with a Risk flagging + a Decision deciding CTRL_METHOD."""
    risk = _risk([CTRL_METHOD], "God method: does too much")
    assert load_risks(recall_db, [risk]).loaded == 1
    dec = _decision([CTRL_METHOD], "Refactor selectAttedanceCondition")
    assert load_design_decisions(recall_db, [dec]).loaded == 1
    return (
        recall_db,
        risk_id(risk.title, risk.source_commit, sorted(risk.flags)),
        decision_id(dec.title, dec.source_commit, sorted({CTRL_METHOD})),
    )


def test_recall_surfaces_attached_risk_in_separate_channel(design_risk_db):
    driver, rid, _ = design_risk_db
    out = recall(driver, CTRL_METHOD, depth=1)

    assert set(out) == RESULT_KEYS                          # risks/decisions channels exist
    assert rid not in {it["id"] for it in out["items"]}     # never leaks into items
    entry = next((r for r in out["risks"] if r["id"] == rid), None)
    assert entry is not None                                # surfaced in the risks channel
    assert entry["edge_kind"] == "inferred"                 # inferred marker rides in
    assert entry["title"]
    assert any(ref["id"] == CTRL_METHOD for ref in entry["refs"])  # grounded to flagged code
    assert "stale" in entry                                 # freshness flag present


def test_recall_surfaces_attached_decision_in_separate_channel(design_risk_db):
    driver, _, did = design_risk_db
    out = recall(driver, CTRL_METHOD, depth=1)

    assert did not in {it["id"] for it in out["items"]}     # never leaks into items
    entry = next((d for d in out["decisions"] if d["id"] == did), None)
    assert entry is not None                                # surfaced in the decisions channel
    assert entry["edge_kind"] == "inferred"
    assert any(ref["id"] == CTRL_METHOD for ref in entry["refs"])


def test_design_risk_channels_bounded_by_limit(design_risk_db):
    driver, _, _ = design_risk_db
    out = recall(driver, CTRL_METHOD, depth=1, limit=1)
    assert len(out["risks"]) <= 1                           # server-side row-bound
    assert len(out["decisions"]) <= 1


def test_recall_community_surfaces_member_risk_and_decision(recall_db, ir):
    """ac-3: 구조적 결합(community) 회상 위에 멤버 Class에 결박된 위험/결정 표시."""
    aug = copy.deepcopy(ir)
    prov = next(n for n in aug.nodes if n.kind == REPO).provenance
    augment_communities(aug, prov)
    ingest(recall_db, aug)
    cid = community_id([CTRL, SVC])

    risk = _risk([CTRL], "God object: CommuteController")
    assert load_risks(recall_db, [risk]).loaded == 1
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))
    dec = _decision([SVC], "Split CommuteService")
    assert load_design_decisions(recall_db, [dec]).loaded == 1
    did = decision_id(dec.title, dec.source_commit, sorted({SVC}))

    out = recall_community(recall_db, cid)
    assert set(out) == RESULT_KEYS
    member_ids = {it["id"] for it in out["items"]}
    assert rid not in member_ids and did not in member_ids  # inferred entities not in items
    assert any(r["id"] == rid for r in out["risks"])        # member's risk surfaced
    assert any(d["id"] == did for d in out["decisions"])    # member's decision surfaced
