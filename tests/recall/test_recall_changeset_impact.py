"""TDD for the changeset-impact recall channel (wi_260714y06) — the changeset-level
generalization of ``recall_test_impact``.

``recall_changeset_impact(driver, *, files=, methods=, commit=, depth=, limit=)`` answers
"given a code CHANGE (a changeset), which tests transitively cover the changed code (and
might break)?". It is QUERY-SIDE only: it resolves the changeset to a SET of production
Method seeds and reuses the SAME backward-CALLS BFS as ``recall_test_impact`` (seeded with
the whole set at once). It materialises NO new edge and calls NO LLM.

Two test layers, deliberately split by dependency (mirrors test_recall_test_impact.py):

* **Mock-unit** (a fake driver isolates the Python orchestration from Neo4j — runs in any
  environment): each of the three input types (files -> CONTAINS*, commit -> MODIFIES ->
  CONTAINS*, methods -> _resolve) resolves to seeds and yields impacted tests; a test reached
  from TWO seeds DEDUPS to exactly one item (shared ``visited``); an empty / unresolved
  changeset is an explicit gap (never a confident empty); the static-lower-bound gap is
  ALWAYS present and the file/commit over-approximation gap rides along only when file/commit
  seeds were used; and items/sources NEVER carry ``author``. Plus the CLI surface (ac-5).

* **Live-Neo4j** (the ``fixtures_test_impact`` corpus ingested onto the shared graph): the
  real resolver Cypher over files / methods / a synthetic commit, dedup across seeds, the
  hot-seed per-hop read bound (COMPUTE, not just output rows), determinism + limit, and
  author-omission under real author-bearing records. These require a Neo4j testcontainer
  (Docker); where Docker is unavailable they ERROR on the fixture (a Docker-fixture error,
  NOT a collection/import failure), same as the sibling channel.
"""

import contextlib
from pathlib import Path

import pytest

from palimpsest import cli
from palimpsest.extract import extract
from palimpsest.ir import Provenance
from palimpsest.kg import ingest
from palimpsest.recall import recall_changeset_impact
from palimpsest.recall.graphrag import (
    _STATIC_LOWER_BOUND_GAP,
    _CHANGESET_OVERAPPROX_GAP,
)

# ── the fixtures_test_impact corpus (shared with test_recall_test_impact.py) ──
# production app.Widget#render(), a DIRECT-calling test, an INDIRECT chain test via
# app.WidgetHelper, and a test reaching the seed THROUGH a PRODUCTION helper
# (app.WidgetProdHelper). ids verified by running the real extractor.
TEST_IMPACT_FIXTURES = Path(__file__).parents[1] / "extract" / "fixtures_test_impact"

WIDGET_RENDER = "app.Widget#render()"                       # production seed
DIRECT_TEST = "app.WidgetDirectTest#rendersDirectly()"      # direct 1-hop test caller
HELPER = "app.WidgetHelper#callWidget()"                     # helper (under src/test)
INDIRECT_TEST = "app.WidgetIndirectTest#rendersIndirectly()"  # indirect via HELPER chain
PROD_HELPER = "app.WidgetProdHelper#callWidgetProd()"       # PRODUCTION intermediary
VIA_PROD_TEST = "app.WidgetViaProdTest#rendersViaProd()"    # test reaching seed THROUGH PROD_HELPER

SOURCE_COMMIT = "c20b7332d8c60ce73794427a4c28120b085c134d"
COMMITTED_AT = "2025-09-03T16:22:54+09:00"
PROV = Provenance(
    source_commit=SOURCE_COMMIT,
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at=COMMITTED_AT,
)

# The _result contract shape (mirrors tests/recall/test_recall_churn.py's RESULT_KEYS).
RESULT_KEYS = {
    "items", "sources", "summaries", "risks", "decisions", "relations",
    "gaps", "confidence", "expand_handle",
}


# ─────────────────────────── mock-unit layer (no Neo4j) ──────────────────────────
# A fake driver that emulates ONLY the Cypher shapes this channel issues, discriminated by
# substring:
#   * _RESOLVE               (method seed lookup)       — "labels(n) AS labels"
#   * _CHANGESET_FILE_METHODS (file  -> methods)        — "f.path IN $paths"
#   * _CHANGESET_COMMIT_METHODS (commit -> methods)     — ":MODIFIES]"
#   * _TEST_CALLERS          (backward CALLS hop)       — "<-[:CALLS]-"
# The resolvers just return the mapped Method ids; the backward hop reproduces the query's
# DISTINCT + ORDER BY id + LIMIT over a plain callee_id -> [caller rows] adjacency (each row
# carrying ``is_test`` so the Python BFS partitions production intermediaries from test items).
# Rows deliberately carry NO 'author' key so author-omission is checkable without a live DB.

