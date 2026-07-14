"""wi_260714v6m (issue #19 항목②) — the runtime-coverage test-impact overlay.

WHY THESE TESTS EXIST
  This overlay applies ADR-20260706 §결정6's isolated-producer / HEAD-only auxiliary
  overlay pattern to the test-impact accuracy axis. Static CALLS is a LOWER BOUND
  (reflection/DI/polymorphic test callers are invisible to it, ``graphrag.py``
  ``_STATIC_LOWER_BOUND_GAP``); runtime coverage — measured OUTSIDE palimpsest by a
  producer and handed in as a producer-neutral payload — catches what static misses.

  ac-2 (edge_kind 3값 + COVERS out of REL_TYPES): a runtime coverage fact is an
    OBSERVED execution fact — neither a deterministic structural edge nor an inferred
    semantic one — so ``edge_kind`` gains a third value ``runtime``. The COVERS edge
    type is DELIBERATELY absent from ``REL_TYPES`` (the CALLS_API precedent): the
    generic deterministic writer can never stamp it, and the backfill per-commit loop
    never touches it — the dedicated ``load_coverage`` loader is the ONLY producer.

Scope-local MOCK-UNIT (the calls_api precedent): the loader + recall are exercised
against an in-memory fake Neo4j driver that answers the resolution/MERGE/recall
queries and MERGE-dedups edges. No testcontainer, no network.
"""

from pathlib import Path

from palimpsest.extract import extract
from palimpsest.ir import (
    EDGE_KIND_DETERMINISTIC,
    EDGE_KIND_INFERRED,
    EDGE_KIND_RUNTIME,
    COVERS,
    Provenance,
)
from palimpsest.kg import ingest
from palimpsest.kg.coverage import CoverageRecord, load_coverage
from palimpsest.kg.ingest import REL_TYPES
from palimpsest.recall.graphrag import (
    _STATIC_LOWER_BOUND_GAP,
    recall_runtime_test_impact,
    recall_test_impact,
)

COMMITTED = "2025-09-03T16:22:54+09:00"
HEAD = "c20b7332d8c60ce73794427a4c28120b085c134d"

TEST_QN = "test.OrderTest#covers_place"
PROD_QN = "app.OrderService#place"


# --- in-memory fake Neo4j driver (mock-unit; MERGE-dedups on (src, dst)) --------------
# Mirrors tests/kg/test_calls_api.py: no testcontainer, no network. Answers the loader's
# qualified_name resolution + COVERS MERGE, and (below) the recall traversal.


class _Rec:
    def __init__(self, d):
        self._d = d

    def __getitem__(self, k):
        return self._d[k]

    def get(self, k, default=None):
        return self._d.get(k, default)

    def data(self):
        return dict(self._d)


class _Result:
    def __init__(self, rows):
        self._rows = [_Rec(r) for r in rows]

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, drv):
        self._d = drv

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **p):
        d = self._d
        if "[r:COVERS]" in query and "MERGE" in query:            # MERGE the runtime edge
            d.merged[(p["src"], p["dst"])] = dict(p)
            return _Result([])
        if "t.is_test = true" in query:                          # resolve the TEST endpoint
            n = d.methods.get(p["qn"])
            if n and n.get("is_test"):
                return _Result([{"id": n["id"], "committed_at": n.get("committed_at")}])
            return _Result([])
        if "MATCH (m:Method {qualified_name: $qn})" in query:    # resolve a PRODUCTION endpoint
            n = d.methods.get(p["qn"])
            if n:
                return _Result([{"id": n["id"], "committed_at": n.get("committed_at")}])
            return _Result([])
        if "(prod:Method {id: $id})<-[r:COVERS]-(t:Method)" in query:   # recall: prod -> covering tests
            rows = [
                {**d.methods_by_id[src], **d.merged[(src, dst)]}
                for (src, dst) in sorted(d.merged) if dst == p["id"]
            ]
            return _Result(rows)
        raise AssertionError(f"unexpected query: {query[:70]!r}")


