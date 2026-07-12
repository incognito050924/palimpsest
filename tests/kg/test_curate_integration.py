"""AC4 / AC5 / AC7 / AC8 — curate -> git-materialise -> existing loader, end to end.

Exercises the whole Candidate B vertical against a live Neo4j (kg conftest rig):
the isolated ``curate`` CLI (LLM stubbed) freezes a grounded payload to a git-SoT
directory, then the UNMODIFIED idempotent inferred loader ingests it. Reuses the
``ingested`` fixture so grounding refs resolve to real code nodes.
"""

import json

from palimpsest.cli import _read_payload_file, main
from palimpsest.ir import CLASS, METHOD
from palimpsest.kg import create_constraints, ingest, load_summaries

GENERATOR = "ditto-curator"          # honest: not palimpsest itself
MODEL = "claude-opus-4-8"            # the real generation model
SOURCE_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"


def _stub_llm(method_qn, klass_qn):
    """One deterministic LLM response grounded in the two real fixture nodes."""
    return json.dumps(
        {
            "claims": [
                {"text": "Handles the go-to-work punch-in flow.", "source_refs": [method_qn]},
                {"text": "Coupled to its declaring class.", "source_refs": [klass_qn]},
            ],
            "gap": "Leave/holiday punch variants are not covered.",
            "confidence": 0.82,
        }
    )


def _materialise(tmp_path, monkeypatch, ir):
    """Run the curate CLI (stubbed LLM) and return the git-SoT payload directory."""
    method = next(n for n in ir.nodes if n.kind == METHOD)
    klass = next(n for n in ir.nodes if n.kind == CLASS)
    import palimpsest.curate as curate_pkg

    monkeypatch.setattr(
        curate_pkg, "default_generate",
        lambda prompt, **kw: _stub_llm(method.qualified_name, klass.qualified_name),
    )
    out = tmp_path / "summaries"
    rc = main([
        "curate",
        "--target", method.qualified_name,
        "--ground", method.qualified_name,
        "--ground", klass.qualified_name,
        "--facts", "punchIn() records the go-to-work punch; declared in the controller.",
        "--generator", GENERATOR,
        "--model", MODEL,
        "--source-commit", SOURCE_COMMIT,
        "--created-at", "2026-07-12T09:00:00+09:00",
        "--out", str(out),
    ])
    assert rc == 0
    return out


def _load_dir(driver, payload_dir):
    summaries = [s for p in sorted(payload_dir.glob("*.json")) for s in _read_payload_file(p)]
    return load_summaries(driver, summaries)


def _edge_kind_counts(driver):
    with driver.session() as session:
        total = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        det = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind = 'deterministic' RETURN count(r) AS c"
        ).single()["c"]
        inferred = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind = 'inferred' RETURN count(r) AS c"
        ).single()["c"]
        missing = session.run(
            "MATCH ()-[r]->() WHERE r.edge_kind IS NULL RETURN count(r) AS c"
        ).single()["c"]
    return total, det, inferred, missing


def _snapshot(driver):
    """(node ids, edge tuples) — the identity a rebuild must reproduce exactly."""
    with driver.session() as session:
        nodes = frozenset(
            r["id"] for r in session.run("MATCH (n) WHERE n.id IS NOT NULL RETURN n.id AS id")
        )
        edges = frozenset(
            (r["s"], r["t"], r["dst"], r["k"])
            for r in session.run(
                "MATCH (a)-[r]->(b) RETURN a.id AS s, type(r) AS t, b.id AS dst, r.edge_kind AS k"
            )
        )
    return nodes, edges


def test_curate_payload_loads_as_inferred_with_grounding(ingested, ir, tmp_path, monkeypatch):
    """AC4: loading the materialised payload creates inferred SUMMARIZES edges,
    each Summary bound to >=1 grounding edge."""
    payload_dir = _materialise(tmp_path, monkeypatch, ir)
    result = _load_dir(ingested, payload_dir)
    assert result.loaded == 1 and result.rejected == 0

    with ingested.session() as session:
        inferred = session.run(
            "MATCH ()-[r:SUMMARIZES]->() WHERE r.edge_kind = 'inferred' RETURN count(r) AS c"
        ).single()["c"]
        assert inferred >= 1
        per_summary = session.run(
            "MATCH (sm:Summary)-[r:SUMMARIZES]->() RETURN sm.id AS id, count(r) AS c"
        ).data()
    assert per_summary and all(row["c"] >= 1 for row in per_summary)


def test_curate_payload_preserves_no_laundering(ingested, ir, tmp_path, monkeypatch):
    """AC5: det ⊎ inferred == total ∧ NULL == 0 still holds after the curate load."""
    _, det0, inferred0, missing0 = _edge_kind_counts(ingested)
    assert det0 > 0 and inferred0 == 0 and missing0 == 0

    payload_dir = _materialise(tmp_path, monkeypatch, ir)
    _load_dir(ingested, payload_dir)

    total, det, inferred, missing = _edge_kind_counts(ingested)
    assert missing == 0
    assert det + inferred == total
    assert inferred >= 1
    assert det == det0  # deterministic layer untouched


def test_curate_provenance_survives_load_byte_identical(ingested, ir, tmp_path, monkeypatch):
    """AC8 (load half): generator/model are non-palimpsest and byte-preserved by
    the loader — no self-attribution laundering."""
    payload_dir = _materialise(tmp_path, monkeypatch, ir)
    # what the git-SoT file carries, verbatim
    on_disk = _read_payload_file(next(payload_dir.glob("*.json")))[0]
    assert on_disk.generator == GENERATOR != "palimpsest"
    assert on_disk.model == MODEL != "palimpsest"

    _load_dir(ingested, payload_dir)
    with ingested.session() as session:
        row = session.run(
            "MATCH (sm:Summary) RETURN sm.generator AS g, sm.model AS m"
        ).single()
    assert row["g"] == GENERATOR      # byte-identical through the loader
    assert row["m"] == MODEL


def test_curate_rebuild_is_deterministic(clean_db, ir, tmp_path, monkeypatch):
    """AC7 (VG2): ingest+load -> drop everything -> rebuild from the SAME git-SoT
    reproduces an identical graph (nodes, edges, ids, edge_kind, grounding)."""
    payload_dir = _materialise(tmp_path, monkeypatch, ir)

    ingest(clean_db, ir)
    _load_dir(clean_db, payload_dir)
    snap1 = _snapshot(clean_db)

    with clean_db.session() as session:  # Neo4j drop
        session.run("MATCH (n) DETACH DELETE n")
    create_constraints(clean_db)
    ingest(clean_db, ir)                 # rebuild code from its deterministic SoT
    _load_dir(clean_db, payload_dir)     # reload the frozen payload from git-SoT
    snap2 = _snapshot(clean_db)

    assert snap1 == snap2
    # and the rebuild really did materialise the inferred layer (not an empty tie)
    assert any(k == "inferred" for (_s, _t, _d, k) in snap1[1])
