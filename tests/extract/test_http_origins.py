"""Scope-local tests for the HTTP-construct registry (``extract.http_origins``).

These pin the ADR-20260713-ontology-framework-constructs-not-project-patterns
boundary at the registry level: JVM *standard* framework/library HTTP constructs
(Spring ``WebClient`` / ``RestTemplate``, OpenFeign ``@FeignClient``) are recognized
when a JVM scanner resolves the callee to their import ORIGIN (the Java FQN), whereas
a project-local in-house wrapper (``io.incognito.rest.client``) is the disclosed gap
and is NEVER recognized — recognizing it would internalize a per-project pattern into
the ontology (host-neutral violation, ADR decision 1).

They also guard the existing JS-family semantics unchanged (regression, ac-7).
"""

from palimpsest.extract.http_origins import (
    HTTP_CONSTRUCTS,
    is_recognized_call,
    recognizes_specifier,
)


# --- JVM standard constructs: recognized via resolved import ORIGIN (FQN) -----
# Each asserts the AC clause "a JVM scanner resolves base -> framework FQN and the
# registry recognizes it the same origin-keyed way it recognizes axios/node-fetch".

WEBCLIENT_FQN = "org.springframework.web.reactive.function.client.WebClient"
RESTTEMPLATE_FQN = "org.springframework.web.client.RestTemplate"
FEIGNCLIENT_FQN = "org.springframework.cloud.openfeign.FeignClient"


def test_spring_webclient_fqn_is_recognized():
    assert is_recognized_call("WebClient", WEBCLIENT_FQN) is True


def test_spring_resttemplate_fqn_is_recognized():
    assert is_recognized_call("RestTemplate", RESTTEMPLATE_FQN) is True


def test_openfeign_feignclient_fqn_is_recognized():
    assert is_recognized_call("FeignClient", FEIGNCLIENT_FQN) is True


def test_jvm_fqns_are_registered_origins():
    # A JVM scanner consumes the registry the same way ecmascript does: the resolved
    # FQN specifier must be a registered package origin.
    for fqn in (WEBCLIENT_FQN, RESTTEMPLATE_FQN, FEIGNCLIENT_FQN):
        assert recognizes_specifier(fqn) is True


# --- In-house wrapper + project-local: the disclosed gap, NEVER recognized ----
# Encodes the ADR honest-gap clause: recognizing io.incognito.rest.client would
# carve a per-project pattern into the ontology.


def test_inhouse_httpclients_wrapper_not_recognized():
    assert is_recognized_call(
        "HttpClients", "io.incognito.rest.client.HttpClients"
    ) is False


def test_inhouse_kotlin_wrapper_not_recognized():
    assert is_recognized_call(
        "KotlinHttpClients", "io.incognito.rest.client.KotlinHttpClients"
    ) is False


def test_project_local_relative_import_not_recognized():
    # JS project wrapper — recognition stops at the wrapper level.
    assert is_recognized_call("api", "./client") is False


# --- Regression: existing JS-family semantics unchanged (ac-7) -----------------


def test_js_fetch_global_still_recognized():
    assert is_recognized_call("fetch", None) is True


def test_js_axios_and_node_fetch_still_recognized():
    assert is_recognized_call("axios", "axios") is True
    assert is_recognized_call("get", "node-fetch") is True


def test_js_bare_unknown_package_not_recognized():
    assert is_recognized_call("thing", "some-random-pkg") is False


def test_js_entries_preserved_in_registry():
    names = {c.name for c in HTTP_CONSTRUCTS}
    assert {"fetch", "axios", "node-fetch"} <= names
