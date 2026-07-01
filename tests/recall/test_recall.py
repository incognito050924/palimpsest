"""TDD for GraphRAG recall: progressive, grounded, combinatorial (n4-impl-recall).

Vertical slices against a LIVE Neo4j (see conftest). One behavior at a time.

Fixture graph (the n2 ``commute`` slice): a CommuteController class whose methods
CALL the matching CommuteService methods; Class->Method / File->Class / Repo->
Package via CONTAINS; a single Class->Class DEPENDS_ON. IMPORTS is sparse (only
intra-corpus targets resolve). So the meaningful traversal leans on CALLS /
DEPENDS_ON / CONTAINS.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from palimpsest.recall import recall, expand

IFACE = "kr.co.ecoletree.service.commute.service.CommuteService"
CTRL = "kr.co.ecoletree.service.commute.controller.CommuteController"

# A controller Method that CALLS the matching service Method (from the extractor).
CTRL_METHOD = CTRL + "#selectAttedanceCondition(Map,HttpServletRequest)"
SVC_METHOD = IFACE + "#selectAttedanceCondition(Map)"

TOTAL_NODES = 28  # whole fixture graph (Repo + 2 Package + 2 File + 2 Class + 21 Method)


def _by_id(items):
    return {it["id"]: it for it in items}


def test_recall_related_methods_and_classes_with_sources(recall_db):
    """query(a known Method) -> related Method/Class via CALLS + CONTAINS, each
    carrying commit + file:line grounding."""
    out = recall(recall_db, CTRL_METHOD, depth=1, limit=25)

    assert out["gaps"] == []  # seed resolved, default traversal -> no gaps
    items = _by_id(out["items"])

    # The containing class is reached via CONTAINS (incoming edge from the Class).
    assert CTRL in items
    cls = items[CTRL]
    assert cls["kind"] == "Class"
    assert cls["relation"] == "CONTAINS"

    # The service method it calls is reached via CALLS.
    assert SVC_METHOD in items
    callee = items[SVC_METHOD]
    assert callee["kind"] == "Method"
    assert callee["relation"] == "CALLS"

    # Every recalled item carries its sources = commit + file:line.
    for it in out["items"]:
        src = it["sources"]
        assert src["source_commit"] == "c20b7332d8c60ce73794427a4c28120b085c134d"
        assert src["path"] and src["path"].endswith(".java")
        assert isinstance(src["start_line"], int) and src["start_line"] >= 1
        assert src["end_line"] >= src["start_line"]

    # Separated grounding channel mirrors the items (no prose "answer").
    assert {s["id"] for s in out["sources"]} == set(items)
    assert 0.0 <= out["confidence"] <= 1.0


def test_recall_is_bounded_and_expand_handle_pulls_next_hop(recall_db):
    """Progressive: depth+limit bound the result (never the whole graph), and the
    unexpanded frontier comes back as an expand_handle the caller can pull."""
    out = recall(recall_db, CTRL, depth=1, limit=3)

    # Bounded by the limit, and strictly smaller than the whole graph.
    assert len(out["items"]) <= 3
    assert len(out["items"]) < TOTAL_NODES

    # There is more to see -> a non-empty pull-handle (not the whole graph loaded).
    handle = out["expand_handle"]
    assert handle is not None and handle["frontier"]

    first_ids = {it["id"] for it in out["items"]}

    # Pull the next hop on demand: fresh items, none already returned, all grounded.
    nxt = expand(recall_db, handle, limit=25)
    next_ids = {it["id"] for it in nxt["items"]}
    assert next_ids  # the handle actually yielded more
    assert next_ids.isdisjoint(first_ids)
    for it in nxt["items"]:
        assert it["sources"]["source_commit"] == "c20b7332d8c60ce73794427a4c28120b085c134d"


def test_recall_unresolved_seed_reports_gap_not_confident_empty(recall_db):
    """ac-3: a seed that does not resolve -> explicit gap, empty items, zero
    confidence, fields kept separate (never a confident empty answer)."""
    out = recall(recall_db, "com.does.not.Exist#nope()", depth=2, limit=25)

    assert out["items"] == []
    assert out["sources"] == []
    assert out["gaps"] and any("did not resolve" in g for g in out["gaps"])
    assert out["confidence"] == 0.0
    assert set(out) == {
        "items", "sources", "summaries", "gaps", "confidence", "expand_handle",
    }


def test_recall_explicit_relation_without_edges_reports_gap(recall_db):
    """ac-3: when a caller explicitly requests a relation the seed has no edges
    for, that is an explicit gap — not a silent empty result."""
    # A Method has CALLS / CONTAINS edges but no IMPORTS (IMPORTS is File-level).
    out = recall(recall_db, CTRL_METHOD, depth=1, limit=25, relations=["IMPORTS"])

    assert out["gaps"] and any("IMPORTS" in g for g in out["gaps"])
    # No related items surfaced for the empty relation (seed is not echoed as related).
    assert all(it["relation"] != "IMPORTS" for it in out["items"])


FORBIDDEN_GENERATIVE_MODULES = [
    "openai", "anthropic", "langchain", "langchain_core", "llama_index",
    "transformers", "cohere", "neo4j_graphrag", "vertexai",
    "google.generativeai", "litellm", "sentence_transformers",
]


def test_recall_path_imports_no_generative_module_and_keeps_fields_separate(recall_db):
    """ac-3 no-laundering: importing the recall path pulls in NO LLM/generative
    module, and the output keeps items/sources/gaps/confidence SEPARATE (no
    merged prose 'answer')."""
    # (a) Behavioral: a fresh interpreter that imports only the recall path must
    # load none of the generative libraries.
    src = Path(__file__).parents[2] / "src"
    probe = (
        "import importlib, sys, json\n"
        "importlib.import_module('palimpsest.recall')\n"
        f"forbidden = {FORBIDDEN_GENERATIVE_MODULES!r}\n"
        "bad = sorted({m for m in sys.modules for p in forbidden "
        "if m == p or m.startswith(p + '.')})\n"
        "print(json.dumps(bad))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True, text=True, check=True,
    )
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], f"recall path imported generative modules: {loaded}"

    # (b) Structural: separated fields, no laundered prose field.
    out = recall(recall_db, CTRL_METHOD, depth=1, limit=25)
    assert set(out) == {
        "items", "sources", "summaries", "gaps", "confidence", "expand_handle",
    }
    for laundered in ("answer", "summary", "text", "response", "narrative"):
        assert laundered not in out
    assert isinstance(out["items"], list)
    assert isinstance(out["sources"], list)
    assert isinstance(out["gaps"], list)
    assert isinstance(out["confidence"], (int, float))
    # Each item's structural signal stays inside its own grounded record.
    for it in out["items"]:
        assert set(it["sources"]) == {
            "source_commit", "path", "start_line", "end_line", "committed_at",
        }
