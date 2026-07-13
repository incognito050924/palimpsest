"""TDD for the test-impact recall channel (#7 — impl-recall, closes ac-2 + ac-3).

``recall_test_impact(driver, method_id)`` answers "which tests exercise this changed
production Method?" by walking ``CALLS`` BACKWARD (seed <- caller) to the test Methods
(``is_test=true``) that transitively call the seed — direct (1 hop) and indirect (a
helper chain, >1 hop).

Two test layers, deliberately split by dependency:

* **Mock-unit** (a fake driver isolates the Python BFS orchestration from Neo4j — runs
  in any environment): the multi-hop backward BFS returns the direct AND indirect test
  callers, multi-path callers dedup, ``depth<=0`` degrades to a seed-only (empty)
  result, the standing static-lower-bound gap is ALWAYS present, a zero-coverage result
  adds the DISTINCT re-ingest advisory, and items/sources NEVER carry ``author``. These
  encode the ac-2 traversal ORCHESTRATION and the ac-3 honesty/bounding contracts.

* **Live-Neo4j** (the ``fixtures_test_impact`` corpus ingested onto the shared graph):
  the real Cypher — backward direction + the ``is_test`` WHERE filter, the per-hop
  server-side row bound on a hot seed (COMPUTE bounded, mirrors ``test_traversal_bound``),
  deterministic ordering, and author-omission under real node records. These require a
  Neo4j testcontainer (Docker); where Docker is unavailable they ERROR on the fixture
  (a Docker-fixture error, NOT a collection/import failure).

Design invariant pinned by ac-3: the traversal is a PER-HOP bounded BFS (mirrors
``recall``'s ``_hop``), NOT a variable-length ``[:CALLS*1..depth]`` path — a var-length
path bounds only OUTPUT rows, not COMPUTE, and is not rebuild-deterministic.
"""

from pathlib import Path

import pytest

from palimpsest.extract import extract
from palimpsest.ir import Provenance
from palimpsest.kg import ingest
from palimpsest.recall.graphrag import (
    recall_test_impact,
    _STATIC_LOWER_BOUND_GAP,
    _TEST_IMPACT_FANOUT_CAP,
)

# ── the fixtures_test_impact corpus (impl-marker) — a SEPARATE tree from fixtures/ ──
# production app.Widget#render(), a DIRECT-calling test, an INDIRECT chain test via
# app.WidgetHelper (itself under src/test), and — the dominant real case — a test that
# reaches the seed THROUGH a PRODUCTION helper (app.WidgetProdHelper, under src/main, so
# is_test is falsy). ids verified by running the real extractor.
TEST_IMPACT_FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures_test_impact"

WIDGET_RENDER = "app.Widget#render()"                       # production seed (is_test None)
DIRECT_TEST = "app.WidgetDirectTest#rendersDirectly()"      # direct 1-hop test caller
HELPER = "app.WidgetHelper#callWidget()"                     # helper (under src/test)
INDIRECT_TEST = "app.WidgetIndirectTest#rendersIndirectly()"  # indirect via HELPER chain
PROD_HELPER = "app.WidgetProdHelper#callWidget()"           # PRODUCTION intermediary (is_test None)
VIA_PROD_TEST = "app.WidgetViaProdTest#rendersViaProd()"    # test reaching seed THROUGH PROD_HELPER

SOURCE_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"
COMMITTED_AT = "2025-09-03T16:22:54+09:00"
PROV = Provenance(
    source_commit=SOURCE_COMMIT,
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at=COMMITTED_AT,
)


# ─────────────────────────── mock-unit layer (no Neo4j) ──────────────────────────
# A fake driver that emulates ONLY the two Cypher shapes recall_test_impact issues:
#   * _RESOLVE  (seed lookup)              — discriminated by "labels(n) AS labels"
#   * _TEST_CALLERS (backward CALLS hop)   — discriminated by "<-[:CALLS]-"
# The fake faithfully reproduces the query's DISTINCT + ORDER BY id + LIMIT semantics
# over a plain callee_id -> [caller rows] adjacency — returning ALL callers (NO is_test
# filter, mirroring the fixed backward-CALLS query), each row carrying its ``is_test``
# flag so the Python BFS does the is_test partition (production intermediaries advance the
# frontier; only is_test callers become items). This exercises the BFS (hop counting,
# visited dedup, per-hop budget, gap construction, _item/_sources projection) in isolation.
# Rows deliberately carry NO 'author' key so author-omission is checkable without a live DB.


