"""TDD for the MODIFIES recall channels: churn (hotspots) + co-change.

Live Neo4j (session-scoped ``recall_db`` — the fixture IR is ingested, so the
File nodes and one commit Episode exist). Each test layers its OWN synthetic
Episodes / Files + MODIFIES edges onto the shared graph and tears them down in a
``finally`` (mirrors ``test_traversal_bound``'s gen- cleanup), so the session
graph other recall tests share is left pristine.

Covers ac-3 (bounded, deterministic churn / co-change), the author-omission
regression for these NEW entry points (the first path that makes an
author-bearing Episode reachable), the co-change fan-out row-bound, and
graceful-empty on an empty / unresolved query.
"""

import json

from palimpsest.kg import ingest_modifies
from palimpsest.recall import recall, recall_churn, recall_cochange

SVC_FILE = "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"
CTRL_FILE = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"
# A distinctive author string on the synthetic Episodes: churn/co-change must
# NEVER surface it (the Episode is reachable via MODIFIES for the first time).
SECRET = "SECRET <secret.person@corp.example>"
CA = "2025-09-03T16:22:54+09:00"

RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions", "relations",
    "gaps", "confidence", "expand_handle",
}


def _episode(driver, eid):
    with driver.session() as s:
        s.run(
            "MERGE (e:Episode {id:$id}) "
            "SET e.author=$a, e.committed_at=$ca, e.source_commit=$id",
            id=eid, a=SECRET, ca=CA,
        )


def _synth_file(driver, fid):
    with driver.session() as s:
        s.run("MERGE (f:File {id:$id}) SET f.qualified_name=$id, f.path=$id", id=fid)


def _cleanup(driver):
    with driver.session() as s:
        s.run("MATCH ()-[r:MODIFIES]->() DELETE r")
        s.run("MATCH (e:Episode) WHERE e.id STARTS WITH 'churn-ep-' DETACH DELETE e")
        s.run("MATCH (f:File) WHERE f.id STARTS WITH 'churn-synth/' DETACH DELETE f")


def _modrows(eid, *file_ids):
    return [{"episode_id": eid, "file_id": f, "committed_at": CA} for f in file_ids]


# --- churn (hotspots) ---------------------------------------------------------

def test_churn_ranks_files_by_commit_count_desc(recall_db):
    """ac-3: hotspots are Files ordered by distinct-commit count DESC, bounded,
    run-stable (id tiebreak). SVC touched by 3 commits outranks CTRL by 1."""
    driver = recall_db
    for eid in ("churn-ep-a", "churn-ep-b", "churn-ep-c"):
        _episode(driver, eid)
    rows = _modrows("churn-ep-a", SVC_FILE, CTRL_FILE)
    rows += _modrows("churn-ep-b", SVC_FILE)
    rows += _modrows("churn-ep-c", SVC_FILE)
    ingest_modifies(driver, rows)
    try:
        out = recall_churn(driver, limit=25)
        assert set(out) == RESULT_KEYS
        ids = [it["id"] for it in out["items"]]
        assert ids[:2] == [SVC_FILE, CTRL_FILE]
        counts = {it["id"]: it["churn"] for it in out["items"]}
        assert counts[SVC_FILE] == 3 and counts[CTRL_FILE] == 1
        assert out["gaps"] == []
    finally:
        _cleanup(driver)


def test_churn_graceful_empty_when_no_modifies(recall_db):
    """Empty MODIFIES graph -> explicit gap, never a crash or confident empty."""
    driver = recall_db
    with driver.session() as s:
        s.run("MATCH ()-[r:MODIFIES]->() DELETE r")
    out = recall_churn(driver)
    assert out["items"] == []
    assert out["gaps"] and any("MODIFIES" in g for g in out["gaps"])


# --- co-change ----------------------------------------------------------------

