"""wi_260713c7t — Spring HTTP-API semantics for the Java extractor (ac-2, ac-9).

WHY THESE TESTS EXIST
  ac-2 (Endpoint emission): a Spring controller must yield ``Endpoint`` nodes whose
    identity is the ``spring:``-discriminated, normalized ``"METHOD PATH"`` (design
    contract Decisions 1 & 2). Two shapes are pinned:
      * the LEGACY HYBRID — a plain ``@Controller`` whose ``@ResponseBody`` methods
        carry a method-less ``@RequestMapping`` (CommuteController): the method-less
        mapping answers any verb, so the endpoint method is the ``ANY_METHOD`` "*"
        sentinel; a plain view-returning ``@Controller`` method (no ``@ResponseBody``)
        is deliberately NOT an API Endpoint (honest exclusion, not a false positive);
      * the MODERN REST — ``@RestController`` + ``@GetMapping`` with a class-level
        ``@RequestMapping`` base and a ``@PathVariable``: the base joins the method
        path, and the native param syntax ``{id}`` is PRESERVED at identity level
        (erasing it would collapse sibling routes).
    Every emitted id carries the ``spring:`` discriminator so the SvelteKit f/e plane
    (prefix-less ids) is byte-untouched. The handler Method HANDLES the Endpoint and
    the File REALIZES it (SvelteKit routing-edge precedent).
  ac-9 (DI role + injected deps): ``@Service`` / ``@Repository`` / ``@Controller``
    classes carry the right ``role`` marker (a pure property off identity), and
    injected dependencies — ``@Autowired`` fields AND constructor-injection params —
    stay on the EXISTING ``DEPENDS_ON`` edge. NO new edge kind is introduced for DI.

Scope-local mock-unit: drives the real ``extract()`` over small hermetic corpora
(tmp trees + the shared CommuteController fixture, read-only); no DB, no network.
"""

from pathlib import Path

from palimpsest.ir import (
    Provenance,
    ENDPOINT,
    DEPENDS_ON,
    ANY_METHOD,
    REALIZES,
    HANDLES,
)
from palimpsest.extract import extract
from palimpsest.extract.spring import (
    AnnotationInfo,
    spring_role,
    spring_endpoints,
    join_route,
    endpoint_qualified_name,
)

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)

FIXTURES = Path(__file__).parent / "fixtures"
CTRL_FILE = "src/main/java/kr/co/ecoletree/service/commute/controller/CommuteController.java"


def _extract_ir(tmp_path, files):
    """Extract an IR from an inline ``{relpath: java-source}`` corpus."""
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="T")


# --- ac-2: legacy hybrid @Controller + @ResponseBody + method-less @RequestMapping --

def test_ac2_hybrid_responsebody_methods_become_any_method_endpoints():
    ir = extract(FIXTURES, PROV, repo_name="EcoleTreeSystems")

    # @ResponseBody + method-less @RequestMapping -> ANY_METHOD sentinel, path normalized.
    ep = ir.node("spring:* /selectAttedanceCondition")
    assert ep is not None, "expected a spring: Endpoint for a @ResponseBody hybrid method"
    assert ep.kind == ENDPOINT
    assert ep.name == ANY_METHOD  # method-less mapping -> "*"
    assert ir.node("spring:* /insertGotoWork") is not None
    assert ir.node("spring:* /selectCheckEditable") is not None

    # A plain view-returning @Controller method (no @ResponseBody) is NOT an Endpoint.
    assert ir.node("spring:* /commute/main") is None

    # Every emitted Endpoint id carries the spring: discriminator (f/e plane untouched).
    endpoints = ir.nodes_of(ENDPOINT)
    assert endpoints, "hybrid controller should emit at least one Endpoint"
    assert all(n.qualified_name.startswith("spring:") for n in endpoints)

    # HANDLES (handler Method -> Endpoint) and REALIZES (File -> Endpoint).
    handler = (
        "kr.co.ecoletree.service.commute.controller.CommuteController"
        "#selectAttedanceCondition(Map,HttpServletRequest)"
    )
    assert ir.has_edge(HANDLES, handler, "spring:* /selectAttedanceCondition")
    assert ir.has_edge(REALIZES, CTRL_FILE, "spring:* /selectAttedanceCondition")


def test_ac2_hybrid_controller_role_is_controller():
    ir = extract(FIXTURES, PROV, repo_name="EcoleTreeSystems")
    ctrl = ir.node("kr.co.ecoletree.service.commute.controller.CommuteController")
    assert ctrl is not None
    assert ctrl.role == "controller"


# --- ac-2: modern @RestController / @GetMapping with base path + @PathVariable -------

def test_ac2_modern_restcontroller_joins_base_and_preserves_param(tmp_path):
    ir = _extract_ir(tmp_path, {
        "OrderController.java": (
            "package p;\n"
            "import org.springframework.web.bind.annotation.RestController;\n"
            "import org.springframework.web.bind.annotation.RequestMapping;\n"
            "import org.springframework.web.bind.annotation.GetMapping;\n"
            "import org.springframework.web.bind.annotation.PathVariable;\n"
            "@RestController\n"
            "@RequestMapping(\"/api/orders\")\n"
            "public class OrderController {\n"
            "  @GetMapping(\"/{id}\")\n"
            "  public String get(@PathVariable String id) { return id; }\n"
            "}\n"
        ),
    })
    # base @RequestMapping joined with method @GetMapping; identity keeps {id} verbatim.
    ep = ir.node("spring:GET /api/orders/{id}")
    assert ep is not None, "expected spring:GET /api/orders/{id}"
    assert ep.kind == ENDPOINT
    assert ep.name == "GET"

    handler = "p.OrderController#get(String)"
    assert ir.has_edge(HANDLES, handler, "spring:GET /api/orders/{id}")
    assert ir.has_edge(REALIZES, "OrderController.java", "spring:GET /api/orders/{id}")

    # @RestController implies a body response -> no separate @ResponseBody needed.
    assert ir.node("p.OrderController").role == "controller"


