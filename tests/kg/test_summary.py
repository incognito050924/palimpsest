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

from palimpsest.ir import CLASS, EMBEDDING_DIM, METHOD, Summary, SummaryClaim
from palimpsest.kg import load_summaries, summary_id
from palimpsest.kg.summary import VECTOR_INDEX_NAME, create_vector_index


def _vec(value: float = 0.1) -> list[float]:
    """A literal, well-formed embedding of the index dimension (no model call)."""
    return [value] * EMBEDDING_DIM


def _embedded(payload, *, generator=None, model="embed-model-v1", value=0.1):
    """The given payload with an external embedding attached (still grounded)."""
    kwargs = dict(
        embedding=_vec(value),
        embedding_model=model,
        embedding_dim=EMBEDDING_DIM,
    )
    if generator is not None:
        kwargs["generator"] = generator
    return replace(payload, **kwargs)


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


# --- ac-1: an external embedding binds to the Summary node (provider-free) -----


def test_embedding_binds_to_summary_and_reload_is_idempotent(ingested, summary_payload):
    payload = _embedded(summary_payload)
    res = load_summaries(ingested, [payload])
    assert res.loaded == 1 and res.embedded == 1

    def read():
        with ingested.session() as session:
            return session.run(
                "MATCH (s:Summary {target_id: $t}) "
                "RETURN s.embedding AS e, s.embedding_model AS m, "
                "s.embedding_dim AS d",
                t=payload.target_id,
            ).single()

    first = read()
    assert first["e"] == payload.embedding
    assert first["m"] == payload.embedding_model
    assert first["d"] == EMBEDDING_DIM

    # MERGE-on-id: re-loading the identical payload changes nothing.
    load_summaries(ingested, [payload])
    second = read()
    assert second["e"] == first["e"]
    assert second["m"] == first["m"]
    assert summary_count(ingested) == 1


def test_summary_without_embedding_still_loads(ingested, summary_payload):
    """Back-compat: an embedding-less payload loads unchanged (no embedding prop)."""
    res = load_summaries(ingested, [summary_payload])
    assert res.loaded == 1 and res.embedded == 0
    with ingested.session() as session:
        e = session.run(
            "MATCH (s:Summary {target_id: $t}) RETURN s.embedding AS e",
            t=summary_payload.target_id,
        ).single()["e"]
    assert e is None


def test_wrong_dimension_embedding_is_rejected_rest_load(ingested, summary_payload):
    good = _embedded(summary_payload, generator="gen-good")
    bad = replace(
        _embedded(summary_payload, generator="gen-bad"),
        embedding=[0.1] * (EMBEDDING_DIM - 1),
    )
    res = load_summaries(ingested, [good, bad])
    assert res.intended == 2 and res.loaded == 1 and res.rejected == 1
    assert res.embedded == 1
    assert "dim" in res.rejections[0].reason.lower()
    assert summary_count(ingested) == 1


def test_embedding_model_mismatch_is_rejected(ingested, summary_payload):
    """Single-embedding-model-per-index: a different model than the one already
    established for the index is rejected (cosine across models is meaningless)."""
    load_summaries(ingested, [_embedded(summary_payload, model="model-A")])
    other = _embedded(summary_payload, generator="gen-other", model="model-B")
    res = load_summaries(ingested, [other])
    assert res.loaded == 0 and res.rejected == 1
    assert "model" in res.rejections[0].reason.lower()


def test_vector_index_makes_embedded_summary_queryable(ingested, summary_payload):
    payload = _embedded(summary_payload, value=0.2)
    res = load_summaries(ingested, [payload])
    assert res.embedded == 1

    create_vector_index(ingested)  # CREATE ... IF NOT EXISTS + AWAIT ONLINE

    with ingested.session() as session:
        hit = session.run(
            "CALL db.index.vector.queryNodes($name, 1, $q) "
            "YIELD node RETURN node.target_id AS t",
            name=VECTOR_INDEX_NAME,
            q=payload.embedding,
        ).single()
    assert hit is not None and hit["t"] == payload.target_id


def test_load_result_reports_loaded_vs_indexed(ingested, summary_payload):
    """With the index provisioned first, the result surfaces how many embedded
    summaries are actually queryable through it (loaded-vs-indexed visibility)."""
    create_vector_index(ingested)
    a = _embedded(summary_payload, generator="gen-a")
    b = _embedded(summary_payload, generator="gen-b")
    res = load_summaries(ingested, [a, b])
    assert res.embedded == 2
    assert res.indexed == 2


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
