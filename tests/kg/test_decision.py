"""TDD for the DesignDecision loader: payload -> Neo4j inferred layer (wi_260702b48).

Realizes the DesignDecision half of ADR-20260702-risk-designdecision-load-contract:
a DesignDecision is a first-class, externally-generated decision node
(``decision:<sha>``) that DECIDES the code (or other decisions) it commits to,
SUPERSEDES other decisions, and ADDRESSES_RISK Risk nodes. Provider-free
(palimpsest calls no LLM). Beyond storing the decision it enforces GROUNDING —
every decision must have >=1 DECIDES edge and every edge target must resolve to a
real graph node, else it is rejected entity-atomically (nothing written). The
inferred layer stays SEPARATE from the deterministic structural layer
(``edge_kind='inferred'``). Live Neo4j via conftest.
"""

from palimpsest.ir import CLASS, METHOD, DesignDecision, Risk
from palimpsest.kg import decision_id, load_design_decisions, load_risks, risk_id
from palimpsest.recall import recall

DEC_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"  # matches conftest PROV


def _decision(decides, supersedes=(), addresses_risks=(), **over):
    base = dict(
        title="Extract the commute punch-in into its own service",
        decides=tuple(decides),
        supersedes=tuple(supersedes),
        addresses_risks=tuple(addresses_risks),
        generator="fixture-decision-generator",
        model="fixture-model-v1",
        source_commit=DEC_COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    base.update(over)
    return DesignDecision(**base)


def _all_targets(d: DesignDecision):
    return sorted({*d.decides, *d.supersedes, *d.addresses_risks})


def _did(d: DesignDecision) -> str:
    return decision_id(d.title, d.source_commit, _all_targets(d))


def decision_count(driver) -> int:
    with driver.session() as session:
        return session.run(
            "MATCH (d:DesignDecision) RETURN count(d) AS c"
        ).single()["c"]


def decides_edge_count(driver) -> int:
    with driver.session() as session:
        return session.run(
            "MATCH (:DesignDecision)-[e:DECIDES]->() RETURN count(e) AS c"
        ).single()["c"]


def _risk(flags, title="God object: does too much", **over):
    base = dict(
        title=title,
        flags=tuple(flags),
        generator="fixture-risk-generator",
        model="fixture-model-v1",
        source_commit=DEC_COMMIT,
        created_at="2026-07-02T09:00:00+09:00",
    )
    base.update(over)
    return Risk(**base)


# --- load: a grounded payload materialises DesignDecision + inferred DECIDES ----


def test_load_creates_decision_and_inferred_decides_edge(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    dec = _decision(decides=(method.qualified_name,))
    res = load_design_decisions(ingested, [dec])
    assert res.intended == 1 and res.loaded == 1 and res.rejected == 0

    did = _did(dec)
    assert did.startswith("decision:")  # namespace-isolated id

    with ingested.session() as session:
        node = session.run(
            "MATCH (d:DesignDecision {id: $id}) RETURN count(d) AS c", id=did
        ).single()["c"]
        total = session.run(
            "MATCH (:DesignDecision)-[e:DECIDES]->() RETURN count(e) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH (:DesignDecision)-[e:DECIDES]->() WHERE e.edge_kind = 'inferred' "
            "RETURN count(e) AS c"
        ).single()["c"]
        det_marked = session.run(
            "MATCH (:DesignDecision)-[e:DECIDES]->() WHERE e.edge_kind = 'deterministic' "
            "RETURN count(e) AS c"
        ).single()["c"]
        code_labelled = session.run(
            "MATCH (d:DesignDecision) WHERE d:Method OR d:Class OR d:File OR d:Package "
            "OR d:Repo RETURN count(d) AS c"
        ).single()["c"]
        cb = session.run(
            "MATCH (d:DesignDecision {id: $id}) RETURN d.code_bound_at AS cb", id=did
        ).single()["cb"]

    assert node == 1
    assert total > 0 and inferred == total  # every DECIDES edge is inferred
    assert det_marked == 0                  # never the deterministic marker
    assert code_labelled == 0               # DesignDecision disjoint from code labels
    # freshness follows the decided code node, not the generator's wall-clock.
    assert cb == method.provenance.committed_at


# --- entity edges: SUPERSEDES -> DesignDecision, ADDRESSES_RISK -> Risk ---------


def test_supersedes_and_addresses_risk_edges_load_inferred(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    klass = next(n for n in ir.nodes if n.kind == CLASS)

    # Plant a prior DesignDecision (to SUPERSEDES) and a Risk (to ADDRESSES_RISK).
    prior = _decision(decides=(klass.qualified_name,), title="prior decision")
    assert load_design_decisions(ingested, [prior]).loaded == 1
    prior_id = _did(prior)

    risk = _risk(flags=(method.qualified_name,))
    assert load_risks(ingested, [risk]).loaded == 1
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))

    dec = _decision(
        decides=(method.qualified_name,),
        supersedes=(prior_id,),
        addresses_risks=(rid,),
        title="superseding decision",
    )
    res = load_design_decisions(ingested, [dec])
    assert res.loaded == 1
    did = _did(dec)

    with ingested.session() as session:
        sup = session.run(
            "MATCH (:DesignDecision {id: $id})-[e:SUPERSEDES]->(t:DesignDecision {id: $pid}) "
            "WHERE e.edge_kind = 'inferred' RETURN count(e) AS c",
            id=did, pid=prior_id,
        ).single()["c"]
        addr = session.run(
            "MATCH (:DesignDecision {id: $id})-[e:ADDRESSES_RISK]->(t:Risk {id: $rid}) "
            "WHERE e.edge_kind = 'inferred' RETURN count(e) AS c",
            id=did, rid=rid,
        ).single()["c"]
    assert sup == 1
    assert addr == 1


# --- grounding: an unresolved target REJECTS the whole decision (entity-atomic) -


def test_reject_unresolved_decides_target(ingested):
    bad = _decision(decides=("does.not.Exist#nope()",))
    res = load_design_decisions(ingested, [bad])
    assert res.intended == 1 and res.loaded == 0 and res.rejected == 1
    assert "unresolved" in res.rejections[0].reason.lower()
    assert decision_count(ingested) == 0
    assert decides_edge_count(ingested) == 0


def test_reject_unresolved_supersedes_target(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    bad = _decision(
        decides=(method.qualified_name,),
        supersedes=("decision:doesnotexist",),
    )
    res = load_design_decisions(ingested, [bad])
    assert res.loaded == 0 and res.rejected == 1
    assert "unresolved" in res.rejections[0].reason.lower()
    assert decision_count(ingested) == 0  # atomic: nothing written


def test_reject_unresolved_addresses_risk_target(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    bad = _decision(
        decides=(method.qualified_name,),
        addresses_risks=("risk:doesnotexist",),
    )
    res = load_design_decisions(ingested, [bad])
    assert res.loaded == 0 and res.rejected == 1
    assert decision_count(ingested) == 0


def test_reject_zero_decides(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    risk = _risk(flags=(method.qualified_name,))
    assert load_risks(ingested, [risk]).loaded == 1
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))

    # Has an ADDRESSES_RISK target but ZERO DECIDES -> ungrounded, rejected.
    empty = _decision(decides=(), addresses_risks=(rid,))
    res = load_design_decisions(ingested, [empty])
    assert res.loaded == 0 and res.rejected == 1
    assert "decides" in res.rejections[0].reason.lower()
    assert decision_count(ingested) == 0  # a zero-DECIDES decision never lands


# --- wrong-label entity target: ADDRESSES_RISK -> a Class is rejected -----------


def test_reject_wrong_label_addresses_risk_target(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    # ADDRESSES_RISK must point at a Risk; a Class exists but is the wrong label.
    bad = _decision(
        decides=(method.qualified_name,),
        addresses_risks=(klass.qualified_name,),
    )
    res = load_design_decisions(ingested, [bad])
    assert res.loaded == 0 and res.rejected == 1
    assert decision_count(ingested) == 0


def test_reject_wrong_label_supersedes_target(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    # SUPERSEDES must point at a DesignDecision; a Class is the wrong label.
    bad = _decision(
        decides=(method.qualified_name,),
        supersedes=(klass.qualified_name,),
    )
    res = load_design_decisions(ingested, [bad])
    assert res.loaded == 0 and res.rejected == 1
    assert decision_count(ingested) == 0


def test_reject_missing_generator(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    nogen = _decision(decides=(method.qualified_name,), generator="")
    res = load_design_decisions(ingested, [nogen])
    assert res.loaded == 0 and res.rejected == 1
    assert "generator" in res.rejections[0].reason.lower()
    assert decision_count(ingested) == 0


def test_batch_rejects_one_and_loads_the_rest(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    good = _decision(decides=(method.qualified_name,))
    bad = _decision(decides=("ghost#x()",), title="dangling decision")
    res = load_design_decisions(ingested, [good, bad])
    assert res.intended == 2 and res.loaded == 1 and res.rejected == 1
    assert decision_count(ingested) == 1


# --- idempotence: re-loading the same payload changes nothing (MERGE-on-id) -----


def test_reload_is_idempotent(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    dec = _decision(decides=(method.qualified_name,))

    load_design_decisions(ingested, [dec])
    first_nodes, first_edges = decision_count(ingested), decides_edge_count(ingested)

    load_design_decisions(ingested, [dec])  # MERGE-on-id -> no duplicates
    second_nodes, second_edges = decision_count(ingested), decides_edge_count(ingested)

    assert first_nodes == second_nodes == 1
    assert first_edges == second_edges > 0


# --- partition: deterministic ⊎ inferred == total, no nulls, with DECIDES etc. --


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


def test_decision_edges_preserve_edge_kind_partition(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    det_total0, det0, inferred0, missing0 = _edge_kind_counts(ingested)
    assert det0 == det_total0 > 0 and inferred0 == 0 and missing0 == 0

    # A decision that exercises all three inferred edge types.
    prior = _decision(decides=(klass.qualified_name,), title="prior")
    assert load_design_decisions(ingested, [prior]).loaded == 1
    prior_id = _did(prior)
    risk = _risk(flags=(method.qualified_name,))
    assert load_risks(ingested, [risk]).loaded == 1
    rid = risk_id(risk.title, risk.source_commit, sorted(risk.flags))
    dec = _decision(
        decides=(method.qualified_name,),
        supersedes=(prior_id,),
        addresses_risks=(rid,),
        title="superseding",
    )
    assert load_design_decisions(ingested, [dec]).loaded == 1

    total, deterministic, inferred, missing = _edge_kind_counts(ingested)
    assert missing == 0
    assert deterministic + inferred == total  # partition, no overlap/gap
    assert inferred > 0
    assert deterministic == det0              # deterministic layer untouched
    # every DECIDES/SUPERSEDES/ADDRESSES_RISK edge is inferred; none deterministic.
    with ingested.session() as session:
        inferred_edges = session.run(
            "MATCH ()-[e]->() WHERE type(e) IN ['DECIDES','SUPERSEDES','ADDRESSES_RISK','RISKS'] "
            "RETURN count(e) AS c"
        ).single()["c"]
        inferred_marked = session.run(
            "MATCH ()-[e]->() WHERE type(e) IN ['DECIDES','SUPERSEDES','ADDRESSES_RISK','RISKS'] "
            "AND e.edge_kind = 'inferred' RETURN count(e) AS c"
        ).single()["c"]
    assert inferred_edges == inferred_marked > 0


# --- namespace isolation: a decision id never shadows a code node --------------


def test_decision_id_never_shadows_a_code_node(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    dec = _decision(decides=(method.qualified_name,))
    did = _did(dec)

    # Plant a code node whose id deliberately collides with the decision id.
    with ingested.session() as session:
        session.run(
            "CREATE (m:Method {id: $id, name: 'decoy', committed_at: 'x'})", id=did
        )

    res = load_design_decisions(ingested, [dec])
    assert res.loaded == 1

    with ingested.session() as session:
        labelsets = sorted(
            tuple(sorted(r["labels"]))
            for r in session.run(
                "MATCH (n {id: $id}) RETURN labels(n) AS labels", id=did
            )
        )
        loaded = session.run(
            "MATCH (d:DesignDecision {id: $id}) RETURN d.title AS t", id=did
        ).single()

    assert ("Method",) in labelsets and ("DesignDecision",) in labelsets
    assert loaded is not None and loaded["t"] == dec.title


# --- recall isolation: DECIDES/SUPERSEDES/ADDRESSES_RISK never enter items ------


def test_decision_edges_not_traversed_into_items(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    dec = _decision(decides=(method.qualified_name,))
    did = _did(dec)
    assert load_design_decisions(ingested, [dec]).loaded == 1

    # Default relations: a decision that DECIDES the seed must NOT surface in items.
    res = recall(ingested, method.qualified_name, depth=2)
    assert did not in {it["id"] for it in res["items"]}

    # Even if a caller explicitly asks to traverse DECIDES, it is filtered out
    # (DECIDES is not in the traversal whitelist) — the decision cannot leak.
    res_explicit = recall(
        ingested, method.qualified_name, depth=2, relations=("DECIDES",)
    )
    assert did not in {it["id"] for it in res_explicit["items"]}


# --- ac: the semantic verdict is a dedicated field, separate from confidence ---


def test_semantic_verdict_round_trips_separate_from_confidence():
    verdict = {"verdict": "confirmed", "judge": "ditto", "model": "judge-v1"}
    d = DesignDecision(
        title="t", decides=("pkg.Cls#m()",), supersedes=(), addresses_risks=(),
        generator="g", model="m", source_commit="deadbeef",
        created_at="2026-07-02T00:00:00Z", confidence=0.8, semantic_verdict=verdict,
    )
    data = d.to_dict()
    assert data["semantic_verdict"] == verdict and data["confidence"] == 0.8
    back = DesignDecision.from_dict(data)
    assert back.semantic_verdict == verdict and back.confidence == 0.8


# --- decision-lineage freshness: valid_from/valid_to (bi-temporal, wi_260702c2m) --
# The 2nd freshness axis (§2-bis): a decision is "live" until a newer decision
# SUPERSEDES it — then it is INVALIDATED (valid_to set), never deleted (전이력 보존).
# Computed deterministically from the SUPERSEDES structure — provider-free, no LLM.


def _valid(session, did):
    return session.run(
        "MATCH (d:DesignDecision {id: $id}) "
        "RETURN d.valid_from AS vf, d.valid_to AS vt",
        id=did,
    ).single()


def test_new_decision_is_live_with_valid_from_eq_created_at(ingested, ir):
    method = next(n for n in ir.nodes if n.kind == METHOD)
    dec = _decision(decides=(method.qualified_name,),
                    created_at="2026-07-02T09:00:00+09:00")
    assert load_design_decisions(ingested, [dec]).loaded == 1
    with ingested.session() as session:
        row = _valid(session, _did(dec))
    assert row["vf"] == "2026-07-02T09:00:00+09:00"  # valid_from = created_at
    assert row["vt"] is None                          # live (not yet superseded)


def test_supersede_invalidates_prior_but_preserves_it(ingested, ir):
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    method = next(n for n in ir.nodes if n.kind == METHOD)
    prior = _decision(decides=(klass.qualified_name,), title="prior",
                      created_at="2026-07-01T00:00:00+09:00")
    assert load_design_decisions(ingested, [prior]).loaded == 1
    prior_id = _did(prior)

    newer = _decision(decides=(method.qualified_name,), supersedes=(prior_id,),
                      title="newer", created_at="2026-07-02T00:00:00+09:00")
    assert load_design_decisions(ingested, [newer]).loaded == 1

    with ingested.session() as session:
        prior_v = _valid(session, prior_id)
        newer_v = _valid(session, _did(newer))
        still = session.run(
            "MATCH (d:DesignDecision {id: $id}) RETURN count(d) AS c", id=prior_id
        ).single()["c"]
    assert still == 1                                     # 전이력 보존: not deleted
    assert prior_v["vt"] == "2026-07-02T00:00:00+09:00"  # invalidated at superseder's created_at
    assert newer_v["vt"] is None                          # the superseder is live


def test_reload_superseded_decision_does_not_reset_valid_to(ingested, ir):
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    method = next(n for n in ir.nodes if n.kind == METHOD)
    prior = _decision(decides=(klass.qualified_name,), title="prior",
                      created_at="2026-07-01T00:00:00+09:00")
    assert load_design_decisions(ingested, [prior]).loaded == 1
    prior_id = _did(prior)
    newer = _decision(decides=(method.qualified_name,), supersedes=(prior_id,),
                      title="newer", created_at="2026-07-02T00:00:00+09:00")
    assert load_design_decisions(ingested, [newer]).loaded == 1

    # Re-load the prior decision (same id, MERGE): its lineage invalidation must
    # survive (valid_to is ON-CREATE-only, never reset by a re-MERGE of the node).
    assert load_design_decisions(ingested, [prior]).loaded == 1
    with ingested.session() as session:
        prior_v = _valid(session, prior_id)
    assert prior_v["vt"] == "2026-07-02T00:00:00+09:00"  # NOT reset to null


def test_semantic_verdict_absent_defaults_to_none():
    legacy = {
        "title": "t", "decides": ["pkg.Cls#m()"], "generator": "g", "model": "m",
        "source_commit": "deadbeef", "created_at": "2026-07-02T00:00:00Z",
    }
    assert DesignDecision.from_dict(legacy).semantic_verdict is None
