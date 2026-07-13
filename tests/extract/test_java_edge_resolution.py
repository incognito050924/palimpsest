"""wi_260713bz4 — Java extractor per-edge resolution precision marker (ac-2, ac-3
+ constraints A, B).

WHY THESE TESTS EXIST
  ac-2 (typed vs name marking): a CALLS edge resolved by the receiver's STATIC TYPE
    (local/field/new/static seed -> known class -> hierarchy candidates) must carry
    ``resolution="typed"``; a CALLS edge produced by the NAME fallback (an untyped
    ``other``/chain receiver whose method matches by bare name) must carry
    ``resolution="name"``. DEPENDS_ON has no typed path today, so it is always "name".
  CONSTRAINT A (order-independent monotone join): the SAME src->dst reachable from
    BOTH a typed call site AND a name-fallback call site must resolve "typed" in EITHER
    source order (typed ∨ name = typed; a stored "name" is upgraded, "typed" is never
    downgraded). The marker must not depend on call-site source order.
  CONSTRAINT B (CALLS cross-package simple-name collision): when a receiver's simple
    type name resolves to >1 corpus class in DIFFERENT packages and the file's captured
    imports do NOT narrow it to one, the resulting CALLS edges are name guesses, marked
    "name" (NOT "typed"). A narrowing import keeps "typed".
  ac-3 (DEPENDS_ON import-qualified): a referenced simple type name resolving to a
    same-simple-name class in a DIFFERENT package is disambiguated to the imported FQN
    (the false edges to other-package same-name classes are dropped). Without a
    narrowing import, the name-based edges are KEPT (no false negatives), marked "name".

Scope-local mock-unit: drives the real ``extract()`` over small hermetic inline corpora
(tmp trees); no DB, no network, no sibling extractors.
"""

from pathlib import Path

from palimpsest.ir import Provenance, CALLS, DEPENDS_ON
from palimpsest.extract import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)


def _extract_ir(tmp_path, files):
    """Extract an IR from an inline ``{relpath: java-source}`` corpus."""
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="T")


def _resolution(ir, kind, src, dst):
    """The ``resolution`` of the (unique) ``kind`` edge ``src``->``dst``, or a marker."""
    matches = [e for e in ir.edges_of(kind) if e.src == src and e.dst == dst]
    assert len(matches) == 1, f"expected exactly one {kind} {src}->{dst}, got {len(matches)}"
    return matches[0].resolution


# --- ac-2: type-resolved CALLS -> typed; chain/other receiver -> name --------

def test_ac2_type_resolved_call_is_typed_and_chain_receiver_is_name(tmp_path):
    ir = _extract_ir(
        tmp_path,
        {
            "src/main/java/app/Helper.java": "package app;\nclass Helper { void doIt() {} }\n",
            "src/main/java/app/Thing.java": "package app;\nclass Thing { void go() {} }\n",
            "src/main/java/app/Service.java": (
                "package app;\n"
                "class Service {\n"
                "    Thing getThing() { return null; }\n"
                "    void run() {\n"
                "        Helper h = new Helper();\n"
                "        h.doIt();\n"          # typed: local Helper -> Helper#doIt
                "        getThing().go();\n"   # other/chain receiver -> name fallback -> Thing#go
                "    }\n"
                "}\n"
            ),
        },
    )
    assert _resolution(ir, CALLS, "app.Service#run()", "app.Helper#doIt()") == "typed"
    assert _resolution(ir, CALLS, "app.Service#run()", "app.Thing#go()") == "name"


# --- CONSTRAINT A: order-independent typed∨name = typed ----------------------

_ORDER_TYPED_FIRST = (
    "package app;\n"
    "class Foo { void m() {} }\n"
    "class Bar {\n"
    "    Foo make() { return new Foo(); }\n"
    "    void a() {\n"
    "        Foo f = new Foo();\n"
    "        f.m();\n"        # TYPED call site (local Foo)
    "        make().m();\n"   # NAME-fallback call site (chain receiver)
    "    }\n"
    "}\n"
)
_ORDER_NAME_FIRST = (
    "package app;\n"
    "class Foo { void m() {} }\n"
    "class Bar {\n"
    "    Foo make() { return new Foo(); }\n"
    "    void a() {\n"
    "        make().m();\n"   # NAME-fallback call site first
    "        Foo f = new Foo();\n"
    "        f.m();\n"        # TYPED call site second
    "    }\n"
    "}\n"
)


def test_constraintA_typed_upgrades_name_regardless_of_source_order(tmp_path):
    # Same src->dst (Bar#a -> Foo#m) reachable from a typed AND a name call site.
    for source in (_ORDER_TYPED_FIRST, _ORDER_NAME_FIRST):
        p = tmp_path / "src/main/java/app/Bar.java"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source)
        ir = extract(tmp_path, PROV, repo_name="T")
        assert _resolution(ir, CALLS, "app.Bar#a()", "app.Foo#m()") == "typed", (
            "typed must win over name irrespective of call-site order"
        )


# --- CONSTRAINT B: cross-package simple-name collision -> name (no narrowing) -