# A synthetic changeset: two disjoint production seeds, WIDGET_RENDER (reached via a file /
# an explicit method) and BUTTON_CLICK (reached via a commit / an explicit method), sharing
# ONE downstream test (SHARED_TEST) so multi-seed dedup is observable.
BUTTON_CLICK = "app.Button#click()"                         # a second production seed
BUTTON_TEST = "app.ButtonTest#clicks()"                     # test exclusive to BUTTON_CLICK
SHARED_TEST = "app.SharedTest#exercisesBoth()"              # test reached from BOTH seeds
WIDGET_FILE = "src/main/java/app/Widget.java"               # file containing WIDGET_RENDER
COMMIT_X = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"       # a commit modifying Button's file


def _caller_row(mid, name, is_test=True):
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
        d = self._driver
        if "labels(n) AS labels" in query:  # _RESOLVE (a Method seed)
            row = d.resolve_map.get(params.get("id"))
            return _FakeResult([row] if row else [])
        if "<-[:CALLS]-" in query:  # _TEST_CALLERS (backward hop)
            d.caller_reads.append(len(params.get("ids", [])))
            ids = list(params["ids"])
            lim = params["lim"]
            seen = {}  # DISTINCT by caller id
            for sid in ids:
                for row in d.graph.get(sid, []):
                    seen[row["id"]] = row
            rows = sorted(seen.values(), key=lambda r: r["id"])[:lim]  # ORDER BY id, LIMIT
            return _FakeResult(rows)
        if ":MODIFIES]" in query:  # _CHANGESET_COMMIT_METHODS
            mids = d.commit_map.get(params.get("commit"), [])
            return _FakeResult([{"id": m} for m in mids])
        if "f.path IN $paths" in query:  # _CHANGESET_FILE_METHODS
            out = {}
            for p in params.get("paths", []):
                for m in d.file_map.get(p, []):
                    out[m] = {"id": m}
            return _FakeResult(list(out.values()))
        raise AssertionError(f"unexpected query: {query[:60]!r}")


class _FakeDriver:
    def __init__(self, *, resolve_map=None, file_map=None, commit_map=None, graph=None):
        self.resolve_map = resolve_map or {}
        self.file_map = file_map or {}
        self.commit_map = commit_map or {}
        self.graph = graph or {}
        self.caller_reads = []

    def session(self, *a, **k):
        return _FakeSession(self)


def _full_changeset_driver(graph=None):
    """The standard synthetic changeset driver: WIDGET_RENDER reachable via WIDGET_FILE and
    as an explicit method; BUTTON_CLICK via COMMIT_X and as an explicit method. Backward-CALLS
    graph gives each seed one exclusive test plus the shared one."""
    return _FakeDriver(
        resolve_map={
            WIDGET_RENDER: _resolve_row(WIDGET_RENDER),
            BUTTON_CLICK: _resolve_row(BUTTON_CLICK),
        },
        file_map={WIDGET_FILE: [WIDGET_RENDER]},
        commit_map={COMMIT_X: [BUTTON_CLICK]},
        graph=graph or {
            WIDGET_RENDER: [_caller_row(DIRECT_TEST, "rendersDirectly"),
                            _caller_row(SHARED_TEST, "exercisesBoth")],
            BUTTON_CLICK: [_caller_row(BUTTON_TEST, "clicks"),
                           _caller_row(SHARED_TEST, "exercisesBoth")],
        },
    )


def test_recall_changeset_impact_is_exported_from_recall_package():
    """ac-1: a first-class recall entry point — reachable via the package, not only the module."""
    from palimpsest.recall import recall_changeset_impact as pkg_fn
    from palimpsest.recall.graphrag import recall_changeset_impact as mod_fn

    assert pkg_fn is mod_fn