def _caller_row(mid, name, is_test=True):
    # A production caller (is_test falsy) lives under src/main; a test caller under src/test.
    sub = "test" if is_test else "main"
    return {
        "id": mid,
        "is_test": is_test,   # partitions frontier-only production callers from item test callers
        "labels": ["Method"],
        "name": name,
        "qualified_name": mid,
        "path": f"src/{sub}/java/app/{name}.java",
        "start_line": 1,
        "end_line": 3,
        "source_commit": SOURCE_COMMIT,
        "committed_at": COMMITTED_AT,
        # NO 'author' — the projection whitelist (_item/_sources) must omit PII.
    }


def _resolve_row(mid):
    return {
        "n": {"id": mid, "qualified_name": mid, "name": mid.rsplit("#", 1)[0]},
        "labels": ["Method"],
    }


class _FakeRecord:
    def __init__(self, d):
        self._d = d

    def __getitem__(self, k):
        return self._d[k]

    def data(self):
        return dict(self._d)


class _FakeResult:
    def __init__(self, records):
        self._records = [_FakeRecord(r) for r in records]

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _FakeSession:
    def __init__(self, driver):
        self._driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        if "labels(n) AS labels" in query:  # _RESOLVE
            row = self._driver.resolve_row
            return _FakeResult([row] if row else [])
        if "<-[:CALLS]-" in query:  # _TEST_CALLERS (backward hop)
            self._driver.caller_reads.append(len(params.get("ids", [])))
            ids = list(params["ids"])
            lim = params["lim"]
            seen = {}  # DISTINCT by caller id
            for sid in ids:
                for row in self._driver.graph.get(sid, []):
                    seen[row["id"]] = row
            rows = sorted(seen.values(), key=lambda r: r["id"])[:lim]  # ORDER BY id, LIMIT
            return _FakeResult(rows)
        raise AssertionError(f"unexpected query: {query[:60]!r}")


class _FakeDriver:
    def __init__(self, resolve_row, graph=None):
        self.resolve_row = resolve_row
        self.graph = graph or {}
        self.caller_reads = []

    def session(self, *a, **k):
        return _FakeSession(self)


def test_recall_test_impact_is_exported_from_recall_package():
    """A first-class recall entry point — reachable via the package, not only the module."""
    from palimpsest.recall import recall_test_impact as pkg_fn
    from palimpsest.recall.graphrag import recall_test_impact as mod_fn

    assert pkg_fn is mod_fn


def test_bfs_returns_direct_and_indirect_test_callers():
    """ac-2: from the production seed, BOTH the direct 1-hop test caller AND the
    indirect test caller reached through the helper chain are returned, each with the
    CALLS relation and its BFS depth. The seed itself is never echoed as an item."""
    graph = {
        WIDGET_RENDER: [_caller_row(DIRECT_TEST, "rendersDirectly"),
                        _caller_row(HELPER, "callWidget")],
        HELPER: [_caller_row(INDIRECT_TEST, "rendersIndirectly")],
    }
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph)

    out = recall_test_impact(drv, WIDGET_RENDER)

    ids = [it["id"] for it in out["items"]]
    assert DIRECT_TEST in ids        # direct 1-hop test caller
    assert INDIRECT_TEST in ids      # indirect via the WidgetHelper chain
    assert WIDGET_RENDER not in ids  # the production seed is not a test item
    by_id = {it["id"]: it for it in out["items"]}
    assert by_id[DIRECT_TEST]["relation"] == "CALLS"
    assert by_id[DIRECT_TEST]["depth"] == 1      # direct caller reached at hop 1
    assert by_id[INDIRECT_TEST]["depth"] == 2    # indirect caller reached at hop 2