class _FakeDriver:
    def __init__(self, methods):
        # methods: {qualified_name: {"id":.., "is_test":bool, "committed_at":..}}
        self.methods = dict(methods)
        self.methods_by_id = {m["id"]: {**m, "qualified_name": qn}
                              for qn, m in methods.items()}
        self.merged = {}

    def session(self, *a, **k):
        return _Session(self)


def _methods():
    return {
        TEST_QN: {"id": TEST_QN, "is_test": True, "committed_at": COMMITTED},
        PROD_QN: {"id": PROD_QN, "is_test": False, "committed_at": COMMITTED},
    }


# --- ac-2: edge_kind 3-value framing + COVERS is a dedicated-loader-only edge --------


def test_edge_kind_has_three_distinct_values():
    """Runtime coverage is an OBSERVED execution fact — a third edge_kind category
    beside the deterministic structural layer and the inferred semantic layer."""
    assert EDGE_KIND_RUNTIME == "runtime"
    assert len({EDGE_KIND_DETERMINISTIC, EDGE_KIND_INFERRED, EDGE_KIND_RUNTIME}) == 3


def test_covers_edge_is_absent_from_rel_types():
    """COVERS is written ONLY by the dedicated ``load_coverage`` loader (CALLS_API
    precedent): keeping it out of REL_TYPES means the generic deterministic writer
    can never stamp it and the backfill per-commit loop never materialises it."""
    assert COVERS == "COVERS"
    assert COVERS not in REL_TYPES


# --- ac-3: producer-neutral load_coverage — resolve, reject-on-unresolved, idempotent --


def test_load_coverage_merges_runtime_covers_edge():
    """A per-test coverage record (test T covered production M at runtime) MERGEs one
    COVERS edge stamped edge_kind='runtime' + the measured HEAD source_commit."""
    drv = _FakeDriver(_methods())
    rec = CoverageRecord(test_qualified_name=TEST_QN, covered=(PROD_QN,), source_commit=HEAD)
    res = load_coverage(drv, [rec])
    assert res.loaded == 1
    assert (TEST_QN, PROD_QN) in drv.merged
    e = drv.merged[(TEST_QN, PROD_QN)]
    assert e["edge_kind"] == EDGE_KIND_RUNTIME     # NOT deterministic, NOT inferred
    assert e["source_commit"] == HEAD              # the built HEAD commit measured
    assert e["code_bound_at"] == COMMITTED         # freshness follows the covered code


def test_load_coverage_is_idempotent():
    """Re-running over an unchanged graph merges the SAME single edge (MERGE-on-(src,dst))
    — a rebuildable projection, no duplicate."""
    drv = _FakeDriver(_methods())
    rec = CoverageRecord(TEST_QN, (PROD_QN,), HEAD)
    load_coverage(drv, [rec])
    load_coverage(drv, [rec])
    keys = [k for k in drv.merged if k == (TEST_QN, PROD_QN)]
    assert len(keys) == 1


def test_load_coverage_rejects_unresolved_test():
    """An unresolved TEST endpoint (not an is_test Method in the graph) REJECTS the whole
    record — no edge, and the reason is SURFACED (never a silent no-op)."""
    drv = _FakeDriver(_methods())
    rec = CoverageRecord("test.Ghost#nope", (PROD_QN,), HEAD)
    res = load_coverage(drv, [rec])
    assert res.loaded == 0
    assert res.rejected == 1
    assert drv.merged == {}
    assert any("test.Ghost#nope" in r for r in res.reasons)


def test_load_coverage_surfaces_unresolved_covered_not_silent():
    """A covered method that resolves to no node (coverage naturally spans unmodeled /
    third-party code) does NOT silently vanish: the resolved edge still loads, and the
    unresolved target is surfaced in the reasons."""
    drv = _FakeDriver(_methods())
    rec = CoverageRecord(TEST_QN, (PROD_QN, "ext.Unmodeled#x"), HEAD)
    res = load_coverage(drv, [rec])
    assert res.loaded == 1
    assert (TEST_QN, PROD_QN) in drv.merged
    assert (TEST_QN, "ext.Unmodeled#x") not in drv.merged
    assert any("ext.Unmodeled#x" in r for r in res.reasons)


