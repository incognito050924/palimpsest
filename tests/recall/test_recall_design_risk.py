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

from palimpsest.ir import (
    CAUSALLY_RELATES,
    CONFLICTS_WITH,
    REPO,
    DesignDecision,
    InferredRelation,
    Risk,
)
from palimpsest.kg import (
    augment_communities,
    community_id,
    decision_id,
    ingest,
    load_design_decisions,
    load_relations,
    load_risks,
    risk_id,
)
from palimpsest.recall import recall, recall_community

COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"  # matches conftest PROV
CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
CTRL_METHOD = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"

RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions", "relations",
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


def test_decisions_channel_exposes_lineage_freshness(design_risk_db):
    """ac-3 (신선도 2축): a live decision surfaces valid_from + valid_to=None + live=True."""
    driver, _, did = design_risk_db
    out = recall(driver, CTRL_METHOD, depth=1)
    entry = next(d for d in out["decisions"] if d["id"] == did)
    assert entry["valid_from"]          # bi-temporal valid_from present
    assert entry["valid_to"] is None    # not superseded
    assert entry["live"] is True        # -> live


def test_decisions_channel_marks_superseded_not_live(recall_db):
    """A superseded decision is still SURFACED (전이력 보존) but flagged not-live."""
    d0 = _decision([CTRL_METHOD], "old: keep punch-in inline")
    assert load_design_decisions(recall_db, [d0]).loaded == 1
    d0_id = decision_id(d0.title, d0.source_commit, sorted({CTRL_METHOD}))
    d1 = DesignDecision(
        title="new: extract punch-in", decides=(SVC,), supersedes=(d0_id,),
        addresses_risks=(), generator="fixture-dec-gen", model="m1",
        source_commit=COMMIT, created_at="2026-07-03T00:00:00+09:00",
    )
    assert load_design_decisions(recall_db, [d1]).loaded == 1

    out = recall(recall_db, CTRL_METHOD, depth=1)
    entry = next(d for d in out["decisions"] if d["id"] == d0_id)
    assert entry["valid_to"] == "2026-07-03T00:00:00+09:00"  # invalidated at superseder's time
    assert entry["live"] is False                            # not live, yet still surfaced


def test_relations_channel_surfaces_inferred_relation(recall_db):
    """ac-3: an inferred relation touching a recalled node surfaces in the
    'relations' channel and never enters the items channel."""
    rel = InferredRelation(
        source_id=CTRL_METHOD, target_id=SVC, rel_type=CONFLICTS_WITH,
        generator="fixture-rel-gen", model="m1", source_commit=COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    assert load_relations(recall_db, [rel]).loaded == 1

    out = recall(recall_db, CTRL_METHOD, depth=1)
    entry = next(
        (r for r in out["relations"]
         if r["source_id"] == CTRL_METHOD and r["target_id"] == SVC),
        None,
    )
    assert entry is not None                     # surfaced in the relations channel
    assert entry["rel_type"] == CONFLICTS_WITH
    assert entry["edge_kind"] == "inferred"
    # never traversed into items via the inferred relation
    assert all(it["relation"] != CONFLICTS_WITH for it in out["items"])


def test_relations_channel_round_trips_provenance(recall_db):
    """The relations channel carries the edge provenance through load->recall:
    confidence + external semantic_verdict (parsed back from stored JSON) + commit.
    Uses a distinct (endpoint, rel_type) so it never collides with other tests'
    edges on the shared session graph."""
    verdict = {"verdict": "confirmed", "judge": "ditto"}
    rel = InferredRelation(
        source_id=CTRL_METHOD, target_id=CTRL, rel_type=CAUSALLY_RELATES,
        generator="g", model="m", source_commit=COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
        confidence=0.7, semantic_verdict=verdict,
    )
    assert load_relations(recall_db, [rel]).loaded == 1

    out = recall(recall_db, CTRL_METHOD, depth=1)
    entry = next(
        r for r in out["relations"]
        if r["source_id"] == CTRL_METHOD and r["target_id"] == CTRL
        and r["rel_type"] == CAUSALLY_RELATES
    )
    assert entry["confidence"] == 0.7
    assert entry["semantic_verdict"] == verdict      # parsed back from stored JSON
    assert entry["source_commit"] == COMMIT
    assert entry["created_at"] == "2026-07-02T09:00:00+09:00"


def test_relations_channel_via_recall_community(recall_db, ir):
    """ac-3: recall_community also surfaces inferred relations on its members."""
    aug = copy.deepcopy(ir)
    prov = next(n for n in aug.nodes if n.kind == REPO).provenance
    augment_communities(aug, prov)
    ingest(recall_db, aug)
    cid = community_id([CTRL, SVC])
    rel = InferredRelation(
        source_id=CTRL, target_id=SVC, rel_type=CONFLICTS_WITH,
        generator="fixture-rel-gen", model="m1", source_commit=COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    assert load_relations(recall_db, [rel]).loaded == 1

    out = recall_community(recall_db, cid)
    assert any(
        r["source_id"] == CTRL and r["target_id"] == SVC and r["rel_type"] == CONFLICTS_WITH
        for r in out["relations"]
    )


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