def test_bfs_traverses_through_production_intermediary():
    """ac-2 core (the dominant real case): a test that reaches the seed THROUGH a
    PRODUCTION helper (is_test falsy) MUST be found. The frontier expands THROUGH every
    backward caller — the production intermediary is a stepping-stone kept in the frontier —
    while ONLY is_test callers are collected as items.

    Guards the defect where the per-hop query pruned the frontier to is_test=true: the
    production helper was dropped at hop 1, so the test reaching the seed through it was
    NEVER found (a false 'no tests impacted'). Two clauses discriminate the fix:
      * VIA_PROD_TEST IN items      — the distant test, reached through production code;
      * PROD_HELPER NOT IN items    — the production intermediary is a stepping-stone,
                                       never itself an answer.
    """
    graph = {
        WIDGET_RENDER: [_caller_row(PROD_HELPER, "callWidget", is_test=False)],  # production intermediary
        PROD_HELPER: [_caller_row(VIA_PROD_TEST, "rendersViaProd", is_test=True)],  # test via prod helper
    }
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph)

    out = recall_test_impact(drv, WIDGET_RENDER)

    ids = [it["id"] for it in out["items"]]
    assert VIA_PROD_TEST in ids       # the distant test, reached THROUGH the production helper
    assert PROD_HELPER not in ids     # production intermediary is a stepping-stone, not an item
    assert WIDGET_RENDER not in ids   # the production seed is never echoed as an item
    by_id = {it["id"]: it for it in out["items"]}
    assert by_id[VIA_PROD_TEST]["depth"] == 2  # reached at hop 2, through the production helper
    assert by_id[VIA_PROD_TEST]["relation"] == "CALLS"


def test_multi_path_caller_is_deduped_across_hops():
    """A caller reachable via more than one path appears EXACTLY once (visited-dedup
    across hops), so a diamond in the backward-call graph never double-counts."""
    A = "app.ATest#a()"
    C = "app.CTest#c()"
    graph = {
        WIDGET_RENDER: [_caller_row(A, "a"), _caller_row(C, "c")],  # hop1: A, C
        A: [_caller_row(C, "c")],  # hop2 from A re-reaches C (already visited)
    }
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph)

    out = recall_test_impact(drv, WIDGET_RENDER)
    ids = [it["id"] for it in out["items"]]
    assert ids.count(C) == 1
    assert sorted(ids) == [A, C]


def test_depth_zero_degrades_to_seed_only_empty_result():
    """ac-3: ``depth<=0`` needs no special-case — the ``while cur_depth < depth`` loop
    simply never runs, so the seed-only (empty) result degrades gracefully. Because it
    is empty, the zero-coverage re-ingest advisory is also present."""
    graph = {WIDGET_RENDER: [_caller_row(DIRECT_TEST, "rendersDirectly")]}
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph)

    out = recall_test_impact(drv, WIDGET_RENDER, depth=0)
    assert out["items"] == []
    assert drv.caller_reads == []  # no hop was ever issued
    assert _STATIC_LOWER_BOUND_GAP in out["gaps"]
    assert any("re-ingest" in g for g in out["gaps"])


def test_unresolved_seed_is_a_gap_not_a_confident_empty():
    """An unresolved seed is an explicit gap (never a confident empty answer); the
    static-lower-bound note still rides along."""
    drv = _FakeDriver(resolve_row=None)
    out = recall_test_impact(drv, "app.Nope#missing()")
    assert out["items"] == []
    assert _STATIC_LOWER_BOUND_GAP in out["gaps"]
    assert any("did not resolve" in g for g in out["gaps"])


def test_static_lower_bound_gap_is_always_present_even_with_results():
    """ac-3 honesty: the static-only LOWER-BOUND note is emitted even when test callers
    ARE found — completeness is never claimed on this channel."""
    graph = {WIDGET_RENDER: [_caller_row(DIRECT_TEST, "rendersDirectly")]}
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph)

    out = recall_test_impact(drv, WIDGET_RENDER)
    assert out["items"]  # results were found
    assert _STATIC_LOWER_BOUND_GAP in out["gaps"]  # yet the gap is still present