# --- ac-4: the recall overlay is provenance-SEPARATED from the static CALLS channel ---


def test_recall_runtime_overlay_tags_covers_and_runtime():
    """The overlay channel surfaces the runtime-covering test tagged relation=COVERS +
    edge_kind='runtime' — provenance-distinct from the static channel's relation=CALLS,
    so an overlay result is never mistaken for a static one."""
    drv = _FakeDriver(_methods())
    load_coverage(drv, [CoverageRecord(TEST_QN, (PROD_QN,), HEAD)])
    res = recall_runtime_test_impact(drv, PROD_QN)
    ids = [it["id"] for it in res["items"]]
    assert TEST_QN in ids
    it = next(it for it in res["items"] if it["id"] == TEST_QN)
    assert it["relation"] == COVERS                    # NOT CALLS — separated channel
    assert it["edge_kind"] == EDGE_KIND_RUNTIME        # observed layer, not deterministic/inferred


def test_recall_runtime_overlay_augments_not_replaces_static():
    """The overlay is opt-in + HEAD-only: with no coverage loaded it returns NO items but
    discloses its nature (absence != 'no test covers it'), and it NEVER emits the static
    channel's lower-bound gap — the static channel keeps its own disclosure, untouched."""
    drv = _FakeDriver(_methods())
    res = recall_runtime_test_impact(drv, PROD_QN)     # no coverage loaded
    assert res["items"] == []
    disclosure = " ".join(res["gaps"]).lower()
    assert "opt-in" in disclosure or "head-only" in disclosure
    # Separation: the runtime overlay does NOT carry the static lower-bound disclosure
    # (that stays on recall_test_impact, the static channel this overlay augments).
    assert _STATIC_LOWER_BOUND_GAP not in res["gaps"]


# --- ac-5: HEAD-only isolation — the overlay never enters the per-commit backfill loop -


def test_backfill_never_materialises_the_runtime_overlay():
    """HEAD-only isolation (the CALLS_API precedent): the coverage loader and the COVERS edge
    are NEVER wired into ``backfill``'s per-commit replay, so a runtime edge can never be
    re-projected across history and pollute the uniform-history (tree-sitter spine) plane.
    The edge's ``source_commit`` records the single built HEAD commit it was measured against
    (asserted in test_load_coverage_merges_runtime_covers_edge)."""
    import palimpsest.backfill as backfill

    src = Path(backfill.__file__).read_text()
    assert "load_coverage" not in src        # the loader is not called in the replay loop
    assert "COVERS" not in src               # the edge type is not projected per-commit
    assert not hasattr(backfill, "load_coverage")


# --- ac-6: opt-in / host-neutral — absent coverage is a clean no-op, static unaffected ---


def test_coverage_is_opt_in_empty_payload_is_a_noop():
    """host-neutral / opt-in: in any environment that produces no coverage payload (the
    default), the loader is a clean no-op — no COVERS edge, no error — and the overlay recall
    degrades to an empty, disclosed result. COVERS is purely additive, so the static channel
    is entirely unaffected by the overlay's absence."""
    drv = _FakeDriver(_methods())
    res = load_coverage(drv, [])                      # no coverage produced anywhere
    assert res.loaded == 0 and res.rejected == 0 and res.reasons == ()
    assert drv.merged == {}                           # nothing materialised
    overlay = recall_runtime_test_impact(drv, PROD_QN)
    assert overlay["items"] == []                     # nothing to augment with — graceful


# --- package surface + CLI wiring (in_scope: load-coverage + overlay recall) ----------


def test_runtime_overlay_channel_exported_from_package():
    from palimpsest.recall import recall_runtime_test_impact as pkg

    assert pkg is recall_runtime_test_impact


def test_cli_wires_the_coverage_subcommands():
    from palimpsest.cli import build_parser

    parser = build_parser()
    a = parser.parse_args(["load-coverage", "coverage_dir/"])
    assert a.func.__name__ == "_cmd_load_coverage"
    b = parser.parse_args(["runtime-test-impact", "app.OrderService#place", "--limit", "5"])
    assert b.func.__name__ == "_cmd_runtime_test_impact"
    assert b.limit == 5


