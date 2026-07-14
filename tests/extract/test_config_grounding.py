"""TDD for config base-url grounding — JVM S2S target binding (wi_260713iah, ac-5).

WHY THESE TESTS EXIST
  A JVM server-to-server caller injects its target host via
  ``@Value("${boxwood.portal.base-url}")`` and concatenates a path onto it
  (``restTemplate.getForObject(baseUrl + "/portal/api/x", ...)``). ac-5 requires that
  we GROUND that base-url against the module's own ``application.yaml`` so the emitted
  ApiCall binds the target SERVICE (its host) — a route that only coincidentally
  matches an Endpoint in a DIFFERENT service must not be falsely linked. When the
  property cannot be resolved to a single literal host, grounding yields an honest GAP
  (no binding), never a guessed one (ac-6 invariant).

  Each clause pins one AC of the grounding:
    * resolve — a plain literal ``base-url`` in application.yaml binds the host, and
      the concatenated-URL ApiCall carries that host (NOT the bare path).
    * @Value literal default — ``${key:http://localhost:8080}`` with the key absent
      from yaml resolves via the literal fallback.
    * gap — a missing key with no default, an env-only ``${ENV:...}`` value, a
      multi-profile disagreement, and a list/non-string value each yield NO binding;
      the concatenated call then emits NOTHING (honest gap, not a false ApiCall).
    * key-only binding — only the referenced key's scalar is returned; a sibling
      secret in the same yaml is never surfaced.
    * safe_load — a ``!!python/...`` tag is refused (no object construction / no code
      execution), degrading to a gap rather than running the tag.
"""

from pathlib import Path

from palimpsest.ir import Provenance, API_CALL
from palimpsest.extract.java import extract as extract_java
from palimpsest.extract.spring_config import (
    parse_value_ref,
    resolve_property,
    resolve_base_url,
    load_profile_maps,
)

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="fixture",
    committed_at="2026-01-01T00:00:00+00:00",
)


# --- pure resolver: parse_value_ref -------------------------------------------

def test_parse_value_ref_key_and_default():
    assert parse_value_ref("${boxwood.portal.base-url:http://localhost:8080}") == (
        "boxwood.portal.base-url",
        "http://localhost:8080",
    )


def test_parse_value_ref_no_default():
    assert parse_value_ref("${boxwood.portal.base-url}") == ("boxwood.portal.base-url", None)


def test_parse_value_ref_spel_is_gap():
    # A SpEL expression / composite is not a resolvable ${...} property ref.
    assert parse_value_ref("#{systemProperties['x']}") is None
    assert parse_value_ref("plain-string") is None


# --- pure resolver: resolve_property (shape classification) -------------------

_BASE = {None: {"boxwood": {"portal": {"base-url": "http://dwp-b-portal-service:8080"}}}}


def test_resolve_property_single_literal():
    assert resolve_property(_BASE, "boxwood.portal.base-url", None) == "http://dwp-b-portal-service:8080"


def test_resolve_property_missing_key_no_default_is_gap():
    assert resolve_property(_BASE, "boxwood.engine.base-url", None) is None


def test_resolve_property_literal_default_when_key_absent():
    assert (
        resolve_property(_BASE, "boxwood.engine.base-url", "http://localhost:8088")
        == "http://localhost:8088"
    )


def test_resolve_property_env_only_value_is_gap():
    maps = {None: {"boxwood": {"portal": {"base-url": "${BOXWOOD_PORTAL_BASE_URL:http://backend:8080}"}}}}
    assert resolve_property(maps, "boxwood.portal.base-url", None) is None


def test_resolve_property_list_value_is_gap():
    maps = {None: {"boxwood": {"portal": {"base-url": ["http://a", "http://b"]}}}}
    assert resolve_property(maps, "boxwood.portal.base-url", None) is None


def test_resolve_property_multi_profile_disagreement_is_gap():
    maps = {
        None: {"boxwood": {"portal": {"base-url": "http://dwp-b-portal-service:8080"}}},
        "prod": {"boxwood": {"portal": {"base-url": "http://prod-portal:9000"}}},
    }
    assert resolve_property(maps, "boxwood.portal.base-url", None) is None


def test_resolve_property_key_only_binding_no_sibling_leak():
    maps = {
        None: {
            "boxwood": {"portal": {"base-url": "http://dwp-b-portal-service:8080"}},
            "spring": {"datasource": {"password": "topsecret"}},
        }
    }
    resolved = resolve_property(maps, "boxwood.portal.base-url", None)
    assert resolved == "http://dwp-b-portal-service:8080"
    assert "topsecret" not in (resolved or "")


