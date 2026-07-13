"""ac-7 (+ authorization-model coverage finding) — the Endpoint discriminator's
tier-scoped unguarded recall (wi_260713c7t, node n-svelte-endpoint-tier).

WHY these tests exist (background for approver + consumer):

wi_260713c7t makes ``label=Endpoint`` shared across tiers. The SvelteKit f/e
colocation plane keeps its id BYTE-UNCHANGED — ``f"{METHOD} {url}"`` with NO prefix
(Frozen Invariant 1) — while Spring b/e endpoints carry a ``spring:`` namespace token
on ``qualified_name`` (Decision 1). ``recall_unguarded_endpoints`` is the f/e
authorization keystone (ac-4): a GLOBAL ``MATCH (ep:Endpoint)`` with no incoming
``GUARDS``. Because Spring b/e endpoints share the ``Endpoint`` label but have NO
GUARDS producer (Spring Security is out of static-extraction scope), an un-scoped
query would flood the f/e result with every b/e endpoint — silently mislabeling a
Spring-guarded endpoint as "unguarded" with the WRONG authorization mechanism.

These tests pin, on a REAL ingested graph, the three ac-7 clauses. The two Endpoint
nodes are hand-built via the ``Node`` dataclass: the Spring extractor is a sibling
node not yet built, so its ``spring:`` id scheme is reproduced directly (isolating
this scope from unbuilt siblings), never imported.

  (a) test_svelte_and_spring_endpoint_are_distinct_nodes — a SvelteKit
      ("GET /api/x") and a Spring ("spring:GET /api/x") Endpoint sharing the SAME
      route are DISTINCT graph nodes; the per-(label,id) MERGE never collapses them.
  (b) test_unguarded_recall_excludes_spring_tier — recall_unguarded_endpoints
      returns ONLY the prefix-less SvelteKit endpoint; the ``spring:`` b/e endpoint
      with no GUARDS does NOT pollute the f/e keystone result.
  (c) test_gap_discloses_spring_security_blindspot — the always-on gap discloses the
      b/e blindspot (Spring Security / @PreAuthorize / the filter chain is invisible
      to static extraction) so an excluded Spring endpoint is an HONEST omission,
      never a wrong-mechanism false verdict.

ac-7 "recall 회귀 0" (the SvelteKit-only unguarded RESULT is unchanged) is validated
by the existing tests/recall/test_routing_queries.py + test_recall_routing_regression
(SvelteKit-only fixtures), run in the same scope.

Live-Neo4j (Docker): reuses the session container from tests/recall/conftest.py; where
Docker is unavailable these ERROR on the fixture (distinct from a collection failure).
"""

import pytest

from palimpsest.ir import IR, Node, Provenance, ENDPOINT
from palimpsest.kg import ingest
from palimpsest.recall.routing import (
    recall_unguarded_endpoints,
    _UNGUARDED_STATIC_LOWER_BOUND_GAP,
)

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

# Same route on two tiers. The SvelteKit id is byte-exact ``f"{METHOD} {url}"`` (no
# prefix — Frozen Invariant 1); the Spring id carries the ``spring:`` namespace token.
# Neither has a GUARDS producer here (a SvelteKit endpoint's guard is a server
# Hook/Layout; Spring Security is out of static scope) — so both are "no
# statically-detected guard", and the un-scoped query would return BOTH.
SVELTE_EP = "GET /api/tierscope"
SPRING_EP = "spring:GET /api/tierscope"


def _endpoint_node(qualified_name: str) -> Node:
    return Node(
        kind=ENDPOINT,
        qualified_name=qualified_name,
        name="GET",
        provenance=PROV,
        path="src/routes/api/tierscope/+server.ts",
        start_line=1,
        end_line=3,
    )


@pytest.fixture(scope="module")
def tier_db(recall_db):
    """Two same-route Endpoints — one SvelteKit (prefix-less), one Spring (``spring:``)
    — ingested ADDITIVELY onto the shared session graph, NEITHER with an incoming
    GUARDS edge. Torn down after the module so the global graph other recall modules
    share is left pristine (mirrors test_recall_routing_regression's cleanup)."""
    ingest(
        recall_db,
        IR(nodes=[_endpoint_node(SVELTE_EP), _endpoint_node(SPRING_EP)]),
    )
    yield recall_db
    with recall_db.session() as s:
        s.run(
            "MATCH (ep:Endpoint) WHERE ep.qualified_name IN $qns DETACH DELETE ep",
            qns=[SVELTE_EP, SPRING_EP],
        )


_ENDPOINTS_BY_QN = (
    "MATCH (ep:Endpoint) WHERE ep.qualified_name IN $qns "
    "RETURN ep.qualified_name AS qn, ep.id AS id ORDER BY qn"
)


def test_svelte_and_spring_endpoint_are_distinct_nodes(tier_db):
    """(a) The per-(label,id) MERGE keeps the two same-route endpoints DISTINCT: the
    prefix-less SvelteKit id and the ``spring:``-namespaced id never collapse into one
    node — the discriminator is what lets both tiers share the ``Endpoint`` label."""
    with tier_db.session() as session:
        rows = [
            r.data()
            for r in session.run(_ENDPOINTS_BY_QN, qns=[SVELTE_EP, SPRING_EP])
        ]
    qns = {r["qn"] for r in rows}
    ids = {r["id"] for r in rows}
    assert qns == {SVELTE_EP, SPRING_EP}   # both endpoints present
    assert len(ids) == 2                   # two DISTINCT node ids — no merge


def test_unguarded_recall_excludes_spring_tier(tier_db):
    """(b) recall_unguarded_endpoints is tier-scoped to the SvelteKit colocation plane:
    the prefix-less endpoint surfaces (no GUARDS), while the ``spring:`` b/e endpoint —
    whose authorization is out of static scope — does NOT pollute the f/e keystone."""
    out = recall_unguarded_endpoints(tier_db)
    qns = {it["qualified_name"] for it in out["items"]}
    assert SVELTE_EP in qns        # SvelteKit endpoint, no GUARDS -> flagged
    assert SPRING_EP not in qns    # Spring b/e endpoint EXCLUDED (no wrong-mechanism verdict)


def test_gap_discloses_spring_security_blindspot(tier_db):
    """(c) Excluding the b/e tier is disclosed honestly: the always-on gap names that
    Spring Security / @PreAuthorize / the filter chain is invisible to static
    extraction, so a Spring-guarded endpoint is never silently treated as an unguarded
    SvelteKit one — an honest omission, not a wrong-mechanism false 'unprotected'."""
    out = recall_unguarded_endpoints(tier_db)
    gap_text = " ".join(out["gaps"])
    assert "@PreAuthorize" in gap_text
    assert "Spring" in gap_text
    # the disclosure rides the same always-on lower-bound constant recall emits.
    assert _UNGUARDED_STATIC_LOWER_BOUND_GAP in out["gaps"]
