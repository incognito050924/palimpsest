"""TDD for the dedicated MODIFIES writer (``ingest_modifies``).

Live Neo4j (see conftest ``ingested`` fixture: the fixture IR is ingested, so
the commit Episode and the File nodes already exist). The writer binds an Episode
to the File(s) that commit changed WITHOUT the generic edge path:

* both endpoints are MATCHed, so a changed path with no HEAD File node (deleted,
  never re-added) yields NO edge and NO phantom File (ac-2);
* the edge carries ``edge_kind='deterministic'`` and the commit provenance, but
  NEVER the author (author lives on the Episode, never laundered onto churn);
* MERGE is idempotent (re-running converges, no duplicate edges).
"""

from palimpsest.kg import ingest_modifies

# The fixture provenance (see conftest PROV) — the Episode ingest minted.
EPISODE = "c20b7332d8c60ce73794427a4c28120b085c134d"
COMMITTED_AT = "2025-09-03T16:22:54+09:00"
SVC_FILE = "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
CTRL_FILE = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"


def _row(file_id):
    return {"episode_id": EPISODE, "file_id": file_id, "committed_at": COMMITTED_AT}


def _scalar(driver, cypher, **params):
    with driver.session() as session:
        return session.run(cypher, **params).single()[0]


def test_modifies_writes_episode_to_file_deterministic(ingested):
    n = ingest_modifies(ingested, [_row(SVC_FILE)])
    assert n == 1

    with ingested.session() as s:
        rec = s.run(
            "MATCH (e:Episode {id:$e})-[r:MODIFIES]->(f:File {id:$f}) "
            "RETURN r.edge_kind AS ek, r.source_commit AS sc, r.committed_at AS ca",
            e=EPISODE, f=SVC_FILE,
        ).single()
    assert rec["ek"] == "deterministic"
    assert rec["sc"] == EPISODE
    assert rec["ca"] == COMMITTED_AT


def test_modifies_is_idempotent(ingested):
    ingest_modifies(ingested, [_row(SVC_FILE)])
    ingest_modifies(ingested, [_row(SVC_FILE)])  # re-run converges
    assert _scalar(
        ingested,
        "MATCH (:Episode {id:$e})-[r:MODIFIES]->(:File {id:$f}) RETURN count(r)",
        e=EPISODE, f=SVC_FILE,
    ) == 1


def test_deleted_path_makes_no_phantom_file_and_no_edge(ingested):
    before = _scalar(ingested, "MATCH (f:File) RETURN count(f)")
    n = ingest_modifies(ingested, [_row("ghost/deleted.java")])
    assert n == 0  # the File never resolved, so no edge landed

    assert _scalar(ingested, "MATCH (f:File) RETURN count(f)") == before
    assert _scalar(
        ingested,
        "MATCH (f:File {id:$f}) RETURN count(f)", f="ghost/deleted.java",
    ) == 0
    assert _scalar(
        ingested,
        "MATCH ()-[r:MODIFIES]->(:File {id:$f}) RETURN count(r)",
        f="ghost/deleted.java",
    ) == 0


def test_modifies_edge_carries_no_author(ingested):
    ingest_modifies(ingested, [_row(SVC_FILE)])
    with ingested.session() as s:
        keys = s.run(
            "MATCH (:Episode {id:$e})-[r:MODIFIES]->(:File {id:$f}) "
            "RETURN keys(r) AS k", e=EPISODE, f=SVC_FILE,
        ).single()["k"]
    assert "author" not in keys
