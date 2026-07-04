"""TDD for the standalone vector-KNN semantic recall entry point (impl-semantic-recall).

``recall_semantic(driver, query_vector, branches=None, limit=25)`` runs a top-k
cosine query over the Summary VECTOR INDEX and returns the SAME standard bounded
result shape as its sibling recall entry points — the matched Summaries surface in
the separate ``summaries`` channel, each carrying:

* a cosine ``score`` field, kept SEPARATE from the result's grounding-coverage
  ``confidence`` (ac-3: cosine is never laundered into confidence),
* a ``stale`` freshness flag (every Summary-surfacing path attaches it), and
* the ``branch`` plane the hit came from (ADR-20260703 branch-scoped identity).

Honesty (ac-3): a query whose best match is below the similarity floor returns an
explicit ``gap`` rather than a confident-empty / low-similarity filled-k answer.

Provider-free/hermetic (ac-5): palimpsest never embeds — the QUERY vector is
supplied by the caller as a LITERAL float vector, so the whole path runs with no
network and no generative library (asserted by a fresh-interpreter probe).

Live Neo4j via conftest (session-scoped ``recall_db``); every graph-touching test
isolates itself on a distinct embedding coordinate AXIS and its own branch planes,
so the global (index-wide) KNN query cannot cross-contaminate between tests
(off-axis summaries are orthogonal -> below the floor -> excluded).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from palimpsest.ir import (
    EMBEDDING_DIM,
    IR,
    METHOD,
    Node,
    Provenance,
    Summary,
    SummaryClaim,
    branch_scoped_id,
)
from palimpsest.kg import ingest, load_summaries, summary_id
from palimpsest.kg.summary import VECTOR_INDEX_NAME, create_vector_index
from palimpsest.recall.graphrag import InvalidQueryVector, recall_semantic

MODEL = "fixture-model-v1"
EMB_MODEL = "fixture-embed-v1"          # single model per cosine index (loader-enforced)
SUMMARY_COMMIT = "s" * 40


def _vec(coords):
    """A literal EMBEDDING_DIM vector, non-zero only on the given coordinates
    (``{index: value}``) — no model call, deterministic, network-free."""
    v = [0.0] * EMBEDDING_DIM
    for i, val in coords.items():
        v[i] = float(val)
    return v


def _ingest_target(driver, branch, qn, committed="2025-01-01T00:00:00+00:00"):
    """Ingest a branch-scoped Method node to be a Summary's SUMMARIZES target,
    returning its branch-scoped id (the summary's freshness/branch anchor)."""
    prov = Provenance(
        source_commit=f"{branch}"[:1] * 40, author="a <a@x.io>", committed_at=committed
    )
    node = Node(
        kind=METHOD, qualified_name=qn, name=qn.rsplit(".", 1)[-1],
        provenance=prov, path="src/S.java", start_line=1, end_line=9, branch=branch,
    )
    ingest(driver, IR(nodes=[node], edges=[]))
    return branch_scoped_id(branch, qn)


def _load_summary(driver, target_id, embedding, tag):
    """Load one embedded Summary grounded in ``target_id``; returns its id."""
    generator = f"sem-{tag}"
    s = Summary(
        target_id=target_id,
        claims=(SummaryClaim(text="a claim", source_refs=(target_id,)),),
        generator=generator, model=MODEL, source_commit=SUMMARY_COMMIT,
        created_at="2026-07-01T09:00:00+09:00",
        embedding=embedding, embedding_model=EMB_MODEL, embedding_dim=EMBEDDING_DIM,
    )
    res = load_summaries(driver, [s])
    assert res.loaded == 1, res.rejections
    return summary_id(target_id, generator, MODEL, SUMMARY_COMMIT)


# ── ac-3: top-k cosine, descending, score its OWN field (≠ confidence) ────────

def test_topk_returns_cosine_descending_with_score_distinct_from_confidence(recall_db):
    b = "sem-topk"
    ta = _ingest_target(recall_db, b, "sem.topk.A")
    tb = _ingest_target(recall_db, b, "sem.topk.B")
    tc = _ingest_target(recall_db, b, "sem.topk.C")
    sa = _load_summary(recall_db, ta, _vec({10: 1.0}), "topkA")            # cos 1.0
    sb = _load_summary(recall_db, tb, _vec({10: 1.0, 11: 1.0}), "topkB")   # cos .707
    sc = _load_summary(recall_db, tc, _vec({10: 1.0, 11: 2.0}), "topkC")   # cos .447
    create_vector_index(recall_db)

    out = recall_semantic(recall_db, _vec({10: 1.0}), branches=[b])
    sums = out["summaries"]

    assert [s["id"] for s in sums] == [sa, sb, sc]          # cosine descending
    scores = [s["score"] for s in sums]
    assert scores == sorted(scores, reverse=True)           # strictly descending
    assert all(s["score"] >= 0.6 for s in sums)             # above the floor
    # cosine score is its OWN field, SEPARATE from grounding-coverage confidence.
    assert out["confidence"] == 0.0                          # coverage of items (empty)
    assert all("score" in s and "confidence" not in s for s in sums)


# ── ac-3: below the floor -> explicit gap, not a filled-k confident answer ─────

def test_below_floor_returns_explicit_gap_not_filled_k(recall_db):
    b = "sem-floor"
    t = _ingest_target(recall_db, b, "sem.floor.A")
    _load_summary(recall_db, t, _vec({20: 1.0}), "floor")
    create_vector_index(recall_db)

    # query orthogonal to the only in-branch embedding -> cosine 0 -> below floor
    out = recall_semantic(recall_db, _vec({21: 1.0}), branches=[b])
    assert out["summaries"] == []
    assert out["gaps"] and any(
        "floor" in g or "similarity" in g for g in out["gaps"]
    )


# ── stale flag: bound code re-committed after binding -> stale=true ───────────

def test_stale_flag_set_when_bound_code_recommitted(recall_db):
    b = "sem-stale"
    t = _ingest_target(recall_db, b, "sem.stale.A",
                       committed="2025-01-01T00:00:00+00:00")
    sid = _load_summary(recall_db, t, _vec({30: 1.0}), "stale")
    create_vector_index(recall_db)
    q = _vec({30: 1.0})

    fresh = next(s for s in recall_semantic(recall_db, q, branches=[b])["summaries"]
                 if s["id"] == sid)
    assert fresh["stale"] is False                          # baseline: bound at load

    with recall_db.session() as s:
        s.run("MATCH (n {id: $id}) SET n.committed_at = $c",
              id=t, c="2026-02-02T00:00:00+00:00")

    after = next(s for s in recall_semantic(recall_db, q, branches=[b])["summaries"]
                 if s["id"] == sid)
    assert after["stale"] is True                           # target re-committed


# ── ac-3/ADR-20260703: branch scoping + branch projection ─────────────────────

def test_branches_scopes_candidates_and_does_not_leak_other_plane(recall_db):
    ta = _ingest_target(recall_db, "sc-a", "sem.scope.X")
    tb = _ingest_target(recall_db, "sc-b", "sem.scope.X")   # same symbol, other plane
    sa = _load_summary(recall_db, ta, _vec({40: 1.0}), "scopeA")
    sb = _load_summary(recall_db, tb, _vec({40: 1.0}), "scopeB")
    create_vector_index(recall_db)

    out = recall_semantic(recall_db, _vec({40: 1.0}), branches=["sc-a"])
    ids = {s["id"] for s in out["summaries"]}
    assert sa in ids and sb not in ids                      # other plane not leaked
    assert out["summaries"] and all(s["branch"] == "sc-a" for s in out["summaries"])


def test_branches_none_surfaces_all_planes_with_branch_projected(recall_db):
    ta = _ingest_target(recall_db, "bn-a", "sem.none.X")
    tb = _ingest_target(recall_db, "bn-b", "sem.none.X")
    sa = _load_summary(recall_db, ta, _vec({50: 1.0}), "noneA")
    sb = _load_summary(recall_db, tb, _vec({50: 1.0}), "noneB")
    create_vector_index(recall_db)

    out = recall_semantic(recall_db, _vec({50: 1.0}), branches=None)
    # both planes surface; nothing off this axis clears the floor, so only these two
    assert {s["id"] for s in out["summaries"]} == {sa, sb}
    assert {s["branch"] for s in out["summaries"]} == {"bn-a", "bn-b"}


# ── limit clamp + typed vector rejection ──────────────────────────────────────

def test_limit_zero_clamps_to_one_and_wrong_vector_is_typed_rejection(recall_db):
    b = "sem-lim"
    t = _ingest_target(recall_db, b, "sem.lim.A")
    sid = _load_summary(recall_db, t, _vec({60: 1.0}), "lim")
    create_vector_index(recall_db)

    # limit=0 must clamp to k=1 (queryNodes requires k>=1), never crash
    out = recall_semantic(recall_db, _vec({60: 1.0}), branches=[b], limit=0)
    assert any(s["id"] == sid for s in out["summaries"])

    # wrong-length vector -> typed rejection BEFORE the driver query
    with pytest.raises(InvalidQueryVector):
        recall_semantic(recall_db, [0.0] * (EMBEDDING_DIM - 1), branches=[b])

    # NaN component -> typed rejection
    bad = _vec({60: 1.0})
    bad[0] = float("nan")
    with pytest.raises(InvalidQueryVector):
        recall_semantic(recall_db, bad, branches=[b])


# ── ac-5: provider-free — the semantic path pulls in 0 generative modules ─────

FORBIDDEN_GENERATIVE_MODULES = [
    "openai", "anthropic", "langchain", "langchain_core", "llama_index",
    "transformers", "cohere", "neo4j_graphrag", "vertexai",
    "google.generativeai", "litellm", "sentence_transformers",
]


def test_semantic_path_imports_no_generative_module():
    """Importing the recall_semantic path pulls in NO LLM/generative/embedding
    module (ac-5 provider-free). Fresh-interpreter probe (no live DB)."""
    src = Path(__file__).parents[2] / "src"
    probe = (
        "import importlib, sys, json\n"
        "m = importlib.import_module('palimpsest.recall.graphrag')\n"
        "assert hasattr(m, 'recall_semantic')\n"
        f"forbidden = {FORBIDDEN_GENERATIVE_MODULES!r}\n"
        "bad = sorted({x for x in sys.modules for p in forbidden "
        "if x == p or x.startswith(p + '.')})\n"
        "print(json.dumps(bad))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True, text=True, check=True,
    )
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], f"semantic path imported generative modules: {loaded}"


def test_recall_semantic_is_exported_from_recall_package():
    """``recall_semantic`` is reachable as ``palimpsest.recall.recall_semantic``,
    the same as its sibling entry points."""
    from palimpsest.recall import recall_semantic as pkg_fn
    from palimpsest.recall.graphrag import recall_semantic as mod_fn

    assert pkg_fn is mod_fn