# --- safe_load: no full-loader object construction / code execution -----------

def test_load_profile_maps_refuses_python_tag(tmp_path):
    # A full loader (yaml.load) would CONSTRUCT this object (run os.system); safe_load
    # refuses the tag and the file degrades to an empty map (a gap). Proof: the side
    # effect never happens and the key stays unresolved.
    resources = tmp_path / "src" / "main" / "resources"
    resources.mkdir(parents=True)
    pwned = tmp_path / "pwned"
    (resources / "application.yaml").write_text(
        "boxwood:\n"
        "  portal:\n"
        f"    base-url: !!python/object/apply:os.system ['touch {pwned}']\n"
    )
    maps = load_profile_maps(resources)
    assert resolve_property(maps, "boxwood.portal.base-url", None) is None
    assert not pwned.exists()


# --- java.py integration: grounded ApiCall binds the target host --------------

def _write(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)


def _apicall_qns(ir):
    return {n.qualified_name for n in ir.nodes_of(API_CALL)}


_CALLER = (
    "package kr.co.x;\n"
    "import org.springframework.web.client.RestTemplate;\n"
    "class PortalApiClient {\n"
    '  @Value("${boxwood.portal.base-url:http://localhost:8080}")\n'
    "  private String baseUrl;\n"
    "  private final RestTemplate restTemplate;\n"
    '  Object load() { return restTemplate.getForObject(baseUrl + "/portal/api/user", C.class); }\n'
    "}\n"
)


def test_grounded_base_url_binds_target_host(tmp_path):
    _write(
        tmp_path,
        {
            "src/main/java/kr/co/x/PortalApiClient.java": _CALLER,
            "src/main/resources/application.yaml": (
                "boxwood:\n  portal:\n    base-url: http://dwp-b-portal-service:8080\n"
            ),
        },
    )
    ir = extract_java(tmp_path, PROV, repo_name="engine")
    qns = _apicall_qns(ir)
    # The ApiCall carries the resolved target host, binding the SERVICE...
    assert "apicall:GET http://dwp-b-portal-service:8080/portal/api/user" in qns
    # ...and NOT the bare path, which would coincidentally match another service (ac-5).
    assert "apicall:GET /portal/api/user" not in qns


def test_value_literal_default_grounds_when_yaml_absent(tmp_path):
    # No application.yaml key -> the @Value literal default is the resolvable fallback.
    _write(tmp_path, {"src/main/java/kr/co/x/PortalApiClient.java": _CALLER})
    ir = extract_java(tmp_path, PROV, repo_name="engine")
    assert "apicall:GET http://localhost:8080/portal/api/user" in _apicall_qns(ir)


def test_unresolvable_config_is_gap_not_binding(tmp_path):
    # base-url is env-only in yaml AND the @Value carries no literal default -> the
    # concatenated call must emit NOTHING (honest gap), never a guessed/false ApiCall.
    caller = (
        "package kr.co.x;\n"
        "import org.springframework.web.client.RestTemplate;\n"
        "class PortalApiClient {\n"
        '  @Value("${boxwood.portal.base-url}")\n'
        "  private String baseUrl;\n"
        "  private final RestTemplate restTemplate;\n"
        '  Object load() { return restTemplate.getForObject(baseUrl + "/portal/api/user", C.class); }\n'
        "}\n"
    )
    _write(
        tmp_path,
        {
            "src/main/java/kr/co/x/PortalApiClient.java": caller,
            "src/main/resources/application.yaml": (
                "boxwood:\n  portal:\n    base-url: ${BOXWOOD_PORTAL_BASE_URL}\n"
            ),
        },
    )
    ir = extract_java(tmp_path, PROV, repo_name="engine")
    assert _apicall_qns(ir) == set()


def test_resolve_base_url_end_to_end(tmp_path):
    resources = tmp_path / "src" / "main" / "resources"
    resources.mkdir(parents=True)
    (resources / "application.yaml").write_text(
        "boxwood:\n  portal:\n    base-url: http://dwp-b-portal-service:8080\n"
    )
    src = tmp_path / "src" / "main" / "java" / "kr" / "co" / "x" / "PortalApiClient.java"
    src.parent.mkdir(parents=True)
    src.write_text("// caller\n")
    assert (
        resolve_base_url(src, "${boxwood.portal.base-url:http://localhost:8080}")
        == "http://dwp-b-portal-service:8080"
    )