def test_accepts_files_methods_commit_and_returns_result_dict():
    """ac-1: the entry point accepts files / methods / commit and returns a _result-shaped
    dict (the same separated-channels contract as the sibling channels)."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(
        drv, files=[WIDGET_FILE], methods=[BUTTON_CLICK], commit=COMMIT_X
    )
    assert set(out) == RESULT_KEYS
    assert isinstance(out["items"], list)


def test_files_seed_resolves_to_impacted_tests():
    """ac-2: a changed FILE resolves (CONTAINS*) to its Methods, whose backward test callers
    are returned."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, files=[WIDGET_FILE])
    ids = {it["id"] for it in out["items"]}
    assert DIRECT_TEST in ids
    assert SHARED_TEST in ids


def test_methods_seed_resolves_to_impacted_tests():
    """ac-2: an explicit changed METHOD resolves exactly and its backward test callers surface."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, methods=[BUTTON_CLICK])
    ids = {it["id"] for it in out["items"]}
    assert BUTTON_TEST in ids
    assert SHARED_TEST in ids


def test_commit_seed_resolves_to_impacted_tests():
    """ac-2: a COMMIT resolves (MODIFIES -> File -> CONTAINS*) to the modified files' Methods,
    whose backward test callers are returned."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, commit=COMMIT_X)
    ids = {it["id"] for it in out["items"]}
    assert BUTTON_TEST in ids
    assert SHARED_TEST in ids


def test_test_reached_from_two_seeds_appears_exactly_once():
    """ac-2 (multi-seed dedup): with BOTH seeds in one changeset, the shared downstream test
    is DISTINCT-deduped by the shared ``visited`` set to EXACTLY one item."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, methods=[WIDGET_RENDER, BUTTON_CLICK])
    ids = [it["id"] for it in out["items"]]
    assert ids.count(SHARED_TEST) == 1
    assert set(ids) == {DIRECT_TEST, BUTTON_TEST, SHARED_TEST}


def test_files_and_commit_union_dedups_across_input_types():
    """ac-2: seeds from DIFFERENT input types (a file AND a commit) union into one seed set,
    and the shared test still appears once — dedup is across the whole resolved set, not
    per-input."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, files=[WIDGET_FILE], commit=COMMIT_X)
    ids = [it["id"] for it in out["items"]]
    assert ids.count(SHARED_TEST) == 1
    assert set(ids) == {DIRECT_TEST, BUTTON_TEST, SHARED_TEST}


def test_empty_changeset_is_a_gap_not_a_confident_empty():
    """ac-4: no seed input at all -> empty items + an explicit 'empty changeset' gap; the
    static-lower-bound note still rides along."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv)
    assert out["items"] == []
    assert _STATIC_LOWER_BOUND_GAP in out["gaps"]
    assert any("empty" in g for g in out["gaps"])
    assert drv.caller_reads == []  # nothing traversed


def test_unresolved_changeset_is_a_gap_not_a_confident_empty():
    """ac-4: a changeset whose inputs resolve to NO Method seed -> empty items + an explicit
    'did not resolve' gap (never a confident 'no tests impacted')."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, files=["src/main/java/app/Nope.java"])
    assert out["items"] == []
    assert any("did not resolve" in g for g in out["gaps"])


