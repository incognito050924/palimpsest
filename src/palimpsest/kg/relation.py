"""Load externally-generated inferred RELATIONS into the KG (provider-free).

palimpsest calls NO LLM. An inferred relation ("A causally relates to / relates to /
conflicts with B") is asserted by an external generator and handed to
:func:`load_relations` for grounded, idempotent load. Unlike Risk/DesignDecision it
creates NO new node — it is a pure inferred EDGE between two EXISTING nodes. It stays
SEPARATE from the deterministic structural layer by ``edge_kind='inferred'`` on every
edge (the schema-enforced no-laundering separation).

Grounding (entity-atomic). BOTH endpoints must resolve to real graph nodes and the
``rel_type`` must be one of :data:`INFERRED_RELATION_TYPES`. Any unresolved endpoint
or unknown rel_type REJECTS the whole relation with a reason — never a dangling edge,
never partially loaded. Rejecting one relation does not stop the rest. The
deterministic ``_REL_MERGE`` writer is deliberately NOT reused: it stamps
``edge_kind='deterministic'`` (which would launder this inferred layer); here every
endpoint is resolved up front and mismatches are rejected.

Idempotence. The edge is MERGEd on ``(source, rel_type, target)``, so re-loading the
same payload changes nothing.

Freshness. ``code_bound_at`` binds to the SOURCE endpoint's ``committed_at``
(freshness follows the code, not the generator's wall-clock). ``created_at`` is the
external generation time carried on the payload.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_INFERRED, INFERRED_RELATION_TYPES, InferredRelation


@dataclass(frozen=True)
class RelationRejection:
    """A refused relation and why — surfaced, never swallowed."""

    key: str
    reason: str


@dataclass(frozen=True)
class RelationLoadResult:
    """Outcome of a load batch: counts + the explicit rejection reasons."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[RelationRejection, ...] = ()


# ``{rel}`` is a closed constant (validated ∈ INFERRED_RELATION_TYPES), never data —
# mirrors kg/decision.py's ``_EDGE_MERGE`` templating. Endpoints are pre-resolved, so
# this MATCH..MATCH..MERGE always materialises (no silent no-op).
_RELATION_MERGE = """
MATCH (a {{id: $source_id}})
MATCH (b {{id: $target_id}})
MERGE (a)-[e:`{rel}`]->(b)
SET e.edge_kind     = $edge_kind,
    e.source_commit = $source_commit,
    e.created_at    = $created_at,
    e.code_bound_at = $code_bound_at,
    e.generator     = $generator,
    e.model         = $model,
    e.confidence    = $confidence,
    e.semantic_verdict = $semantic_verdict
"""


def _key(r: InferredRelation) -> str:
    return f"{r.source_id}|{r.rel_type}|{r.target_id}"


def _structural_reject_reason(r: InferredRelation):
    """A reason the relation is malformed on its face, or None if well-formed
    (endpoint grounding is checked separately, against the live graph)."""
    if not (r.generator and r.generator.strip()):
        return "missing generator"
    if not (r.model and r.model.strip()):
        return "missing model"
    if r.rel_type not in INFERRED_RELATION_TYPES:
        return f"unknown rel_type '{r.rel_type}' (not in {sorted(INFERRED_RELATION_TYPES)})"
    return None


def _resolve(session, ids) -> set:
    if not ids:
        return set()
    rows = session.run(
        "UNWIND $ids AS id MATCH (n {id: id}) RETURN DISTINCT n.id AS id",
        ids=list(ids),
    )
    return {r["id"] for r in rows}


def _committed_at(session, node_id: str):
    rec = session.run(
        "MATCH (n {id: $id}) RETURN n.committed_at AS committed_at LIMIT 1",
        id=node_id,
    ).single()
    return rec["committed_at"] if rec else None


def _write(session, r: InferredRelation, code_bound_at) -> None:
    # Neo4j properties are primitives/arrays, not maps, so an external judge's
    # verdict is stored as a JSON string (recall parses it back).
    semantic_verdict = (
        json.dumps(r.semantic_verdict, ensure_ascii=False)
        if r.semantic_verdict is not None
        else None
    )
    session.run(
        _RELATION_MERGE.format(rel=r.rel_type),
        source_id=r.source_id,
        target_id=r.target_id,
        edge_kind=EDGE_KIND_INFERRED,
        source_commit=r.source_commit,
        created_at=r.created_at,
        code_bound_at=code_bound_at,
        generator=r.generator,
        model=r.model,
        confidence=r.confidence,
        semantic_verdict=semantic_verdict,
    )


def load_relations(driver, relations) -> RelationLoadResult:
    """Load externally-generated inferred relations into the inferred KG layer.

    Each relation is validated (generator/model/known rel_type) then, if BOTH
    endpoints resolve, MERGEd as an inferred edge (``edge_kind='inferred'``) between
    them. A relation that is malformed, carries an unknown rel_type, or has any
    unresolved endpoint is REJECTED with a reason (entity-atomic — nothing written);
    the rest still load. Returns intended/loaded/rejected counts + rejection reasons.
    """
    relations = list(relations)
    rejections: list[RelationRejection] = []
    loaded = 0
    with driver.session() as session:
        for r in relations:
            reason = _structural_reject_reason(r)
            if reason is not None:
                rejections.append(RelationRejection(_key(r), reason))
                continue

            # Grounding, resolved up front (never a silent MATCH..MERGE no-op): any
            # unresolved endpoint rejects the WHOLE relation (entity-atomic).
            endpoints = {r.source_id, r.target_id}
            unresolved = sorted(endpoints - _resolve(session, endpoints))
            if unresolved:
                rejections.append(
                    RelationRejection(_key(r), f"unresolved endpoints: {unresolved}")
                )
                continue

            # Freshness anchors on the SOURCE endpoint's committed_at (None if the
            # source is an inferred entity with no code committed_at). Per-endpoint
            # freshness refinement is deferred (mirrors Risk's flags[0] anchor).
            _write(session, r, _committed_at(session, r.source_id))
            loaded += 1
    return RelationLoadResult(
        intended=len(relations),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
