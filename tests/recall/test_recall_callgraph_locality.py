"""TDD for the callgraph-locality recall channel: the cross-package CALLS signal.

A detect-only, global read-only channel (facet-2, composing with the facet-1
``resolution`` marker) that measures, per Java caller Method, the share of its
outgoing typed CALLS whose callee lives in a DIFFERENT Package than the caller.
BOUNDARY = Package (NOT Community — Community is definitionally vacuous for this
question, which is what killed the constant-zero Community design). Mirrors the
sibling recall_edge_precision: SEPARATE global entry point, standard result
shape, parameterized + id-ordered + LIMIT-bounded Cypher.

Two tiers (mirrors test_recall_edge_precision.py):

* **Mock-unit** — a fake driver returning canned aggregated rows, no Neo4j. Pins
  the DETERMINISM contract (ac-1: id-ordered + LIMIT-bounded, ``$lim`` the sole
  parameter, ``.java`` / ``typed`` / ``name`` dev literals never interpolated, a
  VARIABLE-LENGTH ``[:CONTAINS*]`` climb not a hard-coded path, no MEMBER_OF /
  Community), the GROUNDED-triple-not-content-verdict projection + author
  non-leak (ac-2), the DEFAULT-PACKAGE / ALL-UNRESOLVED buckets (never collapsed
  as same-package, never 0/0 NaN), and the predate-empty re-ingest gap (ac-3).
* **Live-Neo4j** — the ``recall_db`` fixture (the real ``commute`` Java slice).
  The NON-VACUITY WITNESS: a genuine cross-package TYPED call
  (CommuteController -> CommuteService.selectAttedanceCondition, across the
  ``controller`` / ``service`` packages) flags >=1 Method with cross_ratio > 0
  (guards the constant-zero regression), while the ``selectCodeList``
  name-collision (ReportService also declares it -> resolution='name') is carried
  in name_calls, NEVER in the cross numerator.
"""

import json

from palimpsest.recall import recall, recall_callgraph_locality
from palimpsest.recall.graphrag import (
    _CALLGRAPH_LOCALITY,
    _CALLGRAPH_LOCALITY_GAP,
)

RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions", "relations",
    "gaps", "confidence", "expand_handle",
}

# A distinctive author string — the channel must NEVER surface it (Method nodes
# carry an author, but only scalar endpoint projections are returned).
SECRET = "SECRET <secret.person@corp.example>"

CTRL = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"
SVC = "src/main/java/kr/co/ecoletree/service/commute/service/CommuteService.java"


# --- fake driver (mock-unit tier, no Neo4j) -----------------------------------

class _FakeRecord:
    def __init__(self, data):
        self._data = data

    def data(self):
        return self._data


class _FakeResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)


class _FakeSession:
    def __init__(self, rows, log):
        self._rows, self._log = rows, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self._log.append((query, params))
        return _FakeResult([_FakeRecord(dict(r)) for r in self._rows])


class _FakeDriver:
    def __init__(self, rows):
        self._rows, self.log = rows, []

    def session(self, *a, **k):
        return _FakeSession(self._rows, self.log)


def _row(id_, cross, same, unresolved, name_calls, cross_ratio,
         default_package=False):
    """A canned aggregated row, shaped exactly like the query's per-caller RETURN.
    It carries NO author key — the query projects endpoint scalars only."""
    return {
        "id": id_,
        "labels": ["Method"],
        "name": id_.rsplit(".", 1)[-1],
        "qualified_name": id_,
        "path": CTRL,
        "start_line": 10,
        "end_line": 20,
        "source_commit": "c0ffee",
        "committed_at": "2025-09-03T16:22:54+09:00",
        "cross": cross,
        "same": same,
        "unresolved": unresolved,
        "name_calls": name_calls,
        "default_package": default_package,
        "cross_ratio": cross_ratio,
    }


# --- mock-unit: determinism + Package boundary contract (ac-1) -----------------