def test_static_lower_bound_gap_always_present_even_with_results():
    """ac-4 honesty: the static-only LOWER-BOUND note is emitted even when impacted tests
    ARE found — completeness is never claimed on this channel."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, methods=[WIDGET_RENDER])
    assert out["items"]
    assert _STATIC_LOWER_BOUND_GAP in out["gaps"]


def test_overapprox_gap_present_for_file_and_commit_seeds_only():
    """ac-4 honesty: the FILE-granularity over-approximation note rides along WHENEVER file
    or commit seeds were used (those resolve at file granularity), and is ABSENT for an
    explicit-methods-only changeset (exact seeds)."""
    drv = _full_changeset_driver()
    by_file = recall_changeset_impact(drv, files=[WIDGET_FILE])
    assert _CHANGESET_OVERAPPROX_GAP in by_file["gaps"]
    by_commit = recall_changeset_impact(drv, commit=COMMIT_X)
    assert _CHANGESET_OVERAPPROX_GAP in by_commit["gaps"]
    by_method = recall_changeset_impact(drv, methods=[BUTTON_CLICK])
    assert _STATIC_LOWER_BOUND_GAP in by_method["gaps"]
    assert _CHANGESET_OVERAPPROX_GAP not in by_method["gaps"]  # exact seeds, no over-approx


def test_zero_coverage_adds_reingest_advisory():
    """ac-4 honesty: seeds RESOLVE but no test caller is found -> the DISTINCT re-ingest
    advisory is added (a graph predating the is_test marker is not read as 'no test callers'),
    exactly as recall_test_impact."""
    drv = _FakeDriver(resolve_map={WIDGET_RENDER: _resolve_row(WIDGET_RENDER)}, graph={})
    out = recall_changeset_impact(drv, methods=[WIDGET_RENDER])
    assert out["items"] == []
    assert any("re-ingest" in g for g in out["gaps"])


def test_limit_caps_items_and_two_runs_are_deterministic():
    """ac-3: total items are bounded by ``limit`` and two runs over the same graph return the
    SAME items in the SAME order (id-ordered within each hop) — rebuild-deterministic."""
    drv = _full_changeset_driver()
    a = recall_changeset_impact(drv, methods=[WIDGET_RENDER, BUTTON_CLICK], limit=2)
    b = recall_changeset_impact(drv, methods=[WIDGET_RENDER, BUTTON_CLICK], limit=2)
    assert len(a["items"]) == 2
    assert [it["id"] for it in a["items"]] == [it["id"] for it in b["items"]]


def test_items_and_sources_never_expose_author():
    """Author-omission invariant: neither items nor the separate sources channel may carry an
    ``author`` key — the projection goes through the _item/_sources whitelist."""
    drv = _full_changeset_driver()
    out = recall_changeset_impact(drv, methods=[WIDGET_RENDER, BUTTON_CLICK])
    assert out["items"]
    for it in out["items"]:
        assert "author" not in it
        assert "author" not in it["sources"]
    for src in out["sources"]:
        assert "author" not in src


# ─────────────────────────────── CLI surface (ac-5) ──────────────────────────────
# Mirrors tests/recall/test_cli_test_impact.py: the channel itself is covered above, so here
# we prove only the CLI surface — the subcommand exists, parses --file/--method/--commit +
# --depth/--limit, routes to recall_changeset_impact, and renders items + gaps + confidence
# in separated sections. The driver and the recall call are stubbed, so no Neo4j is needed.


def _canned_result():
    return {
        "items": [{
            "id": DIRECT_TEST,
            "kind": "Method",
            "qualified_name": DIRECT_TEST,
            "relation": "CALLS",
            "depth": 1,
            "sources": {
                "source_commit": "c20b7332",
                "path": "src/test/java/app/WidgetDirectTest.java",
                "start_line": 8, "end_line": 11,
                "committed_at": COMMITTED_AT,
            },
        }],
        "sources": [], "summaries": [], "risks": [], "decisions": [], "relations": [],
        "gaps": ["static CALLS is a lower bound: completeness is not claimed"],
        "confidence": 1.0,
        "expand_handle": None,
    }


def _patch_cli(monkeypatch, sink):
    @contextlib.contextmanager
    def _fake_driver():
        yield "DRIVER"

    def _fake_recall(driver, *, files=None, methods=None, commit=None, depth=10, limit=25):
        sink["args"] = (driver, files, methods, commit, depth, limit)
        return _canned_result()

    monkeypatch.setattr(cli, "_driver", _fake_driver)
    monkeypatch.setattr(cli, "recall_changeset_impact", _fake_recall)


def test_cli_changeset_impact_routes_and_renders(monkeypatch, capsys):
    """ac-5: the subcommand parses the seed inputs + --depth/--limit, routes them to the
    channel, and renders the returned items (grounded), gaps, and confidence in own sections."""
    sink = {}
    _patch_cli(monkeypatch, sink)

    rc = cli.main([
        "changeset-impact", "--file", WIDGET_FILE, "--method", BUTTON_CLICK,
        "--commit", COMMIT_X, "--depth", "3", "--limit", "5",
    ])
    assert rc == 0
    # routed to the channel with the parsed seeds + flags (repeatables become lists)
    assert sink["args"] == ("DRIVER", [WIDGET_FILE], [BUTTON_CLICK], COMMIT_X, 3, 5)

    out = capsys.readouterr().out
    assert WIDGET_FILE in out                                # header names the file seed
    assert DIRECT_TEST in out                                # the impacted test-caller item
    assert "src/test/java/app/WidgetDirectTest.java" in out   # grounded source
    assert "GAPS (1)" in out                                  # gaps in own section
    assert "lower bound" in out                               # the honesty gap surfaced
    assert "CONFIDENCE:" in out                               # confidence surfaced


def test_cli_changeset_impact_repeatable_file_and_method(monkeypatch):
    """ac-5: --file and --method are repeatable (append) so a multi-file / multi-method
    changeset is expressible on the CLI."""
    sink = {}
    _patch_cli(monkeypatch, sink)
    rc = cli.main([
        "changeset-impact",
        "--file", WIDGET_FILE, "--file", "src/main/java/app/Button.java",
        "--method", WIDGET_RENDER, "--method", BUTTON_CLICK,
    ])
    assert rc == 0
    _, files, methods, commit, depth, limit = sink["args"]
    assert files == [WIDGET_FILE, "src/main/java/app/Button.java"]
    assert methods == [WIDGET_RENDER, BUTTON_CLICK]
    assert (commit, depth, limit) == (None, 10, 25)  # channel defaults


def test_cli_changeset_impact_requires_a_seed(capsys):
    """ac-5: no seed input at all -> a friendly error and a non-zero exit, BEFORE any driver
    is opened (so it needs no Neo4j)."""
    rc = cli.main(["changeset-impact"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "at least one" in out


# ─────────────────────────── live-Neo4j layer (Docker) ───────────────────────────
# Requires a Neo4j testcontainer; where Docker is unavailable these ERROR on the fixture
# (a Docker-fixture error, distinct from a collection/import failure).


class _CountingDriver:
    """Delegates to a real driver, tallying rows returned per Cypher call (mirrors
    tests/recall/test_recall_test_impact.py's _CountingDriver)."""

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
def changeset_impact_db(recall_db):
    """The fixtures_test_impact corpus ingested ADDITIVELY onto the shared graph (same
    'app.*' namespace as test_recall_test_impact.py — ingest is idempotent by deterministic
    id, so re-ingesting the same corpus is a no-op). Real extract + ingest, so is_test and
    CONTAINS are stamped by the real ingest path."""
    ti_ir = extract(TEST_IMPACT_FIXTURES, PROV, repo_name="TestImpact")
    ingest(recall_db, ti_ir)
    return recall_db


def _file_of(driver, method_id):
    """The repo-relative path of the File that CONTAINS* a Method (queried, not hardcoded)."""
    with driver.session() as s:
        rec = s.run(
            "MATCH (f:File)-[:CONTAINS*]->(m:Method {id:$mid}) RETURN f.path AS path LIMIT 1",
            mid=method_id,
        ).single()
    return rec["path"] if rec else None


def test_live_files_seed_returns_impacted_tests(changeset_impact_db):
    """ac-2 over real Cypher: seeding with the FILE that contains Widget#render() resolves
    (CONTAINS*) to that Method and returns its impacted tests — the direct, the indirect (via
    the test helper), AND the one reached through the production helper."""
    widget_file = _file_of(changeset_impact_db, WIDGET_RENDER)
    assert widget_file  # the fixture File resolved
    out = recall_changeset_impact(changeset_impact_db, files=[widget_file])
    ids = {it["id"] for it in out["items"]}
    assert {DIRECT_TEST, INDIRECT_TEST, VIA_PROD_TEST} <= ids
    assert WIDGET_RENDER not in ids  # the production seed is never echoed as an item
    assert _CHANGESET_OVERAPPROX_GAP in out["gaps"]


def test_live_methods_seed_is_the_union_of_the_seeds(changeset_impact_db):
    """ac-2 over real Cypher: a two-method changeset returns the UNION of what each method
    yields alone (Widget#render()'s tests ∪ WidgetProdHelper#callWidgetProd()'s test)."""
    db = changeset_impact_db
    solo_w = {it["id"] for it in recall_changeset_impact(db, methods=[WIDGET_RENDER])["items"]}
    solo_p = {it["id"] for it in recall_changeset_impact(db, methods=[PROD_HELPER])["items"]}
    combined = {it["id"]
                for it in recall_changeset_impact(db, methods=[WIDGET_RENDER, PROD_HELPER])["items"]}
    assert DIRECT_TEST in solo_w          # a test only WIDGET_RENDER reaches directly
    assert VIA_PROD_TEST in solo_p        # the test PROD_HELPER reaches
    assert combined == solo_w | solo_p    # the union, deduped


def test_live_dedup_across_seeds(changeset_impact_db):
    """ac-2 over real Cypher: VIA_PROD_TEST is reachable from BOTH seeds (from Widget#render()
    through the production helper, and directly from the helper seed) yet appears EXACTLY once."""
    out = recall_changeset_impact(changeset_impact_db, methods=[WIDGET_RENDER, PROD_HELPER])
    ids = [it["id"] for it in out["items"]]
    assert ids.count(VIA_PROD_TEST) == 1


def _synth_episode_modifies(driver, eid, file_path):
    """Layer a synthetic commit Episode + MODIFIES onto an EXISTING fixture File (the churn-
    test pattern), so the commit resolver has an edge to walk."""
    with driver.session() as s:
        s.run(
            "MERGE (e:Episode {id:$id}) "
            "SET e.source_commit=$id, e.author=$a, e.committed_at=$ca",
            id=eid, a="SECRET <secret.person@corp.example>", ca=COMMITTED_AT,
        )
        s.run(
            "MATCH (e:Episode {id:$id}) MATCH (f:File {path:$p}) MERGE (e)-[:MODIFIES]->(f)",
            id=eid, p=file_path,
        )


def _cleanup_episode(driver, eid):
    with driver.session() as s:
        s.run("MATCH (e:Episode {id:$id}) DETACH DELETE e", id=eid)


def test_live_commit_seed_returns_impacted_tests(changeset_impact_db):
    """ac-2 over real Cypher: a synthetic commit that MODIFIES Widget.java resolves (MODIFIES
    -> File -> CONTAINS*) to Widget#render() and returns its impacted tests; the author on the
    synthetic Episode is never surfaced. Torn down afterwards so the shared graph stays clean."""
    db = changeset_impact_db
    widget_file = _file_of(db, WIDGET_RENDER)
    eid = "csimpact-ep-widget"
    _synth_episode_modifies(db, eid, widget_file)
    try:
        out = recall_changeset_impact(db, commit=eid)
        ids = {it["id"] for it in out["items"]}
        assert {DIRECT_TEST, INDIRECT_TEST, VIA_PROD_TEST} <= ids
        assert _CHANGESET_OVERAPPROX_GAP in out["gaps"]
        for it in out["items"]:
            assert "author" not in it and "author" not in it["sources"]
    finally:
        _cleanup_episode(db, eid)


def test_live_ordering_is_deterministic_and_limit_caps(changeset_impact_db):
    """ac-3 over real Cypher: two runs return the SAME items in the SAME order, and ``limit``
    caps the item count."""
    db = changeset_impact_db
    a = recall_changeset_impact(db, methods=[WIDGET_RENDER])
    b = recall_changeset_impact(db, methods=[WIDGET_RENDER])
    assert [it["id"] for it in a["items"]] == [it["id"] for it in b["items"]]
    assert a["items"]
    capped = recall_changeset_impact(db, methods=[WIDGET_RENDER], limit=2)
    assert len(capped["items"]) == 2


HOT = "app.Hot#target()"


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


def test_live_hot_seed_read_is_bounded_server_side(changeset_impact_db):
    """ac-3 (COMPUTE, not just output rows): the changeset entry point inherits the per-hop
    server-side row bound — a hot production seed with many test callers reads at most
    budget + |visited| + 1 rows, never its whole caller set."""
    driver = changeset_impact_db
    n = 8
    _synth_hot(driver, n)
    try:
        counting = _CountingDriver(driver)
        recall_changeset_impact(counting, methods=[HOT], depth=1, limit=3)
        reads = [rows for q, rows in counting.log if "<-[:CALLS]-" in q]
        assert reads  # the backward hop actually queried callers
        # budget(=limit-len(items)=3) + |visited|(=1 seed) + 1 = 5 — full degree never streams.
        assert max(reads) <= 3 + 1 + 1
        assert max(reads) < n
    finally:
        _cleanup_hot(driver)


def test_live_items_and_sources_never_expose_author(changeset_impact_db):
    """Author-omission holds under REAL node records (author is stamped on every Method, so a
    whole-node projection would leak it — the whitelist must not)."""
    out = recall_changeset_impact(changeset_impact_db, methods=[WIDGET_RENDER])
    assert out["items"]
    for it in out["items"]:
        assert "author" not in it
        assert "author" not in it["sources"]
    for src in out["sources"]:
        assert "author" not in src
