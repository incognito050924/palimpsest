"""Cross-tier CALLS_API recall channels (wi_260713c7t, ac-6): read-only, grounded.

Two SEPARATE, read-only entry points over the inferred ``CALLS_API`` edge that mirror the
routing channels' assembly (``_result`` / ``_item`` / ``_resolve``) so both return the SAME
``{items, sources, summaries, risks, decisions, relations, gaps, confidence,
expand_handle}`` shape as the rest of recall. No LLM — pure graph traversal.

  * :func:`recall_endpoint_callers` — given an Endpoint, the front-end ApiCall(s) that
    CALLS_API it (who calls this endpoint).
  * :func:`recall_call_endpoints` — given an ApiCall, the Endpoint(s) it CALLS_API (what
    endpoint this call hits).

Honesty invariant (ac-6), ALWAYS emitted in ``gaps``. CALLS_API is a STATIC LOWER BOUND on
cross-tier wiring, in several enumerated ways, so an ABSENCE is never a "this endpoint is
unused" verdict. The disclosure NAMES every cause (see
``_CROSS_TIER_STATIC_LOWER_BOUND_GAP``) — the two incumbent plus the four this cross-tier
widening introduces:

  * a dynamic-URL call (a bare variable / a runtime-concatenated URL with no static
    segment) emits NO ApiCall node at all (``ir.api_call_qualified_name`` returns None) —
    so it can carry no CALLS_API edge;
  * an ApiCall whose canonical route matches no Endpoint carries no edge either
    (unmatched, e.g. an external / not-yet-extracted service);
  * an in-house HTTP wrapper skip — a call via a project-local wrapper is a recognized
    gap by design (ADR-20260713), not "no call";
  * an unresolved ``@Value`` base-url — a JVM S2S caller whose config base-url could not
    be resolved has no grounded target;
  * an unparsed dev-proxy — a vite/svelte proxy rewrite that is a JS function or an
    env-only target could not be statically evaluated;
  * dataflow not recovered — a JVM caller whose URL is assembled / multi-hop (beyond
    one-hop param->uri) emits NO ApiCall.

So an Endpoint with zero incoming CALLS_API is "no statically-linked caller found", NEVER
"endpoint unused"; completeness is not claimed. Each edge carries its confidence +
``matched_route`` grounding on the item's ``link`` so a low-confidence (templated / wildcard
/ multi-candidate) link is never read as a certain one.

All values are parameterized (``$param``) — no id is string-interpolated into Cypher.
"""

from __future__ import annotations

from palimpsest.ir import CALLS_API
from palimpsest.recall.graphrag import _item, _result

# ALWAYS emitted (ac-6 honesty): the static-lower-bound disclosure. Absence of a CALLS_API
# edge is an honest gap, never an "endpoint unused" verdict. The enumeration NAMES every
# incompleteness cause (the two incumbent + the four this widening introduces) so a consumer
# never reads an empty result as completeness — the #18 exhaustive-enumeration precedent.
_CROSS_TIER_STATIC_LOWER_BOUND_GAP = (
    "CALLS_API is a STATIC lower bound on cross-tier wiring; the ABSENCE of a CALLS_API edge "
    "NEVER means 'this endpoint is unused' or 'this call hits nothing', it means 'no "
    "statically-linked peer was found'. A link can be legitimately missing for ANY of these "
    "causes: (1) a dynamic-URL call (a bare variable / a runtime-concatenated URL with no "
    "static segment) emits NO ApiCall node; (2) an ApiCall whose canonical route matches no "
    "Endpoint carries no edge; (3) an in-house HTTP wrapper skip — a call routed through a "
    "project-local wrapper (io.incognito.rest.client, createApiClient) is a recognized gap "
    "by design (ADR-20260713), not 'no call'; (4) an unresolved @Value base-url — a JVM "
    "service-to-service caller whose config base-url could not be resolved (env-only / "
    "ambiguous profile / compose-alias -> module) has no grounded target; (5) an unparsed "
    "dev-proxy — a vite/svelte proxy whose rewrite is a JS function or an env-only target "
    "could not be statically evaluated, so the f/e route is not resolved; (6) dataflow "
    "not recovered — a JVM caller whose URL is assembled / multi-hop (beyond one-hop "
    "param->uri) emits NO ApiCall. Completeness is not claimed"
)

