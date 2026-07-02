"""TDD for the inferred-summary recall channel (n7-impl-recall-summaries).

Layers externally-generated summaries (loaded via the n6 loader — read-only here)
on top of the deterministic fixture graph, then asserts recall exposes them as a
SEPARATE ``summaries`` channel: grounded, bounded, author-free, and never
laundered into the ``items`` channel. Live Neo4j (see conftest).
"""

import json

import pytest

from palimpsest.ir import METHOD, Summary, SummaryClaim
from palimpsest.kg import load_summaries, summary_id
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

RESULT_KEYS = {"items", "sources", "summaries", "risks", "decisions", "gaps", "confidence", "expand_handle"}


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


# --- semantic verdict (ingest-only, annotate — never a reject path) -----------

VERDICT_GENERATOR = GENERATOR + "-verdict"  # distinct summary-id space


def test_unfaithful_verdict_loads_and_surfaces_in_summaries_channel(recall_db):
    """An external judge's 'unfaithful' verdict does NOT reject the summary — it
    LOADS and the recall summaries channel exposes the verdict as a flag (annotate,
    like `stale`). palimpsest itself never judges; it only ingests the verdict."""
    verdict = {"verdict": "unfaithful", "judge": "ditto", "model": "judge-model-v1"}
    s = Summary(
        target_id=CTRL_METHOD,
        claims=(SummaryClaim(text="Selects attendance-condition rows.",
                             source_refs=(CTRL_METHOD,)),),
        generator=VERDICT_GENERATOR, model=MODEL, source_commit=SOURCE_COMMIT,
        created_at="2026-07-01T09:00:00+09:00",
        semantic_verdict=verdict,
    )
    res = load_summaries(recall_db, [s])
    # Annotate, not reject: an unfaithful verdict still loads.
    assert res.loaded == 1 and res.rejected == 0, res.rejections

    sid = summary_id(CTRL_METHOD, VERDICT_GENERATOR, MODEL, SOURCE_COMMIT)
    out = recall(recall_db, CTRL_METHOD, depth=1, limit=25)
    entry = next(e for e in out["summaries"] if e["id"] == sid)
    assert entry["semantic_verdict"] == verdict


# --- #4 staleness (detect/flag only) -------------------------------------

STALE_GENERATOR = GENERATOR + "-stale"  # distinct id-space from summarized_db


@pytest.fixture
def stale_db(recall_db):
    """Two in-budget summaries for staleness detection, both bound at the fixture
    commit (so stale=False on load):

    * one on the seed ``CTRL_METHOD``, and
    * one on the ``CTRL`` class (reachable via CONTAINS at depth 1).

    A distinct generator keeps these in their own summary-id space, so they never
    collide with the ``summarized_db`` fixtures in the shared session graph.
    """
    method_summary = Summary(
        target_id=CTRL_METHOD,
        claims=(SummaryClaim(text="Selects attendance-condition rows.",
                             source_refs=(CTRL_METHOD,)),),
        generator=STALE_GENERATOR, model=MODEL, source_commit=SOURCE_COMMIT,
        created_at="2026-07-01T09:00:00+09:00",
    )
    class_summary = Summary(
        target_id=CTRL,
        claims=(SummaryClaim(text="The commute controller.",
                             source_refs=(CTRL,)),),
        generator=STALE_GENERATOR, model=MODEL, source_commit=SOURCE_COMMIT,
        created_at="2026-07-01T09:00:00+09:00",
    )
    res = load_summaries(recall_db, [method_summary, class_summary])
    assert res.loaded == 2, res.rejections
    return recall_db


def _stale_entries(out):
    method_sum = next(s for s in out["summaries"]
                      if s["target_id"] == CTRL_METHOD
                      and s["id"].startswith("summary:"))
    class_sum = next(s for s in out["summaries"] if s["target_id"] == CTRL)
    return method_sum, class_sum


def test_summaries_channel_exposes_stale_boolean_false_on_fresh_load(stale_db):
    # ac-1: every summaries entry carries a `stale` boolean; freshly loaded
    # summaries (code_bound_at == the target's current committed_at) are not stale.
    out = recall(stale_db, CTRL_METHOD, depth=1, limit=25)
    assert out["summaries"]
    for s in out["summaries"]:
        assert isinstance(s["stale"], bool)
        assert s["stale"] is False


def test_stale_flips_when_target_is_recommitted(stale_db):
    # ac-2: re-ingesting the TARGET at a newer commit (its committed_at advances)
    # flags THAT summary stale, while an unchanged target's summary stays fresh.
    driver = stale_db
    new_committed_at = "2026-07-02T10:00:00+09:00"

    with driver.session() as s:
        original = s.run(
            "MATCH (n {id: $id}) RETURN n.committed_at AS c", id=CTRL_METHOD
        ).single()["c"]

    method_sum, class_sum = _stale_entries(recall(driver, CTRL_METHOD, 1, 25))
    assert method_sum["stale"] is False and class_sum["stale"] is False  # baseline

    try:
        with driver.session() as s:
            s.run("MATCH (n {id: $id}) SET n.committed_at = $c",
                  id=CTRL_METHOD, c=new_committed_at)

        method_sum, class_sum = _stale_entries(recall(driver, CTRL_METHOD, 1, 25))
        assert method_sum["stale"] is True   # target re-committed -> out of date
        assert class_sum["stale"] is False   # untouched target stays fresh
    finally:
        with driver.session() as s:
            s.run("MATCH (n {id: $id}) SET n.committed_at = $c",
                  id=CTRL_METHOD, c=original)
