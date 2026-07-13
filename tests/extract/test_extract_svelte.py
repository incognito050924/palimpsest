"""TDD for the Svelte 2-level extractor (wi_260713lom, n5 — ac-3).

WHY these tests exist (ac-3, the 2-level mechanism from the n3 spike):
  A ``.svelte`` file is NOT ECMAScript at the top level — it is markup with one or
  more ``<script>`` blocks. tree-sitter-svelte exposes each block as
  ``script_element > raw_text`` with a clean byte range. The extractor must (1) parse
  the ``.svelte`` with the svelte grammar, (2) for EACH ``script_element`` read its
  ``lang`` (default = JavaScript; ``ts``/``typescript`` = TypeScript), slice the
  ``raw_text`` bytes, re-parse that slice with the TS or JS grammar, and (3) drive the
  REUSED ECMAScript ``_EcmaWalker`` over the sub-tree with ``line_offset`` so emitted
  node lines AND call-site lines map back to the REAL ``.svelte`` line numbers. All
  script blocks of one ``.svelte`` share ONE File node whose qualified_name is the
  ``.svelte`` repo-relative path (callables use that path as modpath).

What each test pins:
  * offset correctness (the load-bearing property): a FUNCTION extracted from a
    ``<script>`` that starts on line N of the ``.svelte`` must carry the REAL svelte
    ``start_line``, not the line within the sliced script. Same for CALLS call sites.
  * lang routing + ac-4 asymmetry inherited from n6: a ``lang="ts"`` block yields
    DEPENDS_ON (type annotations present); a ``lang="js"`` block (or absent lang,
    which defaults to JS) yields NONE — verified by absence paired with a positive
    control (the function IS still extracted, only the type edge is absent).
  * Svelte 5 dual ``module``+instance scripts: iterate ALL script_elements, never
    assume one.
  * robustness: a no-script component does not crash; an external ``src`` script and
    a whitespace-only script are SKIPPED (paired with a real inline script so the
    skip is observable, not falsifiable-by-silence).
"""

from palimpsest.ir import (
    Provenance,
    FILE,
    FUNCTION,
    CLASS,
    IMPORTS,
    CALLS,
    DEPENDS_ON,
)
from palimpsest.extract.svelte import extract as extract_svelte
from palimpsest.extract import extract_ecmascript

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="dev <dev@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)


def _extract(tmp_path, files, repo_name="S"):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_svelte(tmp_path, PROV, repo_name=repo_name)


# --- ac-3 core: <script lang="ts"> -> FUNCTION/IMPORTS/CALLS at REAL svelte lines ---

# Line map (1-indexed) is the ORACLE for offset correctness:
#   1: <script lang="ts">
#   2:   import { onMount } from 'svelte';
#   3:   (blank)
#   4:   export function handleClick(e: Event): void {
#   5:     doThing();
#   6:   }
#   7:   (blank)
#   8:   function doThing(): void {}
#   9: </script>
#  10:   (blank)
#  11: <div>hello</div>
COMPONENT_TS = """<script lang="ts">
  import { onMount } from 'svelte';

  export function handleClick(e: Event): void {
    doThing();
  }

  function doThing(): void {}
</script>

<div>hello</div>
"""


def test_svelte_ts_script_functions_at_real_svelte_lines(tmp_path):
    ir = _extract(tmp_path, {"comp.svelte": COMPONENT_TS})

    # ONE File node for the whole .svelte, keyed on the repo-relative .svelte path.
    fnode = ir.node("comp.svelte")
    assert fnode is not None and fnode.kind == FILE

    # handleClick(e: Event) -> FUNCTION at the REAL svelte line 4 (NOT line 3 within
    # the sliced script). paramType "Event" is captured for identity.
    hc = ir.node("comp.svelte.handleClick(Event)")
    assert hc is not None and hc.kind == FUNCTION
    assert hc.path == "comp.svelte"
    assert hc.start_line == 4

    # a second callable in the same block, also offset-correct.
    dt = ir.node("comp.svelte.doThing()")
    assert dt is not None and dt.start_line == 8

    # IMPORTS: the walker emits IMPORTS(file, specifier); a bare specifier ('svelte')
    # is left raw by _resolve_imports.
    assert ir.has_edge(IMPORTS, "comp.svelte", "svelte")

    # CALLS: handleClick() calls doThing() at line 5 -> resolves within the .svelte's
    # own nodes. (The call SITE line must also be offset to the real svelte line for
    # the innermost-range resolution to land inside handleClick's [4,6] span.)
    assert ir.has_edge(CALLS, "comp.svelte.handleClick(Event)", "comp.svelte.doThing()")


