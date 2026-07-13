"""wi_260713c7t — Spring HTTP-API semantics for the Kotlin extractor (ac-1, ac-9).

WHY THESE TESTS EXIST
  ac-1 (Endpoint emission, Kotlin tier): a Kotlin ``@RestController`` must yield the
    SAME ``spring:``-discriminated, normalized ``"METHOD PATH"`` Endpoint identity the
    Java extractor produces (design contract Decisions 1 & 2), because both tiers
    consume the ONE grammar-agnostic ``extract.spring`` mapper — no per-language
    divergence. The load-bearing Kotlin-specific facts these tests pin:
      * class-level ``@RequestMapping("/api/v1/orders")`` base joins the method-level
        ``@GetMapping("/{id}")`` path, and the native param syntax ``{id}`` is PRESERVED
        at identity level (erasing it would collapse sibling routes);
      * the Kotlin annotation AST differs from Java (``@GetMapping("/x")`` reads as an
        ``annotation`` wrapping a ``constructor_invocation``; a bare ``@RestController``
        wraps a ``user_type``; array/named args ``@RequestMapping(value = ["/x"],
        method = [RequestMethod.POST])`` read through ``collection_literal`` nodes) —
        the new Kotlin reader must normalize all of these into ``AnnotationInfo`` so the
        shared mapper sees identical inputs;
      * every emitted id carries ``spring:`` so the SvelteKit f/e plane is untouched,
        the handler ``fun`` HANDLES the Endpoint and the File REALIZES it.
  ac-9 (DI role + injected deps, Kotlin tier): ``@Service`` / ``@RestController`` /
    ``@Repository`` classes carry the right ``role`` marker (a pure property off
    identity), and injected dependencies — Kotlin PRIMARY-CONSTRUCTOR injection params
    AND ``@Autowired`` property fields — stay on the EXISTING ``DEPENDS_ON`` edge. NO new
    edge kind is introduced for DI (Frozen Invariant 4).

Scope-local mock-unit: drives the real Kotlin ``extract()`` over small hermetic corpora
(the shared fixtures_kotlin_spring tree, read-only, one class per file — idiomatic
Kotlin, which also sidesteps a tree-sitter-kotlin multi-class-per-file misparse — plus
inline tmp trees); no DB, no network.
"""

from pathlib import Path

from palimpsest.ir import (
    Provenance,
    CLASS,
    ENDPOINT,
    DEPENDS_ON,
    REALIZES,
    HANDLES,
)
from palimpsest.extract.kotlin import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)

FIXTURES = Path(__file__).parent / "fixtures_kotlin_spring"
PKG = "kr.co.ecoletree.order"


def _extract_ir(tmp_path, files):
    """Extract an IR from an inline ``{relpath: kotlin-source}`` corpus."""
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="K")


# --- ac-1: modern @RestController / @GetMapping with base path + @PathVariable --------

def test_ac1_fixture_restcontroller_joins_base_and_preserves_param():
    ir = extract(FIXTURES, PROV, repo_name="Portal")

    # class @RequestMapping base joined with method @GetMapping; {id} kept verbatim.
    ep = ir.node("spring:GET /api/v1/orders/{id}")
    assert ep is not None, "expected spring:GET /api/v1/orders/{id}"
    assert ep.kind == ENDPOINT
    assert ep.name == "GET"

    # every emitted Endpoint id carries the spring: discriminator (f/e plane untouched).
    endpoints = ir.nodes_of(ENDPOINT)
    assert endpoints, "the controller should emit at least one Endpoint"
    assert all(n.qualified_name.startswith("spring:") for n in endpoints)

    # HANDLES (handler fun -> Endpoint) and REALIZES (File -> Endpoint).
    handler = f"{PKG}.OrderController#get(String)"
    assert ir.has_edge(HANDLES, handler, "spring:GET /api/v1/orders/{id}")
    assert ir.has_edge(REALIZES, "OrderController.kt", "spring:GET /api/v1/orders/{id}")