def test_zero_coverage_adds_distinct_reingest_advisory():
    """ac-3 honesty: when NO test caller is found, a DISTINCT re-ingest advisory is
    added so a graph predating the is_test marker is not read as 'no test callers'."""
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph={})  # seed resolves, no callers
    out = recall_test_impact(drv, WIDGET_RENDER)
    assert out["items"] == []
    reingest = [g for g in out["gaps"] if "re-ingest" in g]
    assert len(reingest) == 1  # exactly one, DISTINCT from the static-lower-bound note
    assert reingest[0] != _STATIC_LOWER_BOUND_GAP
    assert "is_test" in reingest[0]


def test_items_and_sources_never_expose_author():
    """Author-omission invariant: neither items nor the separate sources channel may
    carry an ``author`` key — the projection goes through the _item/_sources whitelist."""
    graph = {
        WIDGET_RENDER: [_caller_row(DIRECT_TEST, "rendersDirectly"),
                        _caller_row(HELPER, "callWidget")],
        HELPER: [_caller_row(INDIRECT_TEST, "rendersIndirectly")],
    }
    drv = _FakeDriver(_resolve_row(WIDGET_RENDER), graph)

    out = recall_test_impact(drv, WIDGET_RENDER)
    assert out["items"]
    for it in out["items"]:
        assert "author" not in it
        assert "author" not in it["sources"]
    for src in out["sources"]:
        assert "author" not in src


def test_fanout_cap_is_a_named_positive_constant():
    """The per-hop fan-out cap that stops a high-degree seed from exploding is a named
    module constant (mirrors _COCHANGE_FANOUT_CAP), never a magic literal in the query."""
    assert isinstance(_TEST_IMPACT_FANOUT_CAP, int)
    assert _TEST_IMPACT_FANOUT_CAP > 0


# ─────────────────────────── live-Neo4j layer (Docker) ───────────────────────────
# Requires a Neo4j testcontainer; where Docker is unavailable these ERROR on the
# fixture (a Docker-fixture error, distinct from a collection/import failure).


class _CountingDriver:
    """Delegates to a real driver, tallying rows returned per Cypher call (mirrors
    tests/recall/test_traversal_bound.py's _CountingDriver)."""

    def __init__(self, inner):
        self._inner = inner
        self.log = []

    def session(self, *a, **k):
        return _CountingSession(self._inner.session(*a, **k), self.log)


class _CountingResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


class _CountingSession:
    def __init__(self, inner, log):
        self._inner = inner
        self._log = log

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def run(self, query, **params):
        records = list(self._inner.run(query, **params))
        self._log.append((query, len(records)))
        return _CountingResult(records)


@pytest.fixture(scope="module")
def test_impact_db(recall_db):
    """The fixtures_test_impact corpus ingested ADDITIVELY onto the shared graph, under
    the 'app.*' namespace that never collides with commute ('kr.co.*') or reconcile
    ('recon.*'). Mirrors the design_risk_db(recall_db) pattern — real extract + ingest,
    so is_test is stamped by the real ingest path (impl-marker)."""
    ti_ir = extract(TEST_IMPACT_FIXTURES, PROV, repo_name="TestImpact")
    ingest(recall_db, ti_ir)
    return recall_db


def test_live_direct_and_indirect_test_callers_returned(test_impact_db):
    """ac-2 over real Cypher: the backward CALLS traversal returns the direct 1-hop test
    caller AND the indirect test caller via the helper chain; the seed is excluded."""
    out = recall_test_impact(test_impact_db, WIDGET_RENDER)
    ids = [it["id"] for it in out["items"]]
    assert DIRECT_TEST in ids
    assert INDIRECT_TEST in ids
    assert WIDGET_RENDER not in ids
    by_id = {it["id"]: it for it in out["items"]}
    assert by_id[DIRECT_TEST]["depth"] == 1
    assert by_id[INDIRECT_TEST]["depth"] == 2
    assert by_id[DIRECT_TEST]["relation"] == "CALLS"


def test_live_only_is_test_callers_surface(test_impact_db):
    """The is_test partition holds over real records: every returned item is a test Method,
    and no PRODUCTION node leaks in — even a production intermediary (WidgetProdHelper) that
    the traversal walks THROUGH must stay out of ``items``. Widget#render() and
    WidgetProdHelper#callWidget() are the two production Methods; neither may surface."""
    out = recall_test_impact(test_impact_db, WIDGET_RENDER)
    assert out["items"]
    ids = {it["id"] for it in out["items"]}
    assert WIDGET_RENDER not in ids   # the production seed
    assert PROD_HELPER not in ids     # the production intermediary — traversed through, not an item
    assert ids == {DIRECT_TEST, HELPER, INDIRECT_TEST, VIA_PROD_TEST}