# --- ac-3 x ac-4 (inherited from n6): a lang="ts" block emits DEPENDS_ON ---

DEPENDS_TS = """<script lang="ts">
  export class Service {
    dep: Foo;
  }

  export class Foo {}
</script>
"""


def test_svelte_ts_script_emits_depends_on(tmp_path):
    ir = _extract(tmp_path, {"svc.svelte": DEPENDS_TS})
    # a TS class field `dep: Foo` -> Service DEPENDS_ON Foo, resolved over the
    # .svelte fragment's own CLASS nodes (Class -> Class), keyed on the .svelte path.
    assert ir.node("svc.svelte.Service") is not None
    assert ir.node("svc.svelte.Service").kind == CLASS
    assert ir.has_edge(DEPENDS_ON, "svc.svelte.Service", "svc.svelte.Foo")


# --- ac-3 x ac-4 asymmetry: a lang="js" block emits NO DEPENDS_ON ---

JS_COMPONENT = """<script lang="js">
  export function build(cfg) {
    helper();
  }

  function helper() {}
</script>
"""


def test_svelte_js_script_no_depends_on(tmp_path):
    ir = _extract(tmp_path, {"comp.svelte": JS_COMPONENT})
    # positive control: the JS function IS extracted (param untyped -> `?`), so the
    # empty DEPENDS_ON below is meaningful, not falsifiable-by-silence.
    assert ir.node("comp.svelte.build(?)") is not None
    assert ir.has_edge(CALLS, "comp.svelte.build(?)", "comp.svelte.helper()")
    # asymmetry: JS carries no type annotations -> zero DEPENDS_ON.
    assert ir.edges_of(DEPENDS_ON) == []


# --- ac-3: an ABSENT lang attribute defaults to JavaScript ---

DEFAULT_COMPONENT = """<script>
  export function greet(name) {}
</script>
"""


def test_svelte_default_lang_is_javascript(tmp_path):
    ir = _extract(tmp_path, {"def.svelte": DEFAULT_COMPONENT})
    # default (no lang) = JS: the function is extracted, its param degrades to `?`
    # (JS has no annotations) and no DEPENDS_ON is emitted.
    assert ir.node("def.svelte.greet(?)") is not None
    assert ir.edges_of(DEPENDS_ON) == []


# --- ac-3: Svelte 5 dual module+instance scripts -> BOTH extracted ---

# Line map (1-indexed):
#   1: <script module lang="ts">
#   2:   export function moduleFn(): void {}
#   3: </script>
#   4: <script lang="ts">
#   5:   export function instanceFn(): void {}
#   6: </script>
DUAL_SCRIPT = """<script module lang="ts">
  export function moduleFn(): void {}
</script>
<script lang="ts">
  export function instanceFn(): void {}
</script>
"""


def test_svelte_dual_module_and_instance_scripts(tmp_path):
    ir = _extract(tmp_path, {"dual.svelte": DUAL_SCRIPT})
    # BOTH script blocks are walked (do not assume one); both share the ONE File node.
    mf = ir.node("dual.svelte.moduleFn()")
    inf = ir.node("dual.svelte.instanceFn()")
    assert mf is not None and inf is not None
    # offset is per-block: moduleFn on real line 2 (first block, offset 0),
    # instanceFn on real line 5 (second block, offset from its own start).
    assert mf.start_line == 2
    assert inf.start_line == 5


