"""TDD for the external-summary loader: payload -> Neo4j inferred layer.

Provider-free (palimpsest calls no LLM): a fixed summary payload is handed in and
loaded, grounded and idempotent. Vertical slices against a LIVE Neo4j (conftest).
The inferred layer must stay SEPARATE from the deterministic structural layer.
"""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from palimpsest.ir import CLASS, METHOD, Summary, SummaryClaim
from palimpsest.kg import load_summaries, summary_id


def node_count(driver) -> int:
    with driver.session() as session:
        return session.run("MATCH (n) RETURN count(n) AS c").single()["c"]


def summary_count(driver) -> int:
    with driver.session() as session:
        return session.run("MATCH (s:Summary) RETURN count(s) AS c").single()["c"]


def summarizes_count(driver) -> int:
    with driver.session() as session:
        return session.run(
            "MATCH (:Summary)-[r:SUMMARIZES]->() RETURN count(r) AS c"
        ).single()["c"]


# --- load: a valid payload materialises Summary + inferred SUMMARIZES ---------


def test_load_creates_summary_and_inferred_summarizes_edge(
    summaries_loaded, summary_payload
):
    driver, res = summaries_loaded
    assert res.intended == 1 and res.loaded == 1 and res.rejected == 0

    with driver.session() as session:
        summary = session.run(
            "MATCH (s:Summary {target_id: $t}) RETURN count(s) AS c",
            t=summary_payload.target_id,
        ).single()["c"]
        total = session.run(
            "MATCH (:Summary)-[r:SUMMARIZES]->() RETURN count(r) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH (:Summary)-[r:SUMMARIZES]->() WHERE r.edge_kind = $k "
            "RETURN count(r) AS c",
            k="inferred",
        ).single()["c"]
        # The inferred layer never wears the deterministic marker, and the
        # Summary label is disjoint from every code label.
        det_marked = session.run(
            "MATCH (:Summary)-[r:SUMMARIZES]->() "
            "WHERE r.edge_kind = 'deterministic' RETURN count(r) AS c"
        ).single()["c"]
        code_labelled_summary = session.run(
            "MATCH (s:Summary) WHERE s:Method OR s:Class OR s:File OR s:Package "
            "OR s:Repo RETURN count(s) AS c"
        ).single()["c"]

    assert summary == 1
    assert total > 0 and inferred == total
    assert det_marked == 0
    assert code_labelled_summary == 0


# --- ac-1: the semantic verdict is a dedicated field, separate from confidence --


def test_semantic_verdict_round_trips_separate_from_confidence():
    """The external judge's verdict rides on its OWN field (not `confidence`, which
    is the generator's self-report) and round-trips through to_dict/from_dict."""
    verdict = {"verdict": "unfaithful", "judge": "ditto", "model": "judge-model-v1"}
    s = Summary(
        target_id="pkg.Cls#m()",
        claims=(SummaryClaim(text="c", source_refs=("pkg.Cls#m()",)),),
        generator="g", model="m", source_commit="deadbeef",
        created_at="2026-07-01T00:00:00Z",
        confidence=0.9,
        semantic_verdict=verdict,
    )
    d = s.to_dict()
    # Distinct fields, not overloaded.
    assert d["semantic_verdict"] == verdict
    assert d["confidence"] == 0.9

    back = Summary.from_dict(d)
    assert back.semantic_verdict == verdict
    assert back.confidence == 0.9


def test_semantic_verdict_absent_defaults_to_none_backward_compatible():
    """A legacy payload without the field still loads (verdict -> None)."""
    legacy = {
        "target_id": "pkg.Cls#m()",
        "claims": [{"text": "c", "source_refs": ["pkg.Cls#m()"]}],
        "generator": "g", "model": "m", "source_commit": "deadbeef",
        "created_at": "2026-07-01T00:00:00Z",
    }
    assert Summary.from_dict(legacy).semantic_verdict is None


# --- provenance: freshness follows the code, not the generator's wall-clock ---


def test_summary_provenance_binds_code_bound_at_to_target(
    summaries_loaded, ir, summary_payload
):
    driver, res = summaries_loaded
    assert res.loaded == 1
    target = ir.node(summary_payload.target_id)
    assert target is not None  # guard fixture assumption

    with driver.session() as session:
        s = session.run(
            "MATCH (s:Summary {target_id: $t}) RETURN s",
            t=summary_payload.target_id,
        ).single()["s"]

    # code_bound_at is the RESOLVED target's committed_at, not a wall-clock.
    assert s["code_bound_at"] == target.provenance.committed_at
    assert s["created_at"] == summary_payload.created_at
    assert s["generator"] == summary_payload.generator is not None
    assert s["model"] == summary_payload.model is not None


# --- honesty: unresolved / mixed / empty / missing-field are REJECTED ---------


def test_reject_unresolved_ref(ingested, summary_payload):
    bad = replace(
        summary_payload,
        claims=(SummaryClaim(text="x", source_refs=("does.not.Exist#nope()",)),),
    )
    res = load_summaries(ingested, [bad])
    assert res.intended == 1 and res.loaded == 0 and res.rejected == 1
    assert "unresolved" in res.rejections[0].reason.lower()
    assert summary_count(ingested) == 0  # not silently dropped, not loaded