# --- LIVE Neo4j integration (wi_260714a7m) -------------------------------------------
# The mock-unit tests above prove the LOGIC against a fake driver (the calls_api precedent);
# these prove the actual Cypher EXECUTES on a real Neo4j — resolution by qualified_name, the
# WHERE is_test = true filter, the COVERS MERGE, and the backward-COVERS recall — by running
# the REAL extract -> ingest pipeline against the session testcontainer (conftest `clean_db`).

PROV_LIVE = Provenance(source_commit=HEAD, author="d <d@e.com>", committed_at=COMMITTED)

_LIVE_PROD = (
    "package p;\n"
    "public class OrderService {\n"
    '  public String place() { return "x"; }\n'
    "}\n"
)
# A test file under src/test/ (is_test=true) with a @Test method that covers place().
_LIVE_TEST = (
    "package p;\n"
    "import org.junit.jupiter.api.Test;\n"
    "public class OrderServiceTest {\n"
    "  @Test public void covers_place() { new OrderService().place(); }\n"
    "}\n"
)
LIVE_PROD_QN = "p.OrderService#place()"
LIVE_TEST_QN = "p.OrderServiceTest#covers_place()"


def _ingest_live_methods(clean_db, tmp_path):
    """Run the REAL pipeline: a production class + a src/test/ @Test class (is_test=true)
    extracted and ingested into the live testcontainer graph. Returns the driver."""
    (tmp_path / "src/main/java/p").mkdir(parents=True)
    (tmp_path / "src/main/java/p/OrderService.java").write_text(_LIVE_PROD)
    (tmp_path / "src/test/java/p").mkdir(parents=True)
    (tmp_path / "src/test/java/p/OrderServiceTest.java").write_text(_LIVE_TEST)
    ir = extract(tmp_path, PROV_LIVE, repo_name="cov")
    ingest(clean_db, ir)
    return clean_db


def _covers_count(driver) -> int:
    with driver.session() as s:
        return s.run("MATCH ()-[r:COVERS]->() RETURN count(r) AS c").single()["c"]


def test_live_load_coverage_materialises_covers_edge(clean_db, tmp_path):
    """The real Cypher EXECUTES: resolve both endpoints by qualified_name (with the
    WHERE is_test = true test filter), MERGE ONE COVERS edge stamped edge_kind='runtime'
    + the measured HEAD source_commit, between the correct two Methods."""
    drv = _ingest_live_methods(clean_db, tmp_path)
    res = load_coverage(drv, [CoverageRecord(LIVE_TEST_QN, (LIVE_PROD_QN,), HEAD)])
    assert res.loaded == 1
    assert _covers_count(drv) == 1
    with drv.session() as s:
        row = s.run(
            "MATCH (t:Method)-[r:COVERS]->(m:Method) "
            "RETURN r.edge_kind AS ek, r.source_commit AS sc, "
            "t.qualified_name AS tqn, m.qualified_name AS mqn"
        ).single()
    assert row["ek"] == "runtime"
    assert row["sc"] == HEAD
    assert row["tqn"] == LIVE_TEST_QN
    assert row["mqn"] == LIVE_PROD_QN


def test_live_recall_runtime_overlay_returns_covering_test(clean_db, tmp_path):
    """The backward-COVERS recall EXECUTES on real Neo4j: given the production Method,
    it surfaces the covering test tagged relation=COVERS + edge_kind='runtime'."""
    drv = _ingest_live_methods(clean_db, tmp_path)
    load_coverage(drv, [CoverageRecord(LIVE_TEST_QN, (LIVE_PROD_QN,), HEAD)])
    res = recall_runtime_test_impact(drv, LIVE_PROD_QN)
    ids = [it["id"] for it in res["items"]]
    assert LIVE_TEST_QN in ids
    it = next(it for it in res["items"] if it["id"] == LIVE_TEST_QN)
    assert it["relation"] == COVERS
    assert it["edge_kind"] == EDGE_KIND_RUNTIME


