"""TDD for the JVM outbound-HTTP caller scanner — Kotlin tier (wi_260713iah).

WHY THESE TESTS EXIST
  The Kotlin half of the SAME scanner the Java tier drives, through the shared
  grammar-agnostic ``extract.jvm_http`` module (spring.py's no-divergence norm).
  A call whose chain-root receiver resolves via a Kotlin ``import`` to a REGISTERED
  construct (``WebClient`` / ``RestTemplate``) and carries a LITERAL URL becomes the
  same ``apicall:{METHOD} {path}`` :data:`API_CALL` identity the Java tier produces.

  Clauses pinned here (mirroring the Java tests, plus the CROSS-GRAMMAR PARITY AC):
    * RestTemplate verb method + literal URL arg[0].
    * WebClient fluent chain ``webClient.get().uri("/x")``.
    * Honest gap — a non-literal (variable) URL -> NOTHING.
    * PARITY — a WebClient literal call yields the IDENTICAL ApiCall id whether it is
      written in a .java or a .kt file (single grammar-agnostic recognizer).
"""

from pathlib import Path

from palimpsest.ir import Provenance, API_CALL
from palimpsest.extract.kotlin import extract as extract_kotlin
from palimpsest.extract.java import extract as extract_java

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)


def _extract(extract_fn, tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract_fn(tmp_path, PROV, repo_name="R")


def _apicall_qns(ir):
    return {n.qualified_name for n in ir.nodes_of(API_CALL)}


_REST_KT = (
    "package com.x\n"
    "import org.springframework.web.client.RestTemplate\n"
    "class OrderClient(private val restTemplate: RestTemplate) {\n"
    "  fun load(): Any = restTemplate.getForObject(\"/api/v1/orders\", C::class.java)\n"
    "}\n"
)

_WEBCLIENT_KT = (
    "package com.x\n"
    "import org.springframework.web.reactive.function.client.WebClient\n"
    "class OrderClient(private val webClient: WebClient) {\n"
    "  fun load(): Any = webClient.get().uri(\"/api/v1/orders\").retrieve()\n"
    "}\n"
)


def test_resttemplate_literal_get_is_apicall(tmp_path):
    ir = _extract(extract_kotlin, tmp_path, {"OrderClient.kt": _REST_KT})
    assert "apicall:GET /api/v1/orders" in _apicall_qns(ir)


def test_webclient_fluent_uri_literal_is_apicall(tmp_path):
    ir = _extract(extract_kotlin, tmp_path, {"OrderClient.kt": _WEBCLIENT_KT})
    assert "apicall:GET /api/v1/orders" in _apicall_qns(ir)


def test_non_literal_url_emits_nothing(tmp_path):
    src = (
        "package com.x\n"
        "import org.springframework.web.client.RestTemplate\n"
        "class C(private val restTemplate: RestTemplate) {\n"
        "  fun go(url: String) { restTemplate.getForObject(url, C::class.java) }\n"
        "}\n"
    )
    ir = _extract(extract_kotlin, tmp_path, {"C.kt": src})
    assert _apicall_qns(ir) == set()


def test_cross_grammar_parity_webclient(tmp_path):
    # The SAME WebClient literal call recognized IDENTICALLY in .java and .kt.
    java_src = (
        "package com.x;\n"
        "import org.springframework.web.reactive.function.client.WebClient;\n"
        "class J {\n"
        "  private final WebClient webClient;\n"
        "  Object load() { return webClient.get().uri(\"/api/v1/orders\").retrieve(); }\n"
        "}\n"
    )
    (tmp_path / "j").mkdir()
    (tmp_path / "k").mkdir()
    (tmp_path / "j" / "J.java").write_text(java_src)
    (tmp_path / "k" / "K.kt").write_text(_WEBCLIENT_KT)
    java_ir = extract_java(tmp_path / "j", PROV, repo_name="R")
    kotlin_ir = extract_kotlin(tmp_path / "k", PROV, repo_name="R")
    java_calls = _apicall_qns(java_ir)
    kotlin_calls = _apicall_qns(kotlin_ir)
    assert "apicall:GET /api/v1/orders" in java_calls
    assert java_calls == kotlin_calls