# --- ac-9: stereotype roles + injected deps on DEPENDS_ON (no new edge kind) ---------

def test_ac9_stereotype_roles_and_injected_deps_reuse_depends_on(tmp_path):
    ir = _extract_ir(tmp_path, {
        "OrderService.java": (
            "package p;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service\n"
            "public class OrderService {\n"
            "  private final OrderRepo repo;\n"
            "  public OrderService(OrderRepo repo) { this.repo = repo; }\n"  # ctor injection
            "}\n"
        ),
        "OrderRepo.java": (
            "package p;\n"
            "import org.springframework.stereotype.Repository;\n"
            "@Repository\n"
            "public class OrderRepo {}\n"
        ),
        "PageController.java": (
            "package p;\n"
            "import org.springframework.stereotype.Controller;\n"
            "import org.springframework.beans.factory.annotation.Autowired;\n"
            "@Controller\n"
            "public class PageController {\n"
            "  @Autowired OrderService svc;\n"  # field injection
            "}\n"
        ),
    })
    # Stereotype -> role marker.
    assert ir.node("p.OrderService").role == "service"
    assert ir.node("p.OrderRepo").role == "repository"
    assert ir.node("p.PageController").role == "controller"

    # Constructor-injection param type is already a class dependency -> DEPENDS_ON.
    assert ir.has_edge(DEPENDS_ON, "p.OrderService", "p.OrderRepo")
    # @Autowired field type -> DEPENDS_ON.
    assert ir.has_edge(DEPENDS_ON, "p.PageController", "p.OrderService")

    # NO new edge kind for injection: only the known deterministic structural set
    # (+ Spring routing edges) may appear.
    known = {"CONTAINS", "IMPORTS", "CALLS", DEPENDS_ON, REALIZES, HANDLES}
    assert {e.kind for e in ir.edges} <= known


def test_ac9_precedence_controller_over_service(tmp_path):
    # A class carrying BOTH @Controller and @Service resolves to "controller"
    # (precedence controller > repository > service > component).
    ir = _extract_ir(tmp_path, {
        "Mixed.java": (
            "package p;\n"
            "import org.springframework.stereotype.Controller;\n"
            "import org.springframework.stereotype.Service;\n"
            "@Service\n@Controller\npublic class Mixed {}\n"
        ),
    })
    assert ir.node("p.Mixed").role == "controller"


# --- spring.py public-API contract (the frozen surface kotlin.py imports) ------------
# These pin the grammar-agnostic mapper directly (no parser), so the kotlin node can
# rely on identical METHOD/PATH/ROLE/gate semantics.

def _ann(name, *args, **named):
    return AnnotationInfo(name=name, args=tuple(args), named_args=dict(named))


def test_spring_role_precedence():
    assert spring_role([_ann("RestController")]) == "controller"
    assert spring_role([_ann("Repository")]) == "repository"
    assert spring_role([_ann("Service")]) == "service"
    assert spring_role([_ann("Component")]) == "component"
    # precedence controller > repository > service > component (order-independent)
    assert spring_role([_ann("Component"), _ann("Service"), _ann("Controller")]) == "controller"
    assert spring_role([_ann("Component"), _ann("Repository")]) == "repository"
    assert spring_role([_ann("NotAStereotype")]) is None


def test_join_route_and_qualified_name():
    assert join_route("", "/commute/main") == "/commute/main"
    assert join_route("/api/orders", "/{id}") == "/api/orders/{id}"   # param preserved
    assert join_route("/service/", "/x") == "/service/x"              # // collapsed
    assert join_route("", "") == "/"                                   # empty+empty -> root
    assert endpoint_qualified_name("GET", "/api/orders") == "spring:GET /api/orders"
    assert endpoint_qualified_name(ANY_METHOD, "/x") == "spring:* /x"


def test_spring_endpoints_gate_and_cartesian():
    rest = [_ann("RestController")]
    plain = [_ann("Controller")]

    # @RestController + @GetMapping -> one GET endpoint.
    assert spring_endpoints(rest, [_ann("GetMapping", "/x")]) == [("GET", "spring:GET /x")]

    # plain @Controller WITHOUT @ResponseBody -> not an API Endpoint (honest exclusion).
    assert spring_endpoints(plain, [_ann("RequestMapping", "/x")]) == []
    # plain @Controller WITH method @ResponseBody -> emitted; method-less -> ANY_METHOD.
    assert spring_endpoints(plain, [_ann("RequestMapping", "/x"), _ann("ResponseBody")]) == [
        (ANY_METHOD, "spring:* /x")
    ]

    # @RequestMapping(method={POST,PUT}) -> one Endpoint per verb (cartesian).
    got = spring_endpoints(rest, [_ann("RequestMapping", value="/x", method="{RequestMethod.POST, RequestMethod.PUT}")])
    assert got == [("POST", "spring:POST /x"), ("PUT", "spring:PUT /x")]

    # a non-controller class emits nothing regardless of mapping annotations.
    assert spring_endpoints([_ann("Service")], [_ann("GetMapping", "/x")]) == []
