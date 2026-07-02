"""TDD for the Risk loader: payload -> Neo4j inferred layer (wi_2607021h0).

Realizes the Risk half of ADR-20260702-risk-designdecision-load-contract: a Risk
is a first-class, externally-generated judgment node (``risk:<sha>``) that RISKS
the code node(s) it flags. Provider-free (palimpsest calls no LLM). Beyond storing
the judgment it enforces GROUNDING — every Risk must flag >=1 code node that
resolves to a real graph node, else it is rejected entity-atomically (nothing
written). The inferred layer stays SEPARATE from the deterministic structural
layer (``edge_kind='inferred'``). Live Neo4j via conftest.
"""

from palimpsest.ir import METHOD, Risk
from palimpsest.kg import load_risks, risk_id
from palimpsest.recall import recall

RISK_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"  # matches conftest PROV


def _risk(flags, **over):
    base = dict(
        title="God object: does too much",
        flags=flags,
        generator="fixture-risk-generator",
        model="fixture-model-v1",
        source_commit=RISK_COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    base.update(over)
    return Risk(**base)


def risk_count(driver) -> int:
    with driver.session() as session:
        return session.run("MATCH (r:Risk) RETURN count(r) AS c").single()["c"]


def risks_edge_count(driver) -> int:
    with driver.session() as session:
        return session.run(
            "MATCH (:Risk)-[e:RISKS]->() RETURN count(e) AS c"
        ).single()["c"]


# --- load: a grounded payload materialises Risk + inferred RISKS --------------


def test_load_creates_risk_and_inferred_risks_edge(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    risk = _risk(flags=(method.qualified_name,))
    res = load_risks(ingested, [risk])
    assert res.intended == 1 and res.loaded == 1 and res.rejected == 0

    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))
    assert rid.startswith("risk:")  # namespace-isolated id

    with ingested.session() as session:
        node = session.run(
            "MATCH (r:Risk {id: $id}) RETURN count(r) AS c", id=rid
        ).single()["c"]
        total = session.run(
            "MATCH (:Risk)-[e:RISKS]->() RETURN count(e) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH (:Risk)-[e:RISKS]->() WHERE e.edge_kind = 'inferred' "
            "RETURN count(e) AS c"
        ).single()["c"]
        det_marked = session.run(
            "MATCH (:Risk)-[e:RISKS]->() WHERE e.edge_kind = 'deterministic' "
            "RETURN count(e) AS c"
        ).single()["c"]
        code_labelled_risk = session.run(
            "MATCH (r:Risk) WHERE r:Method OR r:Class OR r:File OR r:Package "
            "OR r:Repo RETURN count(r) AS c"
        ).single()["c"]
        cb = session.run(
            "MATCH (r:Risk {id: $id}) RETURN r.code_bound_at AS cb", id=rid
        ).single()["cb"]

    assert node == 1
    assert total > 0 and inferred == total  # every RISKS edge is inferred
    assert det_marked == 0                  # never the deterministic marker
    assert code_labelled_risk == 0          # Risk label disjoint from code labels
    # freshness follows the flagged code node, not the generator's wall-clock.
    assert cb == method.provenance.committed_at


# --- grounding: an unresolved flag REJECTS the whole Risk (entity-atomic) ------


def test_reject_unresolved_flag_target(ingested):
    bad = _risk(flags=("does.not.Exist#nope()",))
    res = load_risks(ingested, [bad])
    assert res.intended == 1 and res.loaded == 0 and res.rejected == 1
    assert "unresolved" in res.rejections[0].reason.lower()
    # entity-atomic: nothing written — not a floating judgment node, not an edge.
    assert risk_count(ingested) == 0
    assert risks_edge_count(ingested) == 0