def test_query_is_parameterized_ordered_and_package_scoped():
    """ac-1: the Cypher is id-ordered + LIMIT-bounded, ``$lim`` is the SOLE
    parameter, ``.java`` / ``typed`` / ``name`` are dev literals baked in (never
    interpolated), the Method->Package climb is a VARIABLE-LENGTH ``[:CONTAINS*]``
    (nested classes add hops — never a hard-coded fixed path), and Community /
    MEMBER_OF are never touched (Package is the boundary)."""
    q = _CALLGRAPH_LOCALITY
    assert "ORDER BY id" in q                 # total order -> rebuild-deterministic
    assert "LIMIT $lim" in q                  # server-side bound, parameterized
    assert q.count("$") == 1                  # $lim is the ONLY parameter
    assert "'.java'" in q                     # scope to Java, dev literal
    assert "'typed'" in q and "'name'" in q   # typed/name split, dev literals
    assert "{" not in q and "}" not in q      # no str.format interpolation slot
    assert "author" not in q                  # scalar projections only, no leak
    # variable-length CONTAINS climb (NOT a fixed 4-hop path)
    assert "[:CONTAINS*]" in q
    assert ":Package" in q
    # Package boundary — Community / MEMBER_OF are definitionally out of scope
    assert "MEMBER_OF" not in q and "Community" not in q


def test_items_preserve_query_order_and_pass_lim():
    """ac-1: items map 1:1 to the (already id-ordered) query rows, and the caller
    ``limit`` is forwarded as the sole ``$lim`` parameter."""
    rows = [
        _row("a.f", cross=1, same=1, unresolved=0, name_calls=0, cross_ratio=0.5),
        _row("b.h", cross=0, same=2, unresolved=0, name_calls=0, cross_ratio=0.0),
    ]
    drv = _FakeDriver(rows)
    out = recall_callgraph_locality(drv, limit=7)
    assert set(out) == RESULT_KEYS
    assert [it["id"] for it in out["items"]] == ["a.f", "b.h"]
    assert drv.log and drv.log[0][1] == {"lim": 7}


# --- mock-unit: grounded TRIPLE, no verdict, no author leak (ac-2) -------------

def test_items_carry_grounded_triple_no_verdict_no_author():
    """ac-2: each flag is a GROUNDED observation carrying the cross/same/unresolved
    TRIPLE + ratio (+ name_calls kept SEPARATE), NOT a content/quality verdict, and
    no author string ever surfaces."""
    rows = [_row("a.f", cross=2, same=1, unresolved=1, name_calls=3, cross_ratio=0.667)]
    out = recall_callgraph_locality(_FakeDriver(rows), limit=25)
    it = out["items"][0]
    # grounded provenance
    assert it["sources"]["source_commit"] and it["sources"]["path"]
    assert it["sources"]["start_line"] is not None
    # the cross/same/unresolved TRIPLE + ratio
    assert it["cross"] == 2 and it["same"] == 1 and it["unresolved"] == 1
    assert it["cross_ratio"] == 0.667
    # name-collision calls carried SEPARATELY, never folded into cross
    assert it["name_calls"] == 3
    assert it["cross"] != it["cross"] + it["name_calls"]  # name never in numerator
    # NO content-verdict field of any kind
    for banned in ("verdict", "quality", "score", "severity", "defect", "risk"):
        assert banned not in it
    # author non-leak — nowhere in the serialized result
    blob = json.dumps(out)
    assert "author" not in blob and SECRET not in blob


def test_default_package_bucket_not_collapsed_as_same():
    """ac-2/ac-3: a DEFAULT-PACKAGE caller (no Package node) is a DISTINCT bucket —
    flagged ``default_package`` with no computable locality (cross==same==0) — NEVER
    silently counted as same-package."""
    rows = [_row("d.g", cross=0, same=0, unresolved=0, name_calls=0,
                 cross_ratio=0.0, default_package=True)]
    out = recall_callgraph_locality(_FakeDriver(rows), limit=25)
    it = out["items"][0]
    assert it["default_package"] is True
    assert it["cross"] == 0 and it["same"] == 0   # NOT collapsed into same
    # the standing gap discloses the default-package bucket
    assert any("default" in g.lower() for g in out["gaps"])