def test_cochange_returns_cofiles_excluding_self(recall_db):
    """ac-3: co-change returns Files touched by the SAME commit as the seed,
    bounded and deterministic, and never echoes the seed File itself."""
    driver = recall_db
    _episode(driver, "churn-ep-x")
    ingest_modifies(driver, _modrows("churn-ep-x", SVC_FILE, CTRL_FILE))
    try:
        out = recall_cochange(driver, SVC_FILE, limit=25)
        assert set(out) == RESULT_KEYS
        ids = [it["id"] for it in out["items"]]
        assert CTRL_FILE in ids
        assert SVC_FILE not in ids          # self excluded
        assert out["gaps"] == []
    finally:
        _cleanup(driver)


def test_cochange_unresolved_seed_is_a_gap(recall_db):
    out = recall_cochange(recall_db, "does/not/exist.java")
    assert out["items"] == []
    assert out["gaps"] and any("did not resolve" in g for g in out["gaps"])


def test_cochange_no_cofiles_is_a_gap(recall_db):
    """A resolvable File with no co-change is an honest gap, not a crash."""
    driver = recall_db
    with driver.session() as s:
        s.run("MATCH ()-[r:MODIFIES]->() DELETE r")
    out = recall_cochange(driver, SVC_FILE)
    assert out["items"] == []
    assert out["gaps"]


# --- author omission (NEW channels) -------------------------------------------

def test_churn_and_cochange_never_expose_author(recall_db):
    """The MODIFIES channels make an author-bearing Episode reachable for the
    first time — the author string must NOT surface in either channel."""
    driver = recall_db
    _episode(driver, "churn-ep-1")
    ingest_modifies(driver, _modrows("churn-ep-1", SVC_FILE, CTRL_FILE))
    try:
        for out in (recall_churn(driver), recall_cochange(driver, SVC_FILE)):
            assert out["items"]
            blob = json.dumps(out)
            assert SECRET not in blob
            assert "secret.person" not in blob
            assert "author" not in blob
    finally:
        _cleanup(driver)


def test_modifies_not_reachable_via_default_recall(recall_db):
    """MODIFIES is outside the traversal whitelist — ordinary recall never reaches
    the Episode, so the author-bearing node cannot leak into items."""
    driver = recall_db
    _episode(driver, "churn-ep-t")
    ingest_modifies(driver, _modrows("churn-ep-t", SVC_FILE))
    try:
        out = recall(driver, SVC_FILE, depth=2, limit=50)
        ids = {it["id"] for it in out["items"]}
        assert "churn-ep-t" not in ids
        assert all(it["kind"] != "Episode" for it in out["items"])
    finally:
        _cleanup(driver)


# --- co-change fan-out row-bound (test_traversal_bound 계열) -------------------

class _CountingResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _CountingSession:
    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def run(self, query, **params):
        records = list(self._inner.run(query, **params))
        self._log.append((query, len(records)))
        return _CountingResult(records)


class _CountingDriver:
    def __init__(self, inner):
        self._inner, self.log = inner, []

    def session(self, *a, **k):
        return _CountingSession(self._inner.session(*a, **k), self.log)


def test_cochange_result_is_row_bounded_server_side(recall_db):
    """A mega-commit co-changing many Files does not stream them all: the
    co-change query returns at most the caller ``limit`` rows."""
    driver = recall_db
    n = 30
    _episode(driver, "churn-ep-mega")
    for i in range(n):
        _synth_file(driver, f"churn-synth/f{i}.java")
    rows = _modrows("churn-ep-mega", SVC_FILE, *[f"churn-synth/f{i}.java" for i in range(n)])
    ingest_modifies(driver, rows)
    try:
        drv = _CountingDriver(driver)
        out = recall_cochange(drv, SVC_FILE, limit=25)
        assert len(out["items"]) <= 25

        cc = [c for q, c in drv.log if "(f2:File)" in q]
        assert cc and max(cc) <= 25    # server-side LIMIT bound
        assert n > 25                  # genuinely more candidates than the bound
    finally:
        _cleanup(driver)
