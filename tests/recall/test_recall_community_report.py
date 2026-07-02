"""TDD for CommunityReport surfacing in the recall summaries channel (wi_260702smx).

A CommunityReport (a Summary targeting a Community, grounded in member Classes)
reuses the inferred 'summaries' recall channel: it surfaces there when one of its
member Classes is recalled, and never leaks into the items channel. Live Neo4j
(conftest). Assertions are scoped to this report (the session graph is shared).
"""

import copy

import pytest

from palimpsest.ir import REPO, Summary, SummaryClaim
from palimpsest.kg import augment_communities, community_id, ingest, load_summaries
from palimpsest.recall import recall, recall_community

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
GEN = "communityreport-fixture-gen"  # distinct summary-id space
MODEL = "fixture-model-v1"
SOURCE_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"


@pytest.fixture
def report_recall_db(recall_db, ir):
    """Community partition materialized on the shared recall graph, plus one
    CommunityReport grounded in its member Classes (additive, idempotent MERGE)."""
    aug = copy.deepcopy(ir)
    prov = next(n for n in aug.nodes if n.kind == REPO).provenance
    augment_communities(aug, prov)
    ingest(recall_db, aug)
    cid = community_id([CTRL, SVC])
    report = Summary(
        target_id=cid,
        claims=(
            SummaryClaim(
                text="The commute controller/service cluster.",
                source_refs=(CTRL, SVC),
            ),
        ),
        generator=GEN, model=MODEL, source_commit=SOURCE_COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    res = load_summaries(recall_db, [report])
    assert res.loaded == 1, res.rejections
    return recall_db, cid


def test_community_report_surfaces_in_summaries_channel(report_recall_db):
    driver, cid = report_recall_db
    out = recall(driver, CTRL, depth=1, limit=25)

    entry = next((s for s in out["summaries"] if s["target_id"] == cid), None)
    assert entry is not None                 # surfaced via the summaries channel
    assert entry["edge_kind"] == "inferred"  # inferred marker off the SUMMARIZES edge
    assert entry["refs"]                      # grounded in its member refs

    # No laundering: the report never enters the items channel.
    for it in out["items"]:
        assert it["kind"] != "Summary"
        assert not it["id"].startswith("summary:")


def test_community_report_surfaces_via_recall_community(report_recall_db):
    """wi_260702dbu: recalling the community by id surfaces its report in the
    summaries channel too (member Classes ground it), not only via a member's
    main recall. The report never leaks into the member items channel."""
    driver, cid = report_recall_db
    out = recall_community(driver, cid)

    entry = next((s for s in out["summaries"] if s["target_id"] == cid), None)
    assert entry is not None                 # the community's report surfaces here
    assert entry["edge_kind"] == "inferred"  # inferred marker off the SUMMARIZES edge
    assert entry["refs"]                      # grounded in its member refs

    # No laundering: the report never enters the member items channel.
    for it in out["items"]:
        assert it["kind"] != "Summary"
        assert not it["id"].startswith("summary:")