def test_reject_mixed_resolution_rejects_whole_summary(ingested, ir, summary_payload):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    mixed = replace(
        summary_payload,
        claims=(
            SummaryClaim(text="grounded", source_refs=(method.qualified_name,)),
            SummaryClaim(text="dangling", source_refs=("ghost.Ref#none()",)),
        ),
    )
    res = load_summaries(ingested, [mixed])
    assert res.loaded == 0 and res.rejected == 1
    # Atomic: the resolvable claim is NOT loaded either.
    assert summary_count(ingested) == 0


def test_reject_zero_claim_summary(ingested, summary_payload):
    empty = replace(summary_payload, claims=())
    res = load_summaries(ingested, [empty])
    assert res.loaded == 0 and res.rejected == 1
    assert "claim" in res.rejections[0].reason.lower()


def test_reject_missing_generator(ingested, summary_payload):
    nogen = replace(summary_payload, generator="")
    res = load_summaries(ingested, [nogen])
    assert res.loaded == 0 and res.rejected == 1
    assert "generator" in res.rejections[0].reason.lower()


def test_batch_rejects_one_and_loads_the_rest(ingested, summary_payload):
    # Same target, different generator -> distinct Summary id, so no id clash.
    bad = replace(
        summary_payload,
        generator="other-generator",
        claims=(SummaryClaim(text="dangling", source_refs=("ghost#x()",)),),
    )
    res = load_summaries(ingested, [summary_payload, bad])
    assert res.intended == 2 and res.loaded == 1 and res.rejected == 1
    assert summary_count(ingested) == 1


# --- safety: adversarial claim text is inert data, not executed Cypher --------


INJECTION = "`}) DETACH DELETE (x) //"


def test_adversarial_claim_text_is_inert(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    before = node_count(ingested)
    payload = Summary(
        target_id=method.qualified_name,
        claims=(SummaryClaim(text=INJECTION, source_refs=(method.qualified_name,)),),
        generator="g",
        model="m",
        source_commit="deadbeef",
        created_at="2026-07-01T00:00:00Z",
    )
    res = load_summaries(ingested, [payload])
    assert res.loaded == 1

    after = node_count(ingested)
    assert after == before + 1  # one Summary added; nothing was deleted

    with ingested.session() as session:
        claims = session.run(
            "MATCH (s:Summary {target_id: $t}) RETURN s.claims AS c",
            t=method.qualified_name,
        ).single()["c"]
    assert any(INJECTION in c for c in claims)  # stored verbatim as data


# --- idempotence: re-loading the same payload changes nothing -----------------


def test_reload_is_idempotent(ingested, summary_payload):
    load_summaries(ingested, [summary_payload])
    first_nodes, first_edges = summary_count(ingested), summarizes_count(ingested)

    load_summaries(ingested, [summary_payload])  # MERGE-on-id -> no duplicates
    second_nodes, second_edges = summary_count(ingested), summarizes_count(ingested)

    assert first_nodes == second_nodes == 1
    assert first_edges == second_edges > 0


# --- namespace isolation: a Summary id never shadows a code node --------------


def test_summary_id_never_shadows_a_code_node(ingested, summary_payload):
    sid = summary_id(
        summary_payload.target_id,
        summary_payload.generator,
        summary_payload.model,
        summary_payload.source_commit,
    )
    # Plant a code node whose id deliberately collides with the summary id.
    with ingested.session() as session:
        session.run(
            "CREATE (m:Method {id: $id, name: 'decoy', committed_at: 'x'})", id=sid
        )

    res = load_summaries(ingested, [summary_payload])
    assert res.loaded == 1

    with ingested.session() as session:
        labelsets = sorted(
            tuple(sorted(r["labels"]))
            for r in session.run(
                "MATCH (n {id: $id}) RETURN labels(n) AS labels", id=sid
            )
        )
        summary = session.run(
            "MATCH (s:Summary {id: $id}) RETURN s.target_id AS t", id=sid
        ).single()

    # Two distinct nodes share the id: the planted Method and the new Summary.
    assert ("Method",) in labelsets and ("Summary",) in labelsets
    assert summary is not None and summary["t"] == summary_payload.target_id


# --- ac-4: importing the kg path pulls in NO generative module ----------------


FORBIDDEN_GENERATIVE_MODULES = [
    "openai", "anthropic", "langchain", "langchain_core", "llama_index",
    "transformers", "cohere", "neo4j_graphrag", "vertexai",
    "google.generativeai", "litellm", "sentence_transformers",
]


def test_kg_import_pulls_no_generative_module():
    """A fresh interpreter importing the kg path (summary loader included) must
    load none of the LLM/generative libraries — provider-free invariant."""
    src = Path(__file__).parents[2] / "src"
    probe = (
        "import importlib, sys, json\n"
        "importlib.import_module('palimpsest.kg')\n"
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
    assert loaded == [], f"kg path imported generative modules: {loaded}"