# --- ac-3 robustness: a no-script component does not crash and yields no callables ---

NO_SCRIPT = """<div>just markup {count}</div>
<style>.x { color: red; }</style>
"""


def test_svelte_no_script_component_does_not_crash(tmp_path):
    ir = _extract(tmp_path, {"plain.svelte": NO_SCRIPT})
    # a File node still exists (the file participates in the graph / imports)...
    assert ir.node("plain.svelte") is not None
    # ...but there are no callables to extract.
    assert [n for n in ir.nodes_of(FUNCTION) if n.path == "plain.svelte"] == []


# --- ac-3: an external `src` script is SKIPPED (paired with a real inline script) ---

EXTERNAL_SRC = """<script src="https://cdn.example/lib.js"></script>
<script lang="ts">
  export function localFn(): void {}
</script>
"""


def test_svelte_external_src_script_is_skipped(tmp_path):
    ir = _extract(tmp_path, {"ext.svelte": EXTERNAL_SRC})
    # the external (src=...) block has no inline body and MUST be skipped; the real
    # inline block is still extracted (the skip is observable, not silent).
    assert ir.node("ext.svelte.localFn()") is not None


# --- ac-3: a whitespace-only script is SKIPPED (paired with a real inline script) ---

EMPTY_SCRIPT = """<script lang="ts">

</script>
<script lang="ts">
  export function realFn(): void {}
</script>
"""


def test_svelte_empty_script_is_skipped(tmp_path):
    ir = _extract(tmp_path, {"empty.svelte": EMPTY_SCRIPT})
    # the whitespace-only block is skipped; the real one is extracted.
    assert ir.node("empty.svelte.realFn()") is not None


# --- ac-3 unified driver (SC-B): a mixed repo's .svelte joins the ONE unified IR ---

def test_svelte_joins_unified_ecmascript_ir(tmp_path):
    files = {
        "comp.svelte": COMPONENT_TS,
        "util.ts": "export function util(): void {}\n",
    }
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    ir = extract_ecmascript(tmp_path, PROV, repo_name="U")
    # the .svelte file's nodes are present in the unified IR alongside the .ts nodes.
    assert ir.node("comp.svelte.handleClick(Event)") is not None
    assert ir.node("util.ts.util()") is not None
    # both files are contained by the single Repo node.
    assert ir.has_edge("CONTAINS", "U", "comp.svelte")
    assert ir.has_edge("CONTAINS", "U", "util.ts")


# ── is_test marker (issue #17: multilang test-impact) ──────────────────────────
# Svelte funnels through the shared ecmascript finalize_ir, so the same signals
# apply: *.test.* / *.spec.* filename, or a jest/vitest/mocha import in <script>.

_SV_TEST = '<script>\n  export function mount() {}\n</script>\n\n<div/>\n'
_SV_IMPORT_ONLY = '<script>\n  import { it } from "vitest";\n  export function helper() {}\n</script>\n\n<div/>\n'
_SV_PROD = '<script>\n  export function render() {}\n</script>\n\n<div/>\n'


def test_is_test_marked_by_test_svelte_filename(tmp_path):
    ir = _extract(tmp_path, {"Widget.test.svelte": _SV_TEST, "Widget.svelte": _SV_PROD})
    for n in ir.nodes:
        if n.path == "Widget.test.svelte" and n.kind in (FILE, FUNCTION):
            assert n.is_test is True, (n.kind, n.qualified_name)
        if n.path == "Widget.svelte":
            assert not n.is_test, (n.kind, n.qualified_name)


def test_is_test_marked_by_vitest_import_in_script(tmp_path):
    ir = _extract(tmp_path, {"Panel.svelte": _SV_IMPORT_ONLY})
    code = [n for n in ir.nodes if n.kind in (FILE, FUNCTION)]
    assert code and all(n.is_test for n in code)
