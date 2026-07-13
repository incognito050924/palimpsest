"""TDD for the one-hop param->uri dataflow recovery (wi_260713iah, part 1).

WHY THESE TESTS EXIST (background for approver + consumer)
  The literal-callsite JVM scanner (``test_java_http_calls.py``) deliberately emits
  NOTHING when a recognized HTTP call's URL is a bare parameter (helper-passed). The
  REAL boxwood ``PortalV2ApiClientImpl`` follows exactly that shape: public methods
  call a private helper ``executeGet("/api/v1/connectors/{id}", ...)`` with a LITERAL,
  and the helper forwards its ``String url`` parameter into ``client.get().uri(b ->
  b.path(url))``. Without dataflow the engine->portal cross-tier link stays dark.

  This node recovers it with STRICTLY ONE hop: bind the literal argument at the helper
  CALL site to the ``uri(...)`` parameter inside the helper, and emit the ApiCall with
  the recovered literal path. The clauses pinned here:
    * one-hop recovery: a WebClient helper whose ``String url`` flows into ``uri(...)``,
      called with a literal, yields the recovered ``apicall:{verb} {path}`` node.
    * one-hop HONESTY: a helper called with a VARIABLE (not a literal) recovers
      nothing (a second hop would be needed) — an honest gap, never a guessed route.
    * no in-house wrappers: a helper whose client resolves to an UNREGISTERED origin
      is not recognized, so nothing is recovered (Frozen Invariant 5).
    * Kotlin mirror: the same shape over the Kotlin grammar recovers identically.
"""

from pathlib import Path

from palimpsest.ir import Provenance, API_CALL
from palimpsest.extract.java import extract as extract_java
from palimpsest.extract.kotlin import extract as extract_kotlin

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)