def test_all_unresolved_bucket_is_not_nan():
    """ac-2: an ALL-CALLEES-UNRESOLVED caller has a real 0.0 ratio (never a 0/0
    NaN) and surfaces its unresolved count as its own bucket."""
    rows = [_row("u.v", cross=0, same=0, unresolved=4, name_calls=0, cross_ratio=0.0)]
    out = recall_callgraph_locality(_FakeDriver(rows), limit=25)
    it = out["items"][0]
    assert it["cross_ratio"] == 0.0              # real float, not NaN/None
    assert it["cross_ratio"] == it["cross_ratio"]  # NaN != NaN would fail
    assert it["unresolved"] == 4                 # its own bucket, disclosed
    assert it["cross"] == 0 and it["same"] == 0


def test_standing_gap_discloses_absence_and_splits():
    """ac-2/ac-3: the standing gap is ALWAYS present and discloses the honesty
    axes — zero-CALLS methods are ABSENT (absence != high locality), name-collision
    calls are carried separately, unresolved-package callees are excluded from the
    denominator, and completeness is NOT claimed."""
    out = recall_callgraph_locality(_FakeDriver([_row("a.f", 1, 0, 0, 0, 1.0)]), 25)
    gap = _CALLGRAPH_LOCALITY_GAP
    assert gap in out["gaps"]
    low = gap.lower()
    assert "absence" in low                       # zero-CALLS absence disclosed
    assert "name" in low                          # name-collision split disclosed
    assert "unresolved" in low                    # unresolved bucket disclosed
    assert "not" in low and "complet" in low      # completeness NOT claimed


def test_empty_result_emits_predate_reingest_gap():
    """ac-3: an EMPTY result gets a DISTINCT predate/re-ingest advisory ON TOP of
    the standing gap — a bare empty would be a FALSE all-clear for a graph ingested
    before the resolution / CONTAINS spine landed."""
    out = recall_callgraph_locality(_FakeDriver([]), limit=25)
    assert out["items"] == []
    assert out["confidence"] == 0.0
    assert _CALLGRAPH_LOCALITY_GAP in out["gaps"]        # standing gap still present
    predate = [g for g in out["gaps"] if "predate" in g.lower()]
    assert predate                                        # DISTINCT advisory added
    assert "re-ingest" in predate[0].lower()
    assert len(out["gaps"]) == 2                          # clean-empty distinguished


# --- live-Neo4j: NON-VACUITY WITNESS + real buckets (ac-1 / ac-3) --------------

def test_live_flags_cross_package_typed_call(recall_db):
    """NON-VACUITY WITNESS (ac-1/ac-3, guards the constant-zero regression that
    killed the Community design): on the ingested ``commute`` slice the controller
    makes a genuine cross-package TYPED call (CommuteController ->
    CommuteService.selectAttedanceCondition, ``controller`` -> ``service``), so at
    least one Method is flagged with cross_ratio > 0 and cross >= 1."""
    out = recall_callgraph_locality(recall_db, limit=200)
    assert set(out) == RESULT_KEYS
    assert out["items"], "expected Java callers on the ingested corpus"
    crossers = [it for it in out["items"] if it["cross"] > 0]
    assert crossers, "expected >=1 Method with a cross-package typed call (non-dead)"
    assert any(it["cross_ratio"] > 0 for it in crossers)
    # every flagged caller is a .java Method carrying the grounded triple
    for it in out["items"]:
        assert it["sources"]["path"].endswith(".java")
        assert it["kind"] == "Method"
        assert isinstance(it["cross"], int) and isinstance(it["same"], int)
        assert isinstance(it["unresolved"], int)
        assert it["cross_ratio"] == it["cross_ratio"]     # never NaN
    # non-empty -> the predate advisory is NOT emitted (clean, not predate)
    assert not any("predate" in g.lower() for g in out["gaps"])
    assert _CALLGRAPH_LOCALITY_GAP in out["gaps"]


