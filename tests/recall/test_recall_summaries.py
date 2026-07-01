"""TDD for the inferred-summary recall channel (n7-impl-recall-summaries).

Layers externally-generated summaries (loaded via the n6 loader — read-only here)
on top of the deterministic fixture graph, then asserts recall exposes them as a
SEPARATE ``summaries`` channel: grounded, bounded, author-free, and never
laundered into the ``items`` channel. Live Neo4j (see conftest).
"""

import json

import pytest

from palimpsest.ir import METHOD, Summary, SummaryClaim
from palimpsest.kg import load_summaries
from palimpsest.recall import recall

CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"
CTRL_METHOD = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"
SVC = "kr.co.ecoletree.service.commute.service.CommuteService"
SVC_METHOD = SVC + "#selectAttedanceCondition(Map)"

GENERATOR = "palimpsest-fixture-generator"
MODEL = "fixture-model-v1"
# Same fixed provenance the conftest ingests the graph with.
SOURCE_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"
AUTHOR = "jeongjin <jeongjin@ecoletree.com>"

RESULT_KEYS = {"items", "sources", "summaries", "gaps", "confidence", "expand_handle"}


@pytest.fixture
def summarized_db(recall_db, ir):
    """The recall graph with two summaries layered on (idempotent MERGE-on-id):

    * one on the seed ``CTRL_METHOD`` (grounded in the method + its class) — must
      surface when we recall the seed, and
    * one on a ``far`` Method that is NOT reachable at depth 1 — must NOT surface
      (proves the summaries channel follows the recall budget).
    """
    far = next(
        n.qualified_name
        for n in ir.nodes
        if n.kind == METHOD and n.qualified_name not in {CTRL_METHOD, SVC_METHOD}
    )
    seed_summary = Summary(
        target_id=CTRL_METHOD,
        claims=(
            SummaryClaim(text="Selects attendance-condition rows.",
                         source_refs=(CTRL_METHOD,)),
            SummaryClaim(text="Declared on the CommuteController class.",
                         source_refs=(CTRL,)),
        ),
        generator=GENERATOR, model=MODEL, source_commit=SOURCE_COMMIT,
        created_at="2026-07-01T09:00:00+09:00",
    )
    far_summary = Summary(
        target_id=far,
        claims=(SummaryClaim(text="Out-of-budget summary.", source_refs=(far,)),),
        generator=GENERATOR, model=MODEL, source_commit=SOURCE_COMMIT,
        created_at="2026-07-01T09:00:00+09:00",
    )
    res = load_summaries(recall_db, [seed_summary, far_summary])
    assert res.loaded == 2, res.rejections  # guard: the fixtures actually landed
    return recall_db, far


def test_summaries_surface_as_separate_grounded_bounded_channel(summarized_db):
    driver, far = summarized_db
    out = recall(driver, CTRL_METHOD, depth=1, limit=25)

    # A separate channel (never merged into items).
    assert set(out) == RESULT_KEYS
    assert isinstance(out["summaries"], list) and out["summaries"]

    seed_sum = next(s for s in out["summaries"] if s["target_id"] == CTRL_METHOD)

    # Inferred marker (read from the SUMMARIZES edge) + bound commit (freshness).
    assert seed_sum["edge_kind"] == "inferred"
    assert seed_sum["code_bound_at"]  # non-null freshness bound

    # Grounding refs: real code spans, projected in the author-omitting _sources
    # shape (commit + file:line, no author).
    assert seed_sum["refs"]
    ref_ids = {r["id"] for r in seed_sum["refs"]}
    assert CTRL_METHOD in ref_ids and CTRL in ref_ids
    for r in seed_sum["refs"]:
        assert set(r) == {
            "id", "source_commit", "path", "start_line", "end_line", "committed_at",
        }

    # No laundering: no Summary node and no summary text in the items channel.
    for it in out["items"]:
        assert it["kind"] != "Summary"
        assert not it["id"].startswith("summary:")
        assert "claims" not in it

    # Bounded by the recall budget: a summary on a node outside this depth does
    # not appear (summaries follow depth/limit, not the whole graph).
    item_ids = {it["id"] for it in out["items"]}
    assert far not in item_ids  # precondition: far really is out of budget
    assert all(s["target_id"] != far for s in out["summaries"])


def test_summarizes_relation_never_leaks_a_summary_into_items(summarized_db):
    driver, _ = summarized_db
    # Even when the caller explicitly requests SUMMARIZES, it is not traversable:
    # a Summary can never reach the items channel.
    out = recall(driver, CTRL_METHOD, depth=1, limit=25, relations=["SUMMARIZES"])

    assert set(out) == RESULT_KEYS
    for it in out["items"]:
        assert it["kind"] != "Summary"
        assert not it["id"].startswith("summary:")
    # It still surfaces — but only via the separate summaries channel.
    assert any(s["target_id"] == CTRL_METHOD for s in out["summaries"])


def test_author_is_not_exposed_in_the_summaries_channel(summarized_db):
    driver, _ = summarized_db
    out = recall(driver, CTRL_METHOD, depth=1, limit=25)
    assert out["summaries"]

    blob = json.dumps(out["summaries"])
    assert AUTHOR not in blob  # the git author string never leaks
    for s in out["summaries"]:
        assert "author" not in s
        for r in s["refs"]:
            assert "author" not in r
