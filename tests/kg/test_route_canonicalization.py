"""TDD for the canonical HTTP route functions (Decisions 2 & 3).

WHY this file exists (background for approver + consumer):

Cross-tier CALLS_API matching hinges on ONE thing: a front-end route ("GET
/api/users/[id]"), a back-end route ("spring:GET /api/users/{userId}") and an
ApiCall route ("apicall:GET /api/users/{}") must all reduce to the SAME match key
despite different native param syntaxes. These pure functions in ir.py are that
reduction. The tests pin the TWO deliberately-separate normalization levels:

  * IDENTITY level (``normalize_endpoint_path``) PRESERVES param names — erasing
    them here would collapse sibling routes into one node (Frozen Invariant 2).
  * MATCH level (``canonical_route_path`` / ``canonical_match_key``) erases param
    names to "{}" / "{**}" and MUST be idempotent (a route already at match level
    is a fixed point).
  * ``api_call_qualified_name`` builds an ApiCall identity from a call-site URL,
    returning None for a non-templatable dynamic URL — the ac-6 disclosed gap
    (never a false "endpoint unused").
"""

from palimpsest.ir import (
    api_call_qualified_name,
    canonical_match_key,
    canonical_route_path,
    normalize_endpoint_path,
)


# --- IDENTITY level: normalize_endpoint_path PRESERVES param names (2a) --------

def test_normalize_preserves_param_names():
    # Native param syntax is part of route IDENTITY — preserved verbatim.
    assert normalize_endpoint_path("/api/users/[id]") == "/api/users/[id]"
    assert normalize_endpoint_path("/api/users/{id:[0-9]+}") == "/api/users/{id:[0-9]+}"
    assert normalize_endpoint_path("/api/posts/[id=int]") == "/api/posts/[id=int]"


def test_normalize_single_leading_collapse_and_trailing():
    # single leading /, collapse //, strip trailing / (except root), empty -> /.
    assert normalize_endpoint_path("api//orders/") == "/api/orders"
    assert normalize_endpoint_path("/api//orders") == "/api/orders"
    assert normalize_endpoint_path("/") == "/"
    assert normalize_endpoint_path("") == "/"


# --- MATCH level: canonical_route_path erases to {}/{**}, idempotent (2b) ------

def test_canonical_route_path_erases_single_vars():
    assert canonical_route_path("/api/users/[id]") == "/api/users/{}"
    assert canonical_route_path("/api/users/{userId}") == "/api/users/{}"
    assert canonical_route_path("/api/users/{id:[0-9]+}") == "/api/users/{}"
    assert canonical_route_path("/api/users/:id") == "/api/users/{}"
    assert canonical_route_path("/api/users/${id}") == "/api/users/{}"
    assert canonical_route_path("/api/opt/[[slug]]") == "/api/opt/{}"


def test_canonical_route_path_erases_catch_all():
    assert canonical_route_path("/files/[...rest]") == "/files/{**}"
    assert canonical_route_path("/static/**") == "/static/{**}"
    assert canonical_route_path("/api/{*path}") == "/api/{**}"


def test_canonical_route_path_literal_is_case_sensitive():
    assert canonical_route_path("/API/Users") == "/API/Users"


def test_canonical_route_path_is_idempotent():
    for raw in (
        "/api/users/[id]",
        "/files/[...rest]",
        "/api/users/{id:[0-9]+}",
        "/static/**",
        "/API/Users",
    ):
        once = canonical_route_path(raw)
        assert canonical_route_path(once) == once, raw


# --- MATCH key: cross-tier reduction to (method, canonical_path) (2b) ----------

def test_match_key_reduces_all_three_tiers_to_one_key():
    expected = ("GET", "/api/users/{}")
    assert canonical_match_key("GET /api/users/[id]") == expected            # f/e
    assert canonical_match_key("spring:GET /api/users/{userId}") == expected  # b/e
    assert canonical_match_key("apicall:GET /api/users/{}") == expected       # call


def test_match_key_only_strips_prefix_before_first_space():
    # A ':' inside a path/regex param (AFTER the first space) is NOT a namespace
    # prefix — it must be left intact for canonicalization.
    assert canonical_match_key("GET /api/users/{id:[0-9]+}") == ("GET", "/api/users/{}")


# --- ApiCall identity from a call-site URL, incl. the ac-6 dynamic gap (3) -----

def test_api_call_qualified_name_templatable():
    # A template literal with a static prefix IS templatable: ${...} -> {}.
    assert api_call_qualified_name("GET", "`/api/orders/${id}`") == "apicall:GET /api/orders/{}"
    # A plain string literal with no interpolation carries through.
    assert api_call_qualified_name("GET", '"/api/orders"') == "apicall:GET /api/orders"


def test_api_call_qualified_name_dynamic_returns_none():
    # ac-6 gap: a bare variable or a runtime concatenation is non-templatable ->
    # the ApiCall is NOT emitted (None), never a false endpoint.
    assert api_call_qualified_name("GET", "url") is None
    assert api_call_qualified_name("GET", "'/api/' + id") is None
    # A literal that is ONLY an interpolation is a dressed-up bare variable -> None.
    assert api_call_qualified_name("GET", "`${url}`") is None
