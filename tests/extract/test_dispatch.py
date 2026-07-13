"""TDD for the multi-language extractor dispatch (issue #13).

``EXTRACTORS_BY_EXT`` registered per-language extractors but no production path
routed to them — cli/backfill/reconcile all called ``extract`` (= ``extract_java``
hardcoded), so Kotlin/Python/Rust/ECMAScript extractors were end-to-end dead code.

``dispatch`` wires them: a possibly-MIXED-language tree is routed by extension to
each language GROUP's extractor and the fragments are merged into ONE IR (one Repo
node). The ECMAScript family (.ts/.tsx/.js/.jsx/.svelte) is ONE group so its
union-wide IMPORTS resolution + family CALLS boundary (SC-B, ADR-20260713) survive;
a single-language tree is byte-identical to calling that language's extractor
directly (Java regression invariant).
"""

from palimpsest.ir import (
    Provenance,
    REPO,
    CLASS,
    FUNCTION,
    CALLS,
    IMPORTS,
    MEMBER_OF,
    COMMUNITY,
)
from palimpsest.extract import dispatch, extract_java
from palimpsest.kg import augment_communities

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

JAVA_SRC = """package app;

public class Service {
    void handle() {
        greet();
    }
    void greet() {
    }
}
"""

KOTLIN_SRC = """package app

fun main() {
    greet()
}

fun greet() {
}
"""

PY_SRC = """def main():
    greet()


def greet():
    pass
"""

RUST_SRC = """fn main() {
    greet();
}

fn greet() {
}
"""

# a.ts imports ./b (-> b.js) and calls shared(); b.js has a same-named shared().
A_TS = """import './b';

export function shared() {
}

export function caller() {
    shared();
}
"""

B_JS = """export function shared() {
}
"""

MIXED = {
    "Service.java": JAVA_SRC,
    "app.kt": KOTLIN_SRC,
    "mod.py": PY_SRC,
    "lib.rs": RUST_SRC,
    "a.ts": A_TS,
    "b.js": B_JS,
}


def _write(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


def _ext_group(path):
    """The language-group key of a source path's extension (ECMAScript family
    shares one key so a resolved .ts<->.js link is same-group, not cross-group)."""
    ext = "." + path.rsplit(".", 1)[-1]
    if ext in (".ts", ".tsx", ".js", ".jsx", ".svelte"):
        return "ecmascript"
    return ext


def test_dispatch_routes_each_language(tmp_path):
    """ac-2: each extension lands on the right extractor; every language's nodes
    coexist in ONE merged IR."""
    _write(tmp_path, MIXED)
    ir = dispatch(tmp_path, PROV, repo_name="M")

    def has(kind, name, ext):
        return any(
            n.kind == kind and n.name == name and (n.path or "").endswith(ext)
            for n in ir.nodes
        )

    assert has(CLASS, "Service", ".java"), "Java CLASS not routed"
    assert has(FUNCTION, "greet", ".kt"), "Kotlin FUNCTION not routed"
    assert has(FUNCTION, "greet", ".py"), "Python FUNCTION not routed"
    assert has(FUNCTION, "greet", ".rs"), "Rust FUNCTION not routed"
    assert has(FUNCTION, "shared", ".ts"), "TS FUNCTION not routed"


def test_dispatch_ecmascript_unified_and_scb(tmp_path):
    """ac-3: the ECMAScript family stays a unified pass (relative IMPORTS resolved
    across .ts<->.js) and no CALLS edge crosses a language-GROUP boundary."""
    _write(tmp_path, MIXED)
    ir = dispatch(tmp_path, PROV, repo_name="M")

    # union-wide IMPORTS: a.ts import './b' resolves to the b.js File node.
    assert ir.has_edge(IMPORTS, "a.ts", "b.js")
    # family SC-B: the .ts caller links to the .ts shared, never to b.js.shared.
    assert ir.has_edge(CALLS, "a.ts.caller()", "a.ts.shared()")
    assert not any(e.dst == "b.js.shared()" for e in ir.edges_of(CALLS))

    # cross-language-group SC-B: every CALLS edge stays within one language group
    # (same-named greet()/main() across java/kt/py/rs must NOT be linked).
    ext_of = {n.qualified_name: (n.path or "") for n in ir.nodes}
    for e in ir.edges_of(CALLS):
        sg, dg = _ext_group(ext_of[e.src]), _ext_group(ext_of[e.dst])
        assert sg == dg, f"cross-language CALLS {e.src} -> {e.dst}"


def test_dispatch_single_repo_node(tmp_path):
    """ac-5a: the merged IR has exactly ONE Repo node."""
    _write(tmp_path, MIXED)
    ir = dispatch(tmp_path, PROV, repo_name="M")
    repos = ir.nodes_of(REPO)
    assert len(repos) == 1
    assert repos[0].qualified_name == "M"


def test_dispatch_pure_java_identical_to_extract_java(tmp_path):
    """ac-4: a single-language (Java) tree is byte-identical to extract_java —
    preserving every existing Java ingest/backfill/reconcile invariant."""
    _write(tmp_path, {"Service.java": JAVA_SRC})
    disp = dispatch(tmp_path, PROV, repo_name="J")
    direct = extract_java(tmp_path, PROV, repo_name="J")
    assert disp.nodes == direct.nodes
    assert disp.edges == direct.edges


def test_dispatch_community_deterministic(tmp_path):
    """ac-5b: augment_communities over two independent dispatch runs of the same
    mixed corpus yields the identical Community membership."""
    _write(tmp_path, MIXED)

    def membership():
        ir = dispatch(tmp_path, PROV, repo_name="M")
        augment_communities(ir, PROV)
        comms = {n.qualified_name for n in ir.nodes_of(COMMUNITY)}
        members = {(e.src, e.dst) for e in ir.edges_of(MEMBER_OF)}
        return comms, members

    assert membership() == membership()