def _extract_java(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_java(tmp_path, PROV, repo_name="R")


def _extract_kotlin(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_kotlin(tmp_path, PROV, repo_name="R")


def _apicall_qns(ir):
    return {n.qualified_name for n in ir.nodes_of(API_CALL)}


# The boxwood PortalV2ApiClientImpl shape: public method passes a literal to a private
# helper whose String parameter flows into webClient.get().uri(...).
_PORTAL_CLIENT = (
    "package com.x;\n"
    "import org.springframework.web.reactive.function.client.WebClient;\n"
    "class PortalV2ApiClientImpl {\n"
    "  private final WebClient webClient;\n"
    "  Object getConnector(String id) {\n"
    "    return executeGet(\"/api/v1/connectors/{id}\", id);\n"
    "  }\n"
    "  private Object executeGet(String url, Object body) {\n"
    "    return webClient.get().uri(b -> b.path(url).build()).retrieve();\n"
    "  }\n"
    "}\n"
)


def test_one_hop_recovers_helper_literal_java(tmp_path):
    ir = _extract_java(tmp_path, {"PortalV2ApiClientImpl.java": _PORTAL_CLIENT})
    assert "apicall:GET /api/v1/connectors/{id}" in _apicall_qns(ir)


# The REAL boxwood PortalV2ApiClientImpl shape: the helper takes BOTH the path
# `url` (flows into `.path(url)`) AND a `pathVariables` map (flows into
# `.build(pathVariables)`) into the SAME `uri(...)` lambda. The recovered param must
# be the one feeding `.path(...)` (index 0), not the map (index 1). At the call site
# arg[1] is a NON-literal map, so keying the wrong param recovers nothing (the bug).
_PORTAL_CLIENT_TWO_PARAMS = (
    "package com.x;\n"
    "import org.springframework.web.reactive.function.client.WebClient;\n"
    "class PortalV2ApiClientImpl {\n"
    "  private final WebClient webClient;\n"
    "  Object getConnector(String id) {\n"
    "    return executeGet(\"/api/v1/connectors/{id}\", makeMap(id));\n"
    "  }\n"
    "  private Object executeGet(String url, java.util.Map<String,String> pathVariables) {\n"
    "    return webClient.get()\n"
    "      .uri(b -> b.path(url).build(pathVariables)).retrieve();\n"
    "  }\n"
    "}\n"
)


def test_one_hop_prefers_path_param_over_build_param_java(tmp_path):
    # Both `url` and `pathVariables` flow into the uri lambda; recovery must key the
    # `.path(url)` param (index 0). Call site passes a literal there and a non-literal
    # map at index 1 -> only correct param selection recovers the route.
    ir = _extract_java(tmp_path, {"PortalV2ApiClientImpl.java": _PORTAL_CLIENT_TWO_PARAMS})
    assert "apicall:GET /api/v1/connectors/{id}" in _apicall_qns(ir)


def test_one_hop_only_variable_callsite_is_gap_java(tmp_path):
    # The helper is called with a VARIABLE argument (a second hop): recovering it
    # would need to trace where `p` came from -> honest gap, no ApiCall.
    src = (
        "package com.x;\n"
        "import org.springframework.web.reactive.function.client.WebClient;\n"
        "class C {\n"
        "  private final WebClient webClient;\n"
        "  Object go(String p) { return executeGet(p); }\n"
        "  private Object executeGet(String url) {\n"
        "    return webClient.get().uri(b -> b.path(url).build()).retrieve();\n"
        "  }\n"
        "}\n"
    )
    ir = _extract_java(tmp_path, {"C.java": src})
    assert _apicall_qns(ir) == set()


def test_one_hop_inhouse_wrapper_not_recovered_java(tmp_path):
    # The helper's client resolves to an UNREGISTERED in-house origin -> not a
    # recognized HTTP call, so no dataflow recovery.
    src = (
        "package com.x;\n"
        "import io.incognito.rest.client.RestClient;\n"
        "class C {\n"
        "  private final RestClient client;\n"
        "  Object go() { return executeGet(\"/api/v1/x\"); }\n"
        "  private Object executeGet(String url) {\n"
        "    return client.get().uri(b -> b.path(url).build()).retrieve();\n"
        "  }\n"
        "}\n"
    )
    ir = _extract_java(tmp_path, {"C.java": src})
    assert _apicall_qns(ir) == set()


def test_one_hop_recovers_helper_literal_kotlin(tmp_path):
    src = (
        "package com.x\n"
        "import org.springframework.web.reactive.function.client.WebClient\n"
        "class PortalClient(private val webClient: WebClient) {\n"
        "  fun getConnector(id: String): Any = executeGet(\"/api/v1/connectors/{id}\")\n"
        "  private fun executeGet(url: String): Any =\n"
        "    webClient.get().uri({ b -> b.path(url).build() }).retrieve()\n"
        "}\n"
    )
    ir = _extract_kotlin(tmp_path, {"PortalClient.kt": src})
    assert "apicall:GET /api/v1/connectors/{id}" in _apicall_qns(ir)


def test_recovered_apicall_is_marked_dataflow_derived_java(tmp_path):
    # ac-4: a one-hop dataflow-recovered ApiCall must carry the ``dataflow_derived``
    # marker on the NODE, so downstream (ingest -> loader -> matcher) can distinguish it
    # from a direct call-site literal and discount its cross-tier confidence. Without the
    # marker the recovered engine->portal link escapes to literal 1.0 (the real defect).
    ir = _extract_java(tmp_path, {"PortalV2ApiClientImpl.java": _PORTAL_CLIENT})
    recovered = [
        n for n in ir.nodes_of(API_CALL)
        if n.qualified_name == "apicall:GET /api/v1/connectors/{id}"
    ]
    assert recovered, "expected the dataflow-recovered ApiCall"
    assert recovered[0].dataflow_derived is True


def test_one_hop_prefers_path_param_over_build_param_kotlin(tmp_path):
    # Kotlin analogue of the real boxwood two-param helper: `url` -> `.path(url)`,
    # `pathVariables` -> `.build(pathVariables)` in the same uri lambda. Recovery must
    # key the `.path(...)` param (index 0), not the map (index 1).
    src = (
        "package com.x\n"
        "import org.springframework.web.reactive.function.client.WebClient\n"
        "class PortalClient(private val webClient: WebClient) {\n"
        "  fun getConnector(id: String): Any = executeGet(\"/api/v1/connectors/{id}\", makeMap(id))\n"
        "  private fun executeGet(url: String, pathVariables: Map<String,String>): Any =\n"
        "    webClient.get().uri({ b -> b.path(url).build(pathVariables) }).retrieve()\n"
        "}\n"
    )
    ir = _extract_kotlin(tmp_path, {"PortalClient.kt": src})
    assert "apicall:GET /api/v1/connectors/{id}" in _apicall_qns(ir)