def test_live_load_coverage_is_idempotent(clean_db, tmp_path):
    """Re-running the loader over the unchanged graph keeps exactly ONE COVERS edge
    (real MERGE-on-pattern idempotence, not a fake-driver artifact)."""
    drv = _ingest_live_methods(clean_db, tmp_path)
    rec = CoverageRecord(LIVE_TEST_QN, (LIVE_PROD_QN,), HEAD)
    load_coverage(drv, [rec])
    load_coverage(drv, [rec])
    assert _covers_count(drv) == 1


def test_live_load_coverage_rejects_unresolved_test(clean_db, tmp_path):
    """An unresolved test qualified_name is rejected on real Neo4j — the record counts as
    rejected and NO COVERS edge is materialised (grounded load, no silent no-op)."""
    drv = _ingest_live_methods(clean_db, tmp_path)
    res = load_coverage(drv, [CoverageRecord("p.Ghost#nope()", (LIVE_PROD_QN,), HEAD)])
    assert res.rejected == 1
    assert res.loaded == 0
    assert _covers_count(drv) == 0


# The reflection case that IS the reason ② exists (issue #19 accuracy trigger).
_REFLECT_PROD = (
    "package p;\n"
    "public class ReflectService {\n"
    '  public static String compute() { return "x"; }\n'
    "}\n"
)
# A @Test that reaches compute() ONLY reflectively — emits NO static CALLS edge to it.
_REFLECT_TEST = (
    "package p;\n"
    "import org.junit.jupiter.api.Test;\n"
    "public class ReflectServiceTest {\n"
    "  @Test public void via_reflection() throws Exception {\n"
    '    Class.forName("p.ReflectService").getMethod("compute").invoke(null);\n'
    "  }\n"
    "}\n"
)
REFLECT_PROD_QN = "p.ReflectService#compute()"
REFLECT_TEST_QN = "p.ReflectServiceTest#via_reflection()"


def test_live_runtime_catches_reflection_test_that_static_lower_bound_misses(clean_db, tmp_path):
    """THE reason ② exists, demonstrated end-to-end on live Neo4j: a @Test reaches production
    code ONLY via reflection (Class.forName(...).getMethod(...).invoke), so it emits NO static
    CALLS edge — the static test-impact LOWER BOUND misses it. The runtime producer OBSERVED the
    coverage; loading it as a COVERS(edge_kind='runtime') edge lets the overlay CATCH exactly the
    test static could not see. This is the augment-not-replace value made concrete + measurable."""
    (tmp_path / "src/main/java/p").mkdir(parents=True)
    (tmp_path / "src/main/java/p/ReflectService.java").write_text(_REFLECT_PROD)
    (tmp_path / "src/test/java/p").mkdir(parents=True)
    (tmp_path / "src/test/java/p/ReflectServiceTest.java").write_text(_REFLECT_TEST)
    ir = extract(tmp_path, PROV_LIVE, repo_name="reflect")
    ingest(clean_db, ir)

    # Static lower bound MISSES the reflection-reached test: no CALLS edge targets compute().
    static = recall_test_impact(clean_db, REFLECT_PROD_QN)
    assert REFLECT_TEST_QN not in [it["id"] for it in static["items"]]

    # The runtime producer observed it; load the COVERS(runtime) edge.
    res = load_coverage(clean_db, [CoverageRecord(REFLECT_TEST_QN, (REFLECT_PROD_QN,), HEAD)])
    assert res.loaded == 1

    # The runtime overlay CATCHES exactly the test the static lower bound missed.
    runtime = recall_runtime_test_impact(clean_db, REFLECT_PROD_QN)
    ids = [it["id"] for it in runtime["items"]]
    assert REFLECT_TEST_QN in ids
    it = next(it for it in runtime["items"] if it["id"] == REFLECT_TEST_QN)
    assert it["relation"] == COVERS
    assert it["edge_kind"] == EDGE_KIND_RUNTIME
