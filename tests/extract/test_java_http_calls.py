"""TDD for the JVM outbound-HTTP caller scanner — Java tier (wi_260713iah).

WHY THESE TESTS EXIST
  The Java analogue of ``ecmascript.py:_scan_http_calls``: a call whose chain-root
  receiver resolves (via a single-type import) to a REGISTERED JVM HTTP construct
  (``WebClient`` / ``RestTemplate``, ``http_origins.HTTP_CONSTRUCTS``) and that
  carries a LITERAL URL at the call site becomes an :data:`API_CALL` node with the
  ``apicall:{METHOD} {path}`` identity (``ir.api_call_qualified_name``). Recognition
  keys off the resolved import ORIGIN (the SAME ``is_recognized_call`` rule the JS
  scanner uses), never call syntax.

  Each clause below pins one AC of the literal-callsite scanner:
    * RestTemplate verb method (``getForObject`` -> GET) with a literal URL arg[0].
    * WebClient fluent chain (``webClient.get().uri("/x")``): verb from ``.get()``,
      URL from ``.uri(...)``.
    * ``exchange(url, HttpMethod.GET, ...)``: verb from the literal HttpMethod arg.
    * Honest gap A — an in-house wrapper (``io.incognito.rest.client``) resolves to
      an UNREGISTERED origin -> NOTHING emitted (Frozen Invariant 5).
    * Honest gap B — a non-literal (variable / helper-passed) URL -> NOTHING emitted;
      the one-hop param->uri dataflow is a SEPARATE downstream node, not this one.
"""

from pathlib import Path

from palimpsest.ir import Provenance, API_CALL
from palimpsest.extract.java import extract as extract_java

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_java(tmp_path, PROV, repo_name="R")


def _apicall_qns(ir):
    return {n.qualified_name for n in ir.nodes_of(API_CALL)}


_REST = (
    "package com.x;\n"
    "import org.springframework.web.client.RestTemplate;\n"
    "class OrderClient {\n"
    "  private final RestTemplate restTemplate;\n"
    "  Object load() { return restTemplate.getForObject(\"/api/v1/orders\", C.class); }\n"
    "}\n"
)

_WEBCLIENT = (
    "package com.x;\n"
    "import org.springframework.web.reactive.function.client.WebClient;\n"
    "class OrderClient {\n"
    "  private final WebClient webClient;\n"
    "  Object load() { return webClient.get().uri(\"/api/v1/orders\").retrieve(); }\n"
    "}\n"
)


def test_resttemplate_literal_get_is_apicall(tmp_path):
    ir = _extract(tmp_path, {"OrderClient.java": _REST})
    assert "apicall:GET /api/v1/orders" in _apicall_qns(ir)


def test_webclient_fluent_uri_literal_is_apicall(tmp_path):
    ir = _extract(tmp_path, {"OrderClient.java": _WEBCLIENT})
    assert "apicall:GET /api/v1/orders" in _apicall_qns(ir)


def test_resttemplate_exchange_httpmethod_literal(tmp_path):
    # exchange(url, HttpMethod.GET, ...) -> verb from the literal HttpMethod arg.
    src = (
        "package com.x;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "class C {\n"
        "  private final RestTemplate restTemplate;\n"
        "  void go() { restTemplate.exchange(\"/api/v1/e\", HttpMethod.POST, null, C.class); }\n"
        "}\n"
    )
    ir = _extract(tmp_path, {"C.java": src})
    assert "apicall:POST /api/v1/e" in _apicall_qns(ir)


def test_inhouse_wrapper_is_not_recognized(tmp_path):
    # An in-house HTTP wrapper resolves to an UNREGISTERED origin -> disclosed gap.
    src = (
        "package com.x;\n"
        "import io.incognito.rest.client.RestClient;\n"
        "class C {\n"
        "  private final RestClient restClient;\n"
        "  void go() { restClient.getForObject(\"/api/v1/x\", C.class); }\n"
        "}\n"
    )
    ir = _extract(tmp_path, {"C.java": src})
    assert _apicall_qns(ir) == set()


def test_non_literal_url_emits_nothing(tmp_path):
    # A variable (helper-passed) URL is the dataflow node's target, NOT this one.
    src = (
        "package com.x;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "class C {\n"
        "  private final RestTemplate restTemplate;\n"
        "  void go(String url) { restTemplate.getForObject(url, C.class); }\n"
        "}\n"
    )
    ir = _extract(tmp_path, {"C.java": src})
    assert _apicall_qns(ir) == set()
