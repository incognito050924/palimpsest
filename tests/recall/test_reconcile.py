"""TDD for N-way peer reconcile recall (wi_260702y0d — branch-scoped context).

``reconcile_recall(driver, symbol, branches)`` compares the branch-scoped peers of
ONE symbol across a caller-specified branch set — as EQUALS (no privileged branch).
It ranks the peers by the absolute UTC instant of their ``committed_at`` (newest
first), surfaces the semantic layer bound to each peer DISPLAY-ONLY, and reports the
cross-branch conflict/divergence — all combinatorial, provider-free.

Contracts exercised here:
* UTC-instant ranking, cross-timezone correct (not lexicographic on the raw string).
* No-privilege peer ranking: branch name carries no priority; ``main`` is a peer.
* Only the specified branches compare (ac-6); a sub-set request stays isolated.
* Semantic priority display-only: source + confidence + verdict round-trip, LLM 0.
* Author %ae (email) never leaks into ANY channel.

Live Neo4j via conftest; peers are hand-ingested branch planes on the shared graph,
under a symbol namespace ('recon.*') that never collides with the commute fixture.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from palimpsest.ir import (
    CONFLICTS_WITH,
    METHOD,
    IR,
    InferredRelation,
    Node,
    Provenance,
    branch_scoped_id,
)
from palimpsest.kg import ingest, load_relations
from palimpsest.recall.graphrag import reconcile_recall

SYMBOL = "recon.Widget#render(Ctx)"
OTHER = "recon.Widget#paint(Ctx)"


def _peer_ir(branch, qn, commit, committed_at, author):
    """A one-node branch plane for ``qn`` (grounded, author carried on provenance)."""
    prov = Provenance(source_commit=commit, author=author, committed_at=committed_at)
    node = Node(
        kind=METHOD, qualified_name=qn, name=qn.rsplit("#", 1)[-1],
        provenance=prov, path="src/Widget.java", start_line=1, end_line=9,
        branch=branch,
    )
    return IR(nodes=[node], edges=[])


def _ingest_peer(driver, branch, qn, commit, committed_at, author):
    ingest(driver, _peer_ir(branch, qn, commit, committed_at, author))
    return branch_scoped_id(branch, qn)


def _branch(peers, name):
    return next((p for p in peers if p["branch"] == name), None)


# ── ac-3: UTC-instant ranking, cross-timezone correct ────────────────────────

def test_ranking_is_by_utc_instant_not_lexicographic(recall_db):
    """A +09:00 commit vs a LATER-instant +00:00 commit: raw-string descending sort
    would rank the +09:00 one first, but the +00:00 one is the later UTC moment and
    must be the freshest."""
    # tz-east 09:00+09:00 == 00:00Z ; tz-utc 05:00+00:00 == 05:00Z (5h later).
    _ingest_peer(recall_db, "tz-east", SYMBOL, "e" * 40,
                 "2025-01-01T09:00:00+09:00", "eve <eve@x.io>")
    _ingest_peer(recall_db, "tz-utc", SYMBOL, "u" * 40,
                 "2025-01-01T05:00:00+00:00", "uma <uma@x.io>")

    out = reconcile_recall(recall_db, SYMBOL, ["tz-east", "tz-utc"])
    peers = out["peers"]
    assert [p["branch"] for p in peers] == ["tz-utc", "tz-east"]  # later instant first
    assert _branch(peers, "tz-utc")["freshest"] is True
    assert _branch(peers, "tz-east")["freshest"] is False


def test_tied_utc_instant_marks_all_tied_peers_freshest(recall_db):
    """Two peers at the SAME absolute instant (across zones) are co-freshest — no
    fabricated single winner."""
    _ingest_peer(recall_db, "tie-a", SYMBOL, "a" * 40,
                 "2025-02-01T12:00:00+09:00", "a <a@x.io>")   # 03:00Z
    _ingest_peer(recall_db, "tie-b", SYMBOL, "b" * 40,
                 "2025-02-01T03:00:00+00:00", "b <b@x.io>")   # 03:00Z (same instant)

    out = reconcile_recall(recall_db, SYMBOL, ["tie-a", "tie-b"])
    assert len(out["peers"]) == 2
    assert all(p["freshest"] for p in out["peers"])


def test_unparseable_committed_at_sorts_last_and_never_freshest(recall_db):
    """A missing/unparseable ``committed_at`` sorts last and is never freshest."""
    _ingest_peer(recall_db, "good", SYMBOL, "g" * 40,
                 "2025-03-01T00:00:00+00:00", "g <g@x.io>")
    _ingest_peer(recall_db, "bad", SYMBOL, "z" * 40, "not-a-date", "z <z@x.io>")

    out = reconcile_recall(recall_db, SYMBOL, ["good", "bad"])
    assert out["peers"][-1]["branch"] == "bad"
    assert _branch(out["peers"], "bad")["freshest"] is False
    assert _branch(out["peers"], "good")["freshest"] is True


# ── ac-3: no privileged branch ───────────────────────────────────────────────

def test_no_privileged_branch_main_is_just_a_peer(recall_db):
    """``main`` gets NO priority from its name: an EARLIER-instant ``main`` loses
    freshest to a later-instant feature branch. Order key is the instant alone."""
    _ingest_peer(recall_db, "main", SYMBOL, "m" * 40,
                 "2025-04-01T00:00:00+00:00", "m <m@x.io>")        # earlier
    _ingest_peer(recall_db, "feature", SYMBOL, "f" * 40,
                 "2025-04-02T00:00:00+00:00", "f <f@x.io>")        # later

    out = reconcile_recall(recall_db, SYMBOL, ["main", "feature"])
    assert out["peers"][0]["branch"] == "feature"
    assert _branch(out["peers"], "feature")["freshest"] is True
    assert _branch(out["peers"], "main")["freshest"] is False


# ── ac-6: only the specified branches ────────────────────────────────────────

def test_only_specified_branches_compared_isolation(recall_db):
    """A branch-scoped seed set returns ONLY those branches' peers — unspecified
    planes (incl. the bare-id plane) are not folded in."""
    _ingest_peer(recall_db, "iso-a", SYMBOL, "1" * 40,
                 "2025-05-01T00:00:00+00:00", "a <a@x.io>")
    _ingest_peer(recall_db, "iso-b", SYMBOL, "2" * 40,
                 "2025-05-02T00:00:00+00:00", "b <b@x.io>")
    _ingest_peer(recall_db, "iso-c", SYMBOL, "3" * 40,
                 "2025-05-03T00:00:00+00:00", "c <c@x.io>")
    # A bare-id (branch=None) plane for the same symbol must NOT be folded in.
    ingest(recall_db, _peer_ir(None, SYMBOL, "0" * 40,
                               "2025-05-09T00:00:00+00:00", "n <n@x.io>"))

    out = reconcile_recall(recall_db, SYMBOL, ["iso-a", "iso-b"])
    assert {p["branch"] for p in out["peers"]} == {"iso-a", "iso-b"}
    # every returned peer id lives in one of the requested planes
    assert all(p["id"] == branch_scoped_id(p["branch"], SYMBOL) for p in out["peers"])
    assert out["branches"] == ["iso-a", "iso-b"]


def test_unresolved_symbol_reports_gap_not_confident_empty(recall_db):
    out = reconcile_recall(recall_db, "recon.Nope#gone()", ["x", "y"])
    assert out["peers"] == []
    assert out["gaps"] and any("no peers" in g for g in out["gaps"])


# ── ac-4: semantic priority display-only (source + confidence + verdict) ──────

def test_per_branch_source_and_confidence_display_only(recall_db):
    """Each peer carries its own source_commit (출처); the external judge's verdict
    + confidence bound to a peer surface DISPLAY-ONLY in its semantic channel,
    parsed back from stored JSON — palimpsest generated none of it."""
    a = _ingest_peer(recall_db, "sem-a", SYMBOL, "aa" + "a" * 38,
                     "2025-06-01T00:00:00+00:00", "a <a@x.io>")
    _ingest_peer(recall_db, "sem-a", OTHER, "aa" + "a" * 38,
                 "2025-06-01T00:00:00+00:00", "a <a@x.io>")
    b = _ingest_peer(recall_db, "sem-b", SYMBOL, "bb" + "b" * 38,
                     "2025-06-02T00:00:00+00:00", "b <b@x.io>")

    verdict = {"verdict": "confirmed", "judge": "ditto"}
    rel = InferredRelation(
        source_id=a, target_id=branch_scoped_id("sem-a", OTHER),
        rel_type=CONFLICTS_WITH, generator="g", model="m",
        source_commit="aa" + "a" * 38, created_at="2026-07-02T09:00:00+09:00",
        confidence=0.6, semantic_verdict=verdict,
    )
    assert load_relations(recall_db, [rel]).loaded == 1

    out = reconcile_recall(recall_db, SYMBOL, ["sem-a", "sem-b"])
    pa = _branch(out["peers"], "sem-a")
    pb = _branch(out["peers"], "sem-b")

    # source (출처) is per-branch, always present
    assert pa["source_commit"] == "aa" + "a" * 38
    assert pb["source_commit"] == "bb" + "b" * 38

    # the external verdict + confidence ride in DISPLAY-ONLY on sem-a's peer
    entry = next(r for r in pa["semantic"]["relations"]
                 if r["rel_type"] == CONFLICTS_WITH)
    assert entry["confidence"] == 0.6
    assert entry["semantic_verdict"] == verdict     # parsed back from stored JSON
    assert entry["edge_kind"] == "inferred"
    # sem-b has no bound verdict -> absent, not fabricated
    assert pb["semantic"]["relations"] == []


def test_cross_branch_conflict_edge_and_structural_divergence_labeled_distinctly(recall_db):
    """Two non-generative conflict tracks, labeled distinctly: (a) an EXISTING
    CONFLICTS_WITH edge touching a peer surfaces in ``conflict_edges``; (b) peers
    sharing the symbol but differing in source_commit are a COMPUTED structural
    divergence (no edge written)."""
    a = _ingest_peer(recall_db, "cf-a", SYMBOL, "c1" + "a" * 38,
                     "2025-07-01T00:00:00+00:00", "a <a@x.io>")
    _ingest_peer(recall_db, "cf-a", OTHER, "c1" + "a" * 38,
                 "2025-07-01T00:00:00+00:00", "a <a@x.io>")
    _ingest_peer(recall_db, "cf-b", SYMBOL, "c2" + "b" * 38,
                 "2025-07-02T00:00:00+00:00", "b <b@x.io>")

    rel = InferredRelation(
        source_id=a, target_id=branch_scoped_id("cf-a", OTHER),
        rel_type=CONFLICTS_WITH, generator="g", model="m",
        source_commit="c1" + "a" * 38, created_at="2026-07-02T09:00:00+09:00",
    )
    assert load_relations(recall_db, [rel]).loaded == 1

    out = reconcile_recall(recall_db, SYMBOL, ["cf-a", "cf-b"])
    # (a) existing edge track
    assert any(e["rel_type"] == CONFLICTS_WITH for e in out["conflict_edges"])
    # (b) computed divergence track: two distinct source_commits, flagged diverged
    div = out["code_divergence"]
    assert set(div["source_commits"]) == {"c1" + "a" * 38, "c2" + "b" * 38}
    assert div["diverged"] is True


# ── ac-4/pre-mortem: author %ae (email) never leaks ──────────────────────────

def test_author_email_never_leaks_in_any_channel(recall_db):
    _ingest_peer(recall_db, "ae-a", SYMBOL, "d1" + "a" * 38,
                 "2025-08-01T00:00:00+00:00", "secret <secret.person@corp.example>")
    _ingest_peer(recall_db, "ae-b", SYMBOL, "d2" + "b" * 38,
                 "2025-08-02T00:00:00+00:00", "other <other.person@corp.example>")

    out = reconcile_recall(recall_db, SYMBOL, ["ae-a", "ae-b"])
    assert len(out["peers"]) == 2
    blob = json.dumps(out)
    assert "@corp.example" not in blob
    assert "secret.person" not in blob
    assert "author" not in blob


# ── ac-4: provider-free (LLM 0) ──────────────────────────────────────────────

FORBIDDEN_GENERATIVE_MODULES = [
    "openai", "anthropic", "langchain", "langchain_core", "llama_index",
    "transformers", "cohere", "neo4j_graphrag", "vertexai",
    "google.generativeai", "litellm", "sentence_transformers",
]


def test_reconcile_path_imports_no_generative_module(recall_db):
    """Importing the reconcile path pulls in NO LLM/generative module (ac-4 LLM 0)."""
    src = Path(__file__).parents[2] / "src"
    probe = (
        "import importlib, sys, json\n"
        "m = importlib.import_module('palimpsest.recall.graphrag')\n"
        "assert hasattr(m, 'reconcile_recall')\n"
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
    assert loaded == [], f"reconcile path imported generative modules: {loaded}"
