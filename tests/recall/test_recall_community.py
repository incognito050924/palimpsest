"""TDD for the Community recall entry point (wi_2607010n6, ac-4).

Layers the Class-level Community partition onto the shared fixture graph
(additive MERGE, like the summaries tests) and asserts recall by a Community id
returns its member Class nodes: grounded, bounded, author-omitted, LLM-free, and
via a SEPARATE entry point (never ordinary MEMBER_OF traversal). Live Neo4j.
"""

import copy
import json

import pytest

from palimpsest.ir import REPO
from palimpsest.kg import augment_communities, community_id, ingest
from palimpsest.recall import recall_community

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
AUTHOR = "jeongjin <jeongjin@ecoletree.com>"

RESULT_KEYS = {"items", "sources", "summaries", "risks", "decisions", "gaps", "confidence", "expand_handle"}


@pytest.fixture
def community_recall_db(recall_db, ir):
    """The shared recall graph with the Community partition materialized on top
    (additive, idempotent MERGE — never wipes the session graph)."""
    aug = copy.deepcopy(ir)
    prov = next(n for n in aug.nodes if n.kind == REPO).provenance
    augment_communities(aug, prov)
    ingest(recall_db, aug)
    return recall_db, community_id([CTRL, SVC])


def test_recall_by_community_returns_grounded_bounded_member_classes(community_recall_db):
    driver, cid = community_recall_db
    out = recall_community(driver, cid)

    assert set(out) == RESULT_KEYS            # separated channels, no merged prose
    ids = {it["id"] for it in out["items"]}
    assert CTRL in ids and SVC in ids         # the community's member classes
    for it in out["items"]:
        assert it["kind"] == "Class"
        s = it["sources"]
        assert s["source_commit"] and s["path"] and s["start_line"] is not None  # grounded
    assert out["confidence"] == 1.0           # every member grounded

    # Grounding kept as a separate channel that mirrors items.
    assert {s["id"] for s in out["sources"]} == ids
    # Author never leaks (author-omitting _sources shape).
    assert AUTHOR not in json.dumps(out["sources"])


def test_recall_by_community_is_bounded_by_limit(community_recall_db):
    driver, cid = community_recall_db
    out = recall_community(driver, cid, limit=1)
    assert len(out["items"]) == 1  # bounded


def test_recall_unknown_community_is_honest_gap(community_recall_db):
    driver, _ = community_recall_db
    out = recall_community(driver, "community:0000nonexistent")
    assert out["items"] == []
    assert out["gaps"]  # explicit gap, not a confident empty answer