def test_reject_mixed_resolution_rejects_whole_risk(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    mixed = _risk(flags=(method.qualified_name, "ghost.Ref#none()"))
    res = load_risks(ingested, [mixed])
    assert res.loaded == 0 and res.rejected == 1
    # atomic: the resolvable flag is NOT loaded either.
    assert risk_count(ingested) == 0


def test_reject_zero_flag_risk(ingested):
    empty = _risk(flags=())
    res = load_risks(ingested, [empty])
    assert res.loaded == 0 and res.rejected == 1
    assert "flag" in res.rejections[0].reason.lower()
    assert risk_count(ingested) == 0  # a zero-grounding judgment never lands


def test_reject_missing_generator(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    nogen = _risk(flags=(method.qualified_name,), generator="")
    res = load_risks(ingested, [nogen])
    assert res.loaded == 0 and res.rejected == 1
    assert "generator" in res.rejections[0].reason.lower()
    assert risk_count(ingested) == 0


def test_reject_missing_model(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    nomodel = _risk(flags=(method.qualified_name,), model="")
    res = load_risks(ingested, [nomodel])
    assert res.loaded == 0 and res.rejected == 1
    assert "model" in res.rejections[0].reason.lower()


def test_batch_rejects_one_and_loads_the_rest(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    good = _risk(flags=(method.qualified_name,))
    bad = _risk(title="dangling risk", flags=("ghost#x()",))
    res = load_risks(ingested, [good, bad])
    assert res.intended == 2 and res.loaded == 1 and res.rejected == 1
    assert risk_count(ingested) == 1


# --- idempotence: re-loading the same payload changes nothing (MERGE-on-id) ----


def test_reload_is_idempotent(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    risk = _risk(flags=(method.qualified_name,))

    load_risks(ingested, [risk])
    first_nodes, first_edges = risk_count(ingested), risks_edge_count(ingested)

    load_risks(ingested, [risk])  # MERGE-on-id -> no duplicates
    second_nodes, second_edges = risk_count(ingested), risks_edge_count(ingested)

    assert first_nodes == second_nodes == 1
    assert first_edges == second_edges > 0


# --- partition: deterministic ⊎ inferred == total, no nulls, with RISKS present -


def _edge_kind_counts(driver):
    with driver.session() as session:
        total = session.run(
            "MATCH ()-[r]->() RETURN count(r) AS c"
        ).single()["c"]
        deterministic = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind = 'deterministic' RETURN count(r) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind = 'inferred' RETURN count(r) AS c"
        ).single()["c"]
        missing = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind IS NULL RETURN count(r) AS c"
        ).single()["c"]
    return total, deterministic, inferred, missing


def test_risks_edges_preserve_edge_kind_partition(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    det_total0, det0, inferred0, missing0 = _edge_kind_counts(ingested)
    assert det0 == det_total0 > 0 and inferred0 == 0 and missing0 == 0

    res = load_risks(ingested, [_risk(flags=(method.qualified_name,))])
    assert res.loaded == 1

    total, deterministic, inferred, missing = _edge_kind_counts(ingested)
    assert missing == 0
    assert deterministic + inferred == total  # partition, no overlap/gap
    assert inferred > 0
    assert deterministic == det0              # deterministic layer untouched
    # every RISKS edge is inferred; none is deterministic.
    with ingested.session() as session:
        risks_total = session.run(
            "MATCH ()-[e:RISKS]->() RETURN count(e) AS c"
        ).single()["c"]
        risks_inferred = session.run(
            "MATCH ()-[e:RISKS]->() WHERE e.edge_kind = 'inferred' RETURN count(e) AS c"
        ).single()["c"]
    assert risks_total == risks_inferred > 0


# --- namespace isolation: a Risk id never shadows a code node -----------------


def test_risk_id_never_shadows_a_code_node(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    risk = _risk(flags=(method.qualified_name,))
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))

    # Plant a code node whose id deliberately collides with the risk id.
    with ingested.session() as session:
        session.run(
            "CREATE (m:Method {id: $id, name: 'decoy', committed_at: 'x'})", id=rid
        )

    res = load_risks(ingested, [risk])
    assert res.loaded == 1

    with ingested.session() as session:
        labelsets = sorted(
            tuple(sorted(r["labels"]))
            for r in session.run(
                "MATCH (n {id: $id}) RETURN labels(n) AS labels", id=rid
            )
        )
        loaded_risk = session.run(
            "MATCH (r:Risk {id: $id}) RETURN r.title AS t", id=rid
        ).single()

    # Two distinct nodes share the id: the planted Method and the new Risk.
    assert ("Method",) in labelsets and ("Risk",) in labelsets
    assert loaded_risk is not None and loaded_risk["t"] == risk.title


# --- recall isolation: RISKS is never traversed into the items channel ---------


def test_risks_not_traversed_into_items(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    risk = _risk(flags=(method.qualified_name,))
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))
    assert load_risks(ingested, [risk]).loaded == 1

    # Default relations: a Risk grounded in the seed must NOT surface in items.
    res = recall(ingested, method.qualified_name, depth=2)
    assert rid not in {it["id"] for it in res["items"]}

    # Even if a caller explicitly asks to traverse RISKS, it is filtered out
    # (RISKS is not in the traversal whitelist) — the Risk still cannot leak.
    res_explicit = recall(ingested, method.qualified_name, depth=2, relations=("RISKS",))
    assert rid not in {it["id"] for it in res_explicit["items"]}


# --- ac: the semantic verdict is a dedicated field, separate from confidence ---


def test_semantic_verdict_round_trips_separate_from_confidence():
    verdict = {"verdict": "confirmed", "judge": "ditto", "model": "judge-v1"}
    r = Risk(
        title="t", flags=("pkg.Cls#m()",), generator="g", model="m",
        source_commit="deadbeef", created_at="2026-07-02T00:00:00Z",
        confidence=0.8, semantic_verdict=verdict,
    )
    d = r.to_dict()
    assert d["semantic_verdict"] == verdict and d["confidence"] == 0.8
    back = Risk.from_dict(d)
    assert back.semantic_verdict == verdict and back.confidence == 0.8


def test_semantic_verdict_absent_defaults_to_none():
    legacy = {
        "title": "t", "flags": ["pkg.Cls#m()"], "generator": "g", "model": "m",
        "source_commit": "deadbeef", "created_at": "2026-07-02T00:00:00Z",
    }
    assert Risk.from_dict(legacy).semantic_verdict is None
