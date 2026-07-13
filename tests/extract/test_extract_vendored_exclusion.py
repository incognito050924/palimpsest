"""TDD for vendored/build directory exclusion in the ECMAScript + Svelte walks
(wi_260713lom, n9 — ac-5: "index the repo" means the repo's OWN source).

WHY these tests exist (review n7 MEDIUM):
  The ECMAScript (`.ts/.tsx/.js/.jsx`) and Svelte (`.svelte`) file walks recurse the
  whole tree with ``rglob``. On a real repo that pulls in ``node_modules``, build
  output (``dist``/``build``/``out``/``.svelte-kit``/``.next``), the VCS dir
  (``.git``) and ``coverage`` — polluting the graph with vendored/generated code that
  is not the repo's own source. The walk must SKIP any file whose path-relative-to-root
  has a directory segment in the excluded set, in BOTH extractors.

What each test pins:
  * ecmascript walk (ts/tsx/js/jsx via ``_iter_files``): a vendored ``.ts``/``.js``
    under an excluded dir yields NO File node; a real ``src/*.ts`` DOES (positive
    control — the exclusion is observable, not falsifiable-by-silence).
  * svelte walk (``.svelte`` rglob): a vendored ``.svelte`` under ``.svelte-kit``
    yields NO File node; a real ``src/*.svelte`` DOES.
  * "ANY path segment" semantics: an excluded dir nested BELOW the root
    (``src/node_modules/...``) is also excluded, not just a top-level one.
"""

from palimpsest.ir import Provenance, FILE
from palimpsest.extract.typescript import extract as extract_ts
from palimpsest.extract.svelte import extract as extract_svelte
from palimpsest.extract import extract_ecmascript

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

_SRC_TS = "export function real(): void {}\n"
_SRC_JS = "export function vendored() {}\n"
_SRC_SVELTE = "<script lang=\"ts\">\n  export function comp(): void {}\n</script>\n"


def _write(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


def _file_qnames(ir):
    return {n.qualified_name for n in ir.nodes_of(FILE)}


def test_ecmascript_walk_excludes_vendored_and_build_dirs(tmp_path):
    _write(
        tmp_path,
        {
            "src/app.ts": _SRC_TS,               # real source -> File node
            "node_modules/pkg/index.js": _SRC_JS,  # vendored dep -> skipped
            "dist/bundle.js": _SRC_JS,             # build output -> skipped
            "build/gen.ts": _SRC_TS,               # build output -> skipped
            "out/page.js": _SRC_JS,                # build output -> skipped
            "coverage/lcov.js": _SRC_JS,           # coverage report -> skipped
            "src/node_modules/nested.ts": _SRC_TS,  # ANY segment -> skipped
        },
    )
    ir = extract_ecmascript(tmp_path, PROV, repo_name="R")
    qnames = _file_qnames(ir)
    # positive control: the repo's own source IS indexed.
    assert "src/app.ts" in qnames
    # every vendored/build file is EXCLUDED (no File node).
    for excluded in (
        "node_modules/pkg/index.js",
        "dist/bundle.js",
        "build/gen.ts",
        "out/page.js",
        "coverage/lcov.js",
        "src/node_modules/nested.ts",
    ):
        assert excluded not in qnames, f"vendored path leaked into graph: {excluded}"


def test_typescript_standalone_walk_excludes_vendored(tmp_path):
    # exercise ecmascript's _iter_files directly through the TS-only extractor.
    _write(
        tmp_path,
        {
            "src/mod.ts": _SRC_TS,
            "node_modules/dep/index.ts": _SRC_TS,
        },
    )
    ir = extract_ts(tmp_path, PROV, repo_name="T")
    qnames = _file_qnames(ir)
    assert "src/mod.ts" in qnames
    assert "node_modules/dep/index.ts" not in qnames


def test_svelte_walk_excludes_vendored_build_dirs(tmp_path):
    _write(
        tmp_path,
        {
            "src/Widget.svelte": _SRC_SVELTE,               # real -> File node
            ".svelte-kit/generated/x.svelte": _SRC_SVELTE,  # generated -> skipped
            "node_modules/comp/Lib.svelte": _SRC_SVELTE,    # vendored -> skipped
            ".next/cache.svelte": _SRC_SVELTE,              # build -> skipped
        },
    )
    ir = extract_svelte(tmp_path, PROV, repo_name="S")
    qnames = _file_qnames(ir)
    assert "src/Widget.svelte" in qnames
    for excluded in (
        ".svelte-kit/generated/x.svelte",
        "node_modules/comp/Lib.svelte",
        ".next/cache.svelte",
    ):
        assert excluded not in qnames, f"vendored .svelte leaked into graph: {excluded}"