def test_ac1_requestmapping_named_array_value_and_method(tmp_path):
    # @RequestMapping(value = ["/legacy"], method = [RequestMethod.POST]) — the Kotlin
    # array/named-arg shape: value reads through a collection_literal of string
    # literals -> path "/legacy"; method reads the RequestMethod.X verb -> POST.
    ir = _extract_ir(tmp_path, {
        "LegacyController.kt": (
            "package p\n"
            "import org.springframework.web.bind.annotation.RestController\n"
            "import org.springframework.web.bind.annotation.RequestMapping\n"
            "@RestController\n"
            "class LegacyController {\n"
            "    @RequestMapping(value = [\"/legacy\"], method = [RequestMethod.POST])\n"
            "    fun legacy(): String {\n"
            "        return \"x\"\n"
            "    }\n"
            "}\n"
        ),
    })
    ep = ir.node("spring:POST /legacy")
    assert ep is not None, "expected spring:POST /legacy from named array-args @RequestMapping"
    assert ep.kind == ENDPOINT
    assert ep.name == "POST"
    assert ir.has_edge(HANDLES, "p.LegacyController#legacy()", "spring:POST /legacy")


# --- ac-9: stereotype roles + injected deps on DEPENDS_ON (no new edge kind) ---------

def test_ac9_fixture_stereotype_roles():
    ir = extract(FIXTURES, PROV, repo_name="Portal")
    assert ir.node(f"{PKG}.OrderController").role == "controller"
    assert ir.node(f"{PKG}.OrderService").role == "service"
    assert ir.node(f"{PKG}.OrderRepo").role == "repository"


def test_ac9_fixture_ctor_injection_reuses_depends_on():
    ir = extract(FIXTURES, PROV, repo_name="Portal")

    # Kotlin primary-constructor injection param types are class dependencies -> DEPENDS_ON.
    assert ir.has_edge(DEPENDS_ON, f"{PKG}.OrderController", f"{PKG}.OrderService")
    assert ir.has_edge(DEPENDS_ON, f"{PKG}.OrderService", f"{PKG}.OrderRepo")

    # NO new edge kind for injection: only the known deterministic structural set
    # (+ Spring routing edges) may appear.
    known = {"CONTAINS", "CALLS", DEPENDS_ON, REALIZES, HANDLES}
    assert {e.kind for e in ir.edges} <= known


def test_ac9_field_injection_reuses_depends_on(tmp_path):
    # @Autowired property (field injection) type is also a class dependency -> DEPENDS_ON.
    ir = _extract_ir(tmp_path, {
        "PageController.kt": (
            "package p\n"
            "import org.springframework.stereotype.Controller\n"
            "import org.springframework.beans.factory.annotation.Autowired\n"
            "@Controller\n"
            "class PageController {\n"
            "    @Autowired\n"
            "    lateinit var svc: OrderService\n"
            "}\n"
        ),
        "OrderService.kt": (
            "package p\n"
            "import org.springframework.stereotype.Service\n"
            "@Service\n"
            "class OrderService\n"
        ),
    })
    assert ir.node("p.PageController").role == "controller"
    assert ir.node("p.OrderService").role == "service"
    assert ir.has_edge(DEPENDS_ON, "p.PageController", "p.OrderService")


def test_ac9_precedence_controller_over_service(tmp_path):
    # A class carrying BOTH @Controller and @Service resolves to "controller"
    # (precedence controller > repository > service > component).
    ir = _extract_ir(tmp_path, {
        "Mixed.kt": (
            "package p\n"
            "import org.springframework.stereotype.Controller\n"
            "import org.springframework.stereotype.Service\n"
            "@Service\n@Controller\nclass Mixed\n"
        ),
    })
    m = ir.node("p.Mixed")
    assert m is not None and m.kind == CLASS
    assert m.role == "controller"
