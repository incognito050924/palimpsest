"""Load the inferred cross-tier CALLS_API layer into the KG (wi_260713c7t, Decision 4).

palimpsest calls NO LLM. A CALLS_API edge is not an external payload either — it is
COMPUTED, deterministically, from the deterministic layer already in the graph: every
front-end ``ApiCall`` and every ``Endpoint`` is queried out, reduced to its canonical
route by the pure ``extract/calls_api`` matcher, and each ApiCall x Endpoint route match
is MERGEd as an ``edge_kind='inferred'`` edge between the two EXISTING nodes. No new node
is created — this is a pure inferred EDGE, the cross-tier peer of the SUMMARIZES / RISKS /
DECIDES precedent.

Dedicated loader, NOT the generic writer (Frozen Invariant 3). CALLS_API is deliberately
absent from ``kg.ingest.REL_TYPES`` (the ingest fail-closed guard REJECTS a CALLS_API in
``ir.edges``), so this module is the ONLY producer of the edge. The generic ``_REL_MERGE``
writer is not reused: it stamps ``edge_kind='deterministic'`` (which would launder this
inferred link); here every edge is written ``edge_kind='inferred'`` with its confidence +
route grounding.

Layering note. This imports the matcher from ``palimpsest.extract.calls_api`` — the first
kg->extract import in the tree. The shared route-IDENTITY fns still live in ``ir.py`` (the
``branch_scoped_id`` precedent, imported by both layers); only the pure MATCH ALGORITHM is
sourced here, single-definition, so match logic is never duplicated (and never drifts)
between the extract and kg planes.

Idempotence. Each edge is MERGEd on the ``(ApiCall)-[:CALLS_API]->(Endpoint)`` pattern, so
re-running the loader over an unchanged graph changes nothing (a re-buildable projection).

Freshness. ``code_bound_at`` binds to the ApiCall (source) node's ``committed_at`` —
freshness follows the CALLING code, not a wall-clock. ``source_commit`` is the ApiCall's
commit.
"""

from __future__ import annotations

from dataclasses import dataclass

from palimpsest.extract.calls_api import RouteEnd, match_calls
from palimpsest.ir import EDGE_KIND_INFERRED

# The matcher's provenance stamp (Decision 4): palimpsest is the generator, and the
# model is the versioned deterministic route-matcher — never an LLM.
_GENERATOR = "palimpsest"
_MODEL = "cross-tier-route-matcher/v1"

# All ApiCall / Endpoint identities + grounding, id-ordered for a deterministic,
# rebuild-stable match order. ``committed_at`` / ``source_commit`` ground the CALL side.
_APICALLS = """
MATCH (a:ApiCall)
RETURN a.id AS id, a.qualified_name AS qualified_name,
       a.committed_at AS committed_at, a.source_commit AS source_commit
ORDER BY id
"""

_ENDPOINTS = """
MATCH (e:Endpoint)
RETURN e.id AS id, e.qualified_name AS qualified_name,
       e.committed_at AS committed_at, e.source_commit AS source_commit
ORDER BY id
"""

# Both endpoints are pre-resolved (they came out of the two queries above), so this
# MATCH..MATCH..MERGE always materialises. edge_kind='inferred' is the schema-enforced
# no-laundering separation from the deterministic structural layer.
_CALLS_API_MERGE = """
MATCH (a:ApiCall {id: $src})
MATCH (e:Endpoint {id: $dst})
MERGE (a)-[r:CALLS_API]->(e)
SET r.edge_kind       = $edge_kind,
    r.confidence      = $confidence,
    r.matched_route   = $matched_route,
    r.candidate_count = $candidate_count,
    r.generator       = $generator,
    r.model           = $model,
    r.source_commit   = $source_commit,
    r.code_bound_at   = $code_bound_at
"""


@dataclass(frozen=True)
class CallsApiLoadResult:
    """Outcome of a CALLS_API load: how many nodes were considered, how many edges merged."""

    api_calls: int
    endpoints: int
    loaded: int


def _route_end(row) -> RouteEnd:
    return RouteEnd(
        id=row["id"],
        qualified_name=row["qualified_name"],
        committed_at=row.get("committed_at"),
        source_commit=row.get("source_commit"),
    )


def _write(session, m) -> None:
    session.run(
        _CALLS_API_MERGE,
        src=m.source_id,
        dst=m.target_id,
        edge_kind=EDGE_KIND_INFERRED,
        confidence=m.confidence,
        matched_route=m.matched_route,
        candidate_count=m.candidate_count,
        generator=_GENERATOR,
        model=_MODEL,
        source_commit=m.source_commit,
        code_bound_at=m.code_bound_at,
    )


def load_calls_api(driver) -> CallsApiLoadResult:
    """Compute + load the inferred cross-tier CALLS_API layer (Decision 4).

    Queries every ``ApiCall`` and every ``Endpoint`` out of the graph, runs the pure
    :func:`palimpsest.extract.calls_api.match_calls` matcher over their canonical routes,
    and MERGEs one ``edge_kind='inferred'`` CALLS_API edge per route match — each carrying
    its confidence, ``matched_route`` grounding, and ``candidate_count``. Entity-atomic
    (both endpoints already resolved) and idempotent (MERGE on the edge pattern). Returns
    the ApiCall / Endpoint counts and the number of edges merged.
    """
    with driver.session() as session:
        calls = [_route_end(r.data()) for r in session.run(_APICALLS)]
        endpoints = [_route_end(r.data()) for r in session.run(_ENDPOINTS)]
        matches = match_calls(calls, endpoints)
        for m in matches:
            _write(session, m)
    return CallsApiLoadResult(
        api_calls=len(calls), endpoints=len(endpoints), loaded=len(matches)
    )