# A minimal, isolated name-collision witness graph (ids all under this prefix so
# cleanup deletes ONLY it, leaving the shared recall_db corpus intact). It pins the
# Cypher's typed/name SPLIT on real Neo4j: a caller with ONE typed cross-package call
# and TWO resolution='name' calls (to same-simple-name methods in OTHER packages) must
# report cross=1 (the name calls stay OUT of the numerator), never cross=3.
_WITNESS_ID = "wtns:"
_WITNESS_SETUP = """
CREATE (pa:Package {id:'wtns:pkg.a', qualified_name:'wtns.pkg.a', name:'a'})
CREATE (pb:Package {id:'wtns:pkg.b', qualified_name:'wtns.pkg.b', name:'b'})
CREATE (pc:Package {id:'wtns:pkg.c', qualified_name:'wtns.pkg.c', name:'c'})
CREATE (fa:File {id:'wtns:a.java', path:'wtns/a.java'})
CREATE (ca:Class {id:'wtns:A', path:'wtns/a.java'})
CREATE (caller:Method {id:'wtns:A.run', qualified_name:'wtns.A.run', name:'run',
                       path:'wtns/a.java', start_line:1, end_line:2,
                       source_commit:'w0', committed_at:'2025-01-01T00:00:00Z'})
CREATE (bx:Method {id:'wtns:B.x', qualified_name:'wtns.B.x', name:'x', path:'wtns/b.java'})
CREATE (bd:Method {id:'wtns:B.dup', qualified_name:'wtns.B.dup', name:'dup', path:'wtns/b.java'})
CREATE (cd:Method {id:'wtns:C.dup', qualified_name:'wtns.C.dup', name:'dup', path:'wtns/c.java'})
CREATE (fb:File {id:'wtns:b.java', path:'wtns/b.java'})
CREATE (cb:Class {id:'wtns:B', path:'wtns/b.java'})
CREATE (fc:File {id:'wtns:c.java', path:'wtns/c.java'})
CREATE (cc:Class {id:'wtns:C', path:'wtns/c.java'})
CREATE (pa)-[:CONTAINS]->(fa)-[:CONTAINS]->(ca)-[:CONTAINS]->(caller)
CREATE (pb)-[:CONTAINS]->(fb)-[:CONTAINS]->(cb)
CREATE (cb)-[:CONTAINS]->(bx)
CREATE (cb)-[:CONTAINS]->(bd)
CREATE (pc)-[:CONTAINS]->(fc)-[:CONTAINS]->(cc)-[:CONTAINS]->(cd)
CREATE (caller)-[:CALLS {resolution:'typed'}]->(bx)
CREATE (caller)-[:CALLS {resolution:'name'}]->(bd)
CREATE (caller)-[:CALLS {resolution:'name'}]->(cd)
"""
_WITNESS_TEARDOWN = "MATCH (n) WHERE n.id STARTS WITH $p DETACH DELETE n"


def test_live_name_calls_do_not_inflate_cross(recall_db):
    """ac-1/ac-2 (typed/name split on real Neo4j): a caller with 1 typed cross-package
    call and 2 resolution='name' calls (to same-simple-name methods in OTHER packages)
    reports cross=1 and name_calls=2 — the name-collision calls are carried SEPARATELY,
    never folded into the cross numerator (which would give cross=3, cross_ratio!=1.0)."""
    with recall_db.session() as s:
        s.run(_WITNESS_TEARDOWN, p=_WITNESS_ID)   # idempotent pre-clean
        s.run(_WITNESS_SETUP)
    try:
        out = recall_callgraph_locality(recall_db, limit=500)
        caller = next(it for it in out["items"] if it["id"] == "wtns:A.run")
        assert caller["cross"] == 1            # ONLY the typed cross call
        assert caller["same"] == 0
        assert caller["unresolved"] == 0
        assert caller["name_calls"] == 2       # both name calls carried SEPARATELY
        assert caller["cross_ratio"] == 1.0    # 1/(1+0); name calls NOT in numerator
    finally:
        with recall_db.session() as s:
            s.run(_WITNESS_TEARDOWN, p=_WITNESS_ID)


def test_live_grounded_no_author_leak(recall_db):
    """ac-2: on the real graph (Method nodes DO carry an author) no author string
    leaks, and there is no content-verdict field."""
    out = recall_callgraph_locality(recall_db, limit=200)
    blob = json.dumps(out)
    assert "author" not in blob and "@ecoletree.com" not in blob
    for it in out["items"]:
        for banned in ("verdict", "quality", "score", "severity", "defect", "risk"):
            assert banned not in it


def test_live_locality_not_reachable_via_default_recall(recall_db):
    """The locality triple is a CHANNEL-derived aggregate, not a node property —
    ordinary recall over the caller never turns it into an item field."""
    out = recall(recall_db, CTRL, depth=2, limit=50)
    for it in out["items"]:
        assert "cross_ratio" not in it and "cross" not in it