# ApiCall(s) that CALLS_API a given Endpoint (endpoint -> callers). The edge grounding
# (confidence / matched_route / candidate_count) is projected alongside the ApiCall node so
# a link's certainty travels with it. id-ordered before LIMIT for rebuild-determinism.
_ENDPOINT_CALLERS = """
MATCH (a:ApiCall)-[r:CALLS_API]->(ep:Endpoint {id: $id})
RETURN DISTINCT a.id AS id, labels(a) AS labels, a.name AS name,
       a.qualified_name AS qualified_name,
       a.path AS path, a.start_line AS start_line, a.end_line AS end_line,
       a.source_commit AS source_commit, a.committed_at AS committed_at,
       r.confidence AS confidence, r.matched_route AS matched_route,
       r.candidate_count AS candidate_count
ORDER BY id
LIMIT $lim
"""

# Endpoint(s) a given ApiCall CALLS_API (call -> endpoints). The inverse traversal.
_CALL_ENDPOINTS = """
MATCH (a:ApiCall {id: $id})-[r:CALLS_API]->(ep:Endpoint)
RETURN DISTINCT ep.id AS id, labels(ep) AS labels, ep.name AS name,
       ep.qualified_name AS qualified_name,
       ep.path AS path, ep.start_line AS start_line, ep.end_line AS end_line,
       ep.source_commit AS source_commit, ep.committed_at AS committed_at,
       r.confidence AS confidence, r.matched_route AS matched_route,
       r.candidate_count AS candidate_count
ORDER BY id
LIMIT $lim
"""


def _link_item(rec) -> dict:
    """A grounded item carrying the CALLS_API edge's own grounding on ``link`` — the
    confidence + matched_route + candidate_count that qualify the cross-tier link."""
    it = _item(rec, CALLS_API, 1)
    it["link"] = {
        "confidence": rec.get("confidence"),
        "matched_route": rec.get("matched_route"),
        "candidate_count": rec.get("candidate_count"),
    }
    return it


def recall_endpoint_callers(driver, endpoint_id, limit=25):
    """Recall the front-end ApiCall(s) that CALLS_API an Endpoint (ac-6).

    A SEPARATE, read-only entry point over the inferred CALLS_API edge: every ApiCall
    pointing at ``endpoint_id``, id-ordered and BOUNDED by ``limit``, each carrying the
    edge's confidence + ``matched_route`` grounding on ``link``. Same standard
    ``{items, sources, ...}`` shape as the sibling channels.

    Soundness (ac-6): the result ALWAYS carries the static-lower-bound disclosure in
    ``gaps`` — zero callers means "no statically-linked caller found" (a dynamic-URL call
    emits no ApiCall; an unmatched call carries no edge), NEVER "endpoint unused".
    Combinatorial only.
    """
    gaps = [_CROSS_TIER_STATIC_LOWER_BOUND_GAP]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_ENDPOINT_CALLERS, id=endpoint_id, lim=limit)]
    items = [_link_item(rec) for rec in rows]
    return _result(items, gaps, None, [])


def recall_call_endpoints(driver, call_id, limit=25):
    """Recall the Endpoint(s) a front-end ApiCall CALLS_API (the inverse of
    :func:`recall_endpoint_callers`).

    From the ApiCall ``call_id``, every Endpoint it links to, id-ordered and BOUNDED by
    ``limit``, each carrying the edge's confidence + ``matched_route`` grounding on
    ``link``. The static-lower-bound disclosure is ALWAYS present in ``gaps`` — an ApiCall
    with no linked Endpoint hit no STATICALLY-KNOWN endpoint (an external / not-yet-extracted
    service is invisible), never "this call hits nothing". Combinatorial only.
    """
    gaps = [_CROSS_TIER_STATIC_LOWER_BOUND_GAP]
    with driver.session() as session:
        rows = [r.data() for r in session.run(_CALL_ENDPOINTS, id=call_id, lim=limit)]
    items = [_link_item(rec) for rec in rows]
    return _result(items, gaps, None, [])