def test_live_test_reaches_seed_through_production_intermediary(test_impact_db):
    """ac-2 core over real Cypher: a test that reaches the seed THROUGH a PRODUCTION helper
    (WidgetProdHelper, under src/main → is_test falsy) IS found. The backward frontier must
    expand THROUGH the production intermediary (hop 1) to collect the distant test at hop 2 —
    the exact case a per-hop is_test filter on the frontier would drop as a false 'no tests'."""
    out = recall_test_impact(test_impact_db, WIDGET_RENDER)
    by_id = {it["id"]: it for it in out["items"]}
    assert VIA_PROD_TEST in by_id            # distant test, reached through production code
    assert PROD_HELPER not in by_id          # production intermediary is a stepping-stone
    assert by_id[VIA_PROD_TEST]["depth"] == 2  # hop 2, through the production helper
    assert by_id[VIA_PROD_TEST]["relation"] == "CALLS"


def _synth_hot(driver, n):
    """A hot production seed called by ``n`` distinct is_test Methods (all under app.Hot*)."""
    with driver.session() as s:
        s.run(
            "CREATE (h:Method {id: $hid, qualified_name: $hid, name: 'target', "
            "path: 'src/main/java/app/Hot.java', start_line: 1, end_line: 3, "
            "source_commit: $c, committed_at: $t})",
            hid=HOT, c=SOURCE_COMMIT, t=COMMITTED_AT,
        )
        for i in range(n):
            s.run(
                "MATCH (h:Method {id: $hid}) "
                "CREATE (t:Method {id: $tid, qualified_name: $tid, name: $nm, "
                "is_test: true, path: $p, start_line: 1, end_line: 3, "
                "source_commit: $c, committed_at: $ct})-[:CALLS]->(h)",
                hid=HOT, tid=f"app.HotTest{i}#t()", nm=f"t{i}",
                p=f"src/test/java/app/HotTest{i}.java", c=SOURCE_COMMIT, ct=COMMITTED_AT,
            )


def _cleanup_hot(driver):
    with driver.session() as s:
        s.run("MATCH (n:Method) WHERE n.id STARTS WITH 'app.Hot' DETACH DELETE n")


HOT = "app.Hot#target()"


def test_live_hot_seed_read_is_bounded_server_side(test_impact_db):
    """ac-3 (COMPUTE, not just output rows): a hot seed with many test callers does not
    stream its whole caller set — the hop reads at most budget + |visited| + 1 rows."""
    driver = test_impact_db
    n = 8
    _synth_hot(driver, n)
    try:
        counting = _CountingDriver(driver)
        recall_test_impact(counting, HOT, depth=1, limit=3)
        reads = [rows for q, rows in counting.log if "<-[:CALLS]-" in q]
        assert reads  # the backward hop actually queried callers
        # budget(=limit-len(items)=3) + |visited|(=1 seed) + 1 = 5 — full degree never streams.
        assert max(reads) <= 3 + 1 + 1
        assert max(reads) < n
    finally:
        _cleanup_hot(driver)


def test_live_ordering_is_deterministic(test_impact_db):
    """ac-3: two runs over the same graph return the SAME items in the SAME order
    (id-ordered within each hop) — rebuild-deterministic."""
    a = recall_test_impact(test_impact_db, WIDGET_RENDER)
    b = recall_test_impact(test_impact_db, WIDGET_RENDER)
    assert [it["id"] for it in a["items"]] == [it["id"] for it in b["items"]]
    assert a["items"]


def test_live_items_and_sources_never_expose_author(test_impact_db):
    """Author-omission holds under REAL node records (author is stamped on every node,
    incl. Method, so a whole-node projection would leak it — the whitelist must not)."""
    out = recall_test_impact(test_impact_db, WIDGET_RENDER)
    assert out["items"]
    for it in out["items"]:
        assert "author" not in it
        assert "author" not in it["sources"]
    for src in out["sources"]:
        assert "author" not in src