_COLLISION_CORPUS = {
    "src/main/java/a/Foo.java": "package a;\npublic class Foo { public void m() {} }\n",
    "src/main/java/b/Foo.java": "package b;\npublic class Foo { public void m() {} }\n",
}


def test_constraintB_unnarrowed_collision_calls_marked_name(tmp_path):
    files = dict(_COLLISION_CORPUS)
    files["src/main/java/c/Caller.java"] = (
        "package c;\n"
        "class Caller {\n"
        "    Foo f;\n"          # Foo is ambiguous (a.Foo | b.Foo), no import narrows it
        "    void go() { f.m(); }\n"
        "}\n"
    )
    ir = _extract_ir(tmp_path, files)
    # Both collision-candidate CALLS edges are name guesses, NOT typed.
    assert _resolution(ir, CALLS, "c.Caller#go()", "a.Foo#m()") == "name"
    assert _resolution(ir, CALLS, "c.Caller#go()", "b.Foo#m()") == "name"


def test_constraintB_narrowing_import_keeps_typed(tmp_path):
    files = dict(_COLLISION_CORPUS)
    files["src/main/java/c/Caller2.java"] = (
        "package c;\n"
        "import a.Foo;\n"       # narrows Foo -> a.Foo
        "class Caller2 {\n"
        "    Foo f;\n"
        "    void go() { f.m(); }\n"
        "}\n"
    )
    ir = _extract_ir(tmp_path, files)
    assert _resolution(ir, CALLS, "c.Caller2#go()", "a.Foo#m()") == "typed"
    # ac-2 truthfulness (wi_260713bz4): the import excludes b.Foo, so the CALLS seed
    # must be narrowed just like _depends_on_edges — the false b.Foo#m() edge, which the
    # old code emitted AND mislabeled "typed", must be DROPPED entirely.
    assert not ir.has_edge(CALLS, "c.Caller2#go()", "b.Foo#m()")


def test_ac2_narrowing_import_drops_false_collision_calls_edge(tmp_path):
    # WHY: a receiver simple-name (Foo) that collides across packages (a.Foo | b.Foo,
    # both declaring m()) but is import-narrowed to exactly one FQN (import a.Foo) must
    # resolve the CALLS edge to ONLY the imported class, marked "typed". The old
    # _calls_edges seeded from class_by_simple (ALL same-name classes) and never narrowed
    # the seed for CALLS — so it emitted an edge to b.Foo#m() AND marked it "typed", a
    # false, precisely-labeled edge to the import-excluded class. Edge cases pinned: the
    # surviving imported edge stays typed; the excluded-package edge is gone (not merely
    # demoted to name).
    files = dict(_COLLISION_CORPUS)  # a.Foo#m() and b.Foo#m() both exist
    files["src/main/java/c/Caller.java"] = (
        "package c;\n"
        "import a.Foo;\n"       # excludes b.Foo
        "class Caller {\n"
        "    void go() {\n"
        "        Foo f = new Foo();\n"  # local typed Foo -> narrowed to a.Foo
        "        f.m();\n"
        "    }\n"
        "}\n"
    )
    ir = _extract_ir(tmp_path, files)
    assert _resolution(ir, CALLS, "c.Caller#go()", "a.Foo#m()") == "typed"
    assert not ir.has_edge(CALLS, "c.Caller#go()", "b.Foo#m()")


# --- ac-3: DEPENDS_ON import-qualified disambiguation ------------------------

_DEPENDS_COLLISION = {
    "src/main/java/a/Bar.java": "package a;\npublic class Bar {}\n",
    "src/main/java/b/Bar.java": "package b;\npublic class Bar {}\n",
}


def test_ac3_depends_on_import_narrows_to_single_edge(tmp_path):
    files = dict(_DEPENDS_COLLISION)
    files["src/main/java/c/User.java"] = (
        "package c;\n"
        "import a.Bar;\n"       # narrows Bar -> a.Bar
        "class User {\n"
        "    Bar b;\n"
        "}\n"
    )
    ir = _extract_ir(tmp_path, files)
    # Only the imported FQN survives; the other-package false edge is dropped.
    assert ir.has_edge(DEPENDS_ON, "c.User", "a.Bar")
    assert not ir.has_edge(DEPENDS_ON, "c.User", "b.Bar")
    assert _resolution(ir, DEPENDS_ON, "c.User", "a.Bar") == "name"


def test_ac3_depends_on_without_import_keeps_both_edges_as_name(tmp_path):
    files = dict(_DEPENDS_COLLISION)
    files["src/main/java/c/User2.java"] = (
        "package c;\n"
        "class User2 {\n"       # no import: cannot disambiguate
        "    Bar b;\n"
        "}\n"
    )
    ir = _extract_ir(tmp_path, files)
    # No false negatives: both same-simple-name edges kept, marked name.
    assert _resolution(ir, DEPENDS_ON, "c.User2", "a.Bar") == "name"
    assert _resolution(ir, DEPENDS_ON, "c.User2", "b.Bar") == "name"
