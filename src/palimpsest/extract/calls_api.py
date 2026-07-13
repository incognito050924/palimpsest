"""Cross-tier CALLS_API matcher (wi_260713c7t, Decisions 2b/3/4).

The pure, provider-free bridge between the two HTTP-API planes: a front-end
``ApiCall`` (``apicall:{METHOD} {path}``) and a back-end / f-e ``Endpoint``
(``spring:GET /api/orders`` or the prefix-less SvelteKit ``GET /api/orders``) are
peers of the SAME route seen from opposite tiers. :func:`match_calls` reduces both
sides to their canonical match key (:func:`palimpsest.ir.canonical_match_key` — the
ONE shared route-identity fn, so match logic and route identity can never drift) and
emits a :class:`CallEndpointMatch` for every ApiCall x Endpoint whose route matches.

This module is a PURE function over route identities — it never touches Neo4j and
never writes to ``ir.edges``. Its output is consumed by ``kg/calls_api.py``, the
dedicated inferred loader that MERGEs each match as an ``edge_kind='inferred'``
CALLS_API edge (Frozen Invariant 3: CALLS_API is NEVER written through the generic
deterministic edge writer). Keeping the matcher provider-free and graph-free is what
lets the SAME algorithm run at ingest-time (over the graph) and in a scope-local
unit test (over extracted IR nodes) with no DB.

Confidence heuristic (Decision 4), a lower-is-more-ambiguous ladder:
  * 1.0 — exact method + fully-literal path, a SINGLE matching Endpoint.
  * 0.7 — the matched path carries >=1 ``{}`` / ``{**}`` placeholder (templated), so
          several concrete URLs share it.
  * 0.4 — the method matched only via a wildcard (Spring ``*`` / SvelteKit
          ``fallback``) — the weakest signal.
  * multi-candidate — when one ApiCall matches N>1 Endpoints, every edge carries
          ``candidate_count=N`` and confidence is reduced (the single-candidate 1.0 is
          unavailable): the min() of the applicable tiers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from palimpsest.ir import ANY_METHOD, canonical_match_key, route_host
from palimpsest.extract.provenance import (
    CONFIG,
    LITERAL,
    is_derived,
    transform_confidence_cap,
)

# Method tokens the matcher treats as WILDCARDS (Decision 2e): a method-less Spring
# @RequestMapping reduces to the ``ANY_METHOD`` sentinel ("*"), and a SvelteKit
# ``fallback`` export answers any verb. Either side being a wildcard matches any
# concrete verb on the other side — at reduced confidence.
_SVELTEKIT_FALLBACK = "fallback"
_WILDCARD_METHODS = frozenset({ANY_METHOD, _SVELTEKIT_FALLBACK})


@dataclass(frozen=True)
class RouteEnd:
    """The minimal identity + grounding the matcher needs from ONE node.

    Deliberately decoupled from both :class:`palimpsest.ir.Node` and the Neo4j row
    shape so the same pure matcher serves the extract-time IR (test) and the
    ingest-time graph query (loader). ``committed_at`` / ``source_commit`` are the
    CALL side's git grounding; the Endpoint side needs only ``id`` + ``qualified_name``.
    """

    id: str
    qualified_name: str
    committed_at: Optional[str] = None
    source_commit: Optional[str] = None
    # How this call's ROUTE was resolved (``extract.provenance`` transform). Defaults to
    # LITERAL (a direct call-site literal). A one-hop dataflow-recovered call carries
    # DATAFLOW here; a host-grounded (config-resolved) call is detected at match time from
    # its host prefix, so it need not set this. Kept off ``id`` — it is scoring metadata,
    # never route identity (wi_260713iah part 2).
    transform: str = LITERAL


@dataclass(frozen=True)
class CallEndpointMatch:
    """One inferred CALLS_API assertion (an ApiCall matched to an Endpoint).

    ``matched_route`` is the shared canonical route ("{method} {canonical_path}") — the
    grounding that explains WHY the two tiers were linked. ``candidate_count`` is how
    many Endpoints this one ApiCall matched (N; the ambiguity signal). ``code_bound_at``
    / ``source_commit`` follow the CALL's git grounding (freshness follows the code).
    """

    source_id: str          # ApiCall id
    target_id: str          # Endpoint id
    matched_route: str      # "{method} {canonical_path}" grounding
    confidence: float
    candidate_count: int
    code_bound_at: Optional[str] = None
    source_commit: Optional[str] = None
    # Transform-provenance of the RESOLUTION (``extract.provenance``): how the route was
    # derived — LITERAL direct-match, DATAFLOW one-hop, PROXY rewrite, or CONFIG host
    # grounding. A derived link is auditably distinct from a literal 1.0 match.
    transform: str = LITERAL
    # The absolute target host bound into a host-grounded (config) call
    # (``http://svc:8080``), else None. Confirms WHICH service the call targets — a
    # confidence/binding signal, never part of the match key (wi_260713iah part 3).
    target_host: Optional[str] = None


def _is_wildcard(method: str) -> bool:
    return method in _WILDCARD_METHODS


def _methods_match(a_method: str, e_method: str) -> bool:
    """A call verb matches an endpoint verb when they are equal OR either side is a
    wildcard (Spring ``*`` / SvelteKit ``fallback`` answers any verb)."""
    return a_method == e_method or _is_wildcard(a_method) or _is_wildcard(e_method)


def _is_templated(canonical_path: str) -> bool:
    """Whether a match-level path carries any ``{}`` / ``{**}`` placeholder — i.e. several
    concrete URLs collapse onto it, so a match on it is weaker than a literal one."""
    return any(seg in ("{}", "{**}") for seg in canonical_path.split("/") if seg)


def _confidence(
    templated: bool, wildcard_method: bool, candidate_count: int, transform: str
) -> float:
    """The Decision-4 confidence ladder, as a min() of the applicable ambiguity caps.

    Starts at 1.0 (exact method + literal path, single candidate) and is capped DOWN by
    each ambiguity present: a templated path (0.7), a wildcard-method match (0.4), and
    multi-candidacy (0.7 — the single-candidate 1.0 is unavailable once >1 Endpoint
    matches). A DERIVED ``transform`` (wi_260713iah part 2: one-hop dataflow, dev-proxy
    rewrite, or config host-grounding) adds its own cap — never the literal 1.0, and the
    weakest tier when the derived resolution is not provably unique (>1 candidate). The
    lowest applicable cap wins.
    """
    c = 1.0
    if templated:
        c = min(c, 0.7)
    if wildcard_method:
        c = min(c, 0.4)
    if candidate_count > 1:
        c = min(c, 0.7)
    c = min(c, transform_confidence_cap(transform, unique=candidate_count == 1))
    return c


def _matched_method(a_method: str, e_method: str) -> str:
    """The concrete verb to record on ``matched_route``: prefer the non-wildcard side (a
    call's default GET vs. a method-less endpoint's ``*``); both wildcard -> ANY_METHOD."""
    if not _is_wildcard(a_method):
        return a_method
    if not _is_wildcard(e_method):
        return e_method
    return ANY_METHOD


def _call_transform(c: RouteEnd, host: Optional[str]) -> str:
    """The resolution transform for one call: an explicit non-literal transform on the
    RouteEnd (e.g. DATAFLOW carried from extraction) wins; else a host-grounded call
    (mechanism B stamped a target host) is CONFIG-derived; else a plain LITERAL."""
    if c.transform != LITERAL:
        return c.transform
    if host is not None:
        return CONFIG
    return LITERAL


def match_calls(
    calls: Iterable[RouteEnd], endpoints: Iterable[RouteEnd], proxy=None
) -> list[CallEndpointMatch]:
    """Match every ApiCall to every route-equal Endpoint, tier-agnostically (Decision 4).

    Both sides reduce to ``canonical_match_key`` (method, canonical_path). A grounded
    ApiCall's stamped target host is stripped from the key (part 3) — matching is on
    method+PATH — and surfaced separately as ``target_host`` for target binding + a
    discounted (derived) confidence (part 2). ``proxy`` (mechanism A) is applied to the
    CALL side's key so a front-end ``/api`` call reaches the back-end route (an unevaluable
    proxy prefix reduces to :data:`UNRESOLVABLE_ROUTE` and links to nothing). For each
    ApiCall the full candidate set is found first, so ``candidate_count`` and the
    multi-candidate reduction are exact. Input order is preserved (id-ordered rows) so the
    output is rebuild-stable.
    """
    ep_keyed = [(e, canonical_match_key(e.qualified_name)) for e in endpoints]
    matches: list[CallEndpointMatch] = []
    for c in calls:
        a_method, a_path = canonical_match_key(c.qualified_name, proxy=proxy)
        host = route_host(c.qualified_name)
        transform = _call_transform(c, host)
        candidates = [
            (e, e_method, e_path)
            for (e, (e_method, e_path)) in ep_keyed
            if e_path == a_path and _methods_match(a_method, e_method)
        ]
        n = len(candidates)
        for (e, e_method, e_path) in candidates:
            wildcard = _is_wildcard(a_method) or _is_wildcard(e_method)
            confidence = _confidence(_is_templated(e_path), wildcard, n, transform)
            method = _matched_method(a_method, e_method)
            matches.append(
                CallEndpointMatch(
                    source_id=c.id,
                    target_id=e.id,
                    matched_route=f"{method} {e_path}",
                    confidence=confidence,
                    candidate_count=n,
                    code_bound_at=c.committed_at,
                    source_commit=c.source_commit,
                    transform=transform,
                    target_host=host,
                )
            )
    return matches
