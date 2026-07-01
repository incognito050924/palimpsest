"""Load externally-generated semantic summaries into the KG (provider-free).

palimpsest calls NO LLM. A summary is produced by an external generator and
handed to :func:`load_summaries` for grounded, idempotent load. This inferred
layer is kept SEPARATE from the deterministic structural layer by two markers:

  * node label ``Summary`` (never a code label), and
  * ``edge_kind = "inferred"`` on every ``SUMMARIZES`` edge (deterministic edges
    are ``"deterministic"``) — the schema-enforced no-laundering separation.

Honesty (summary-atomic). Each claim must be grounded in >=1 ``source_ref`` that
resolves to a real graph node. A summary that fails any check is REJECTED with a
reason — never silently dropped, never partially loaded. Rejecting one summary
does not stop the rest from loading. The deterministic ``_REL_MERGE`` writer
(``MATCH..MATCH..MERGE``) is deliberately NOT reused: an unresolved endpoint
would make it write nothing *silently*, which would violate this contract; here
every endpoint is resolved up front and mismatches are rejected.

Idempotence. The Summary id is deterministic and namespace-isolated
(``summary:<hash>`` — a code ``qualified_name`` can never collide), and every
write is MERGE-on-id, so re-loading the same payload changes nothing.

Freshness. ``code_bound_at`` binds to the resolved TARGET node's ``committed_at``
(freshness follows the code, not the generator's wall-clock). ``created_at`` is
the external generation time carried on the payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_INFERRED, Summary


@dataclass(frozen=True)
class Rejection:
    """A refused summary and why — surfaced, never swallowed."""

    summary_id: str
    reason: str


@dataclass(frozen=True)
class SummaryLoadResult:
    """Outcome of a load batch: counts + the explicit rejection reasons."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[Rejection, ...] = ()


def summary_id(target_id: str, generator: str, model: str, source_commit: str) -> str:
    """Deterministic, namespace-isolated Summary id.

    The ``summary:`` prefix guarantees it can never equal a code
    ``qualified_name`` (a Java FQN / repo path), so a Summary never shadows a
    code node even on a hash coincidence; the hash makes re-load idempotent.
    """
    raw = "\x00".join([target_id, generator, model, source_commit])
    return "summary:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Label ("Summary") and rel type ("SUMMARIZES") are closed constants baked into
# the query text; every piece of summary DATA (id, texts, refs, provenance) rides
# in as ``$params`` so adversarial claim text is inert.
_SUMMARY_MERGE = """
MERGE (s:Summary {id: $id})
SET s.target_id     = $target_id,
    s.claims        = $claims,
    s.generator     = $generator,
    s.model         = $model,
    s.source_commit = $source_commit,
    s.created_at    = $created_at,
    s.code_bound_at = $code_bound_at,
    s.confidence    = $confidence,
    s.semantic_verdict = $semantic_verdict,
    s.prompt        = $prompt
"""

# Endpoints are pre-resolved above (unresolved -> the whole summary is rejected,
# never a silent MATCH..MATCH..MERGE no-op), so every MERGE here materialises.
_SUMMARIZES_MERGE = """
MATCH (s:Summary {id: $id})
UNWIND $targets AS tid
MATCH (t {id: tid})
MERGE (s)-[r:SUMMARIZES]->(t)
SET r.edge_kind     = $edge_kind,
    r.source_commit = $source_commit,
    r.created_at    = $created_at,
    r.code_bound_at = $code_bound_at,
    r.generator     = $generator,
    r.model         = $model,
    r.confidence    = $confidence
"""


def _structural_reject_reason(s: Summary):
    """A reason the summary is malformed on its face, or None if it is well-formed
    (grounding of refs is checked separately, against the live graph)."""
    if not (s.generator and s.generator.strip()):
        return "missing generator"
    if not (s.model and s.model.strip()):
        return "missing model"
    if not s.claims:
        return "0-claim summary"
    for i, claim in enumerate(s.claims):
        if not claim.source_refs:
            return f"claim {i} has no source ref"
    return None


def _endpoints(s: Summary) -> set[str]:
    """Every node id that must resolve: the target plus every claim's refs."""
    refs = {ref for claim in s.claims for ref in claim.source_refs}
    refs.add(s.target_id)
    return refs


def _resolve(session, ids: set[str]) -> set[str]:
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


def _write(session, sid: str, s: Summary, endpoints: set[str], code_bound_at) -> None:
    claims = [json.dumps(c.to_dict(), ensure_ascii=False) for c in s.claims]
    # Neo4j properties are primitives/arrays, not maps, so the external judge's
    # verdict is stored as a JSON string (like claims); recall parses it back.
    semantic_verdict = (
        json.dumps(s.semantic_verdict, ensure_ascii=False)
        if s.semantic_verdict is not None
        else None
    )
    session.run(
        _SUMMARY_MERGE,
        id=sid,
        target_id=s.target_id,
        claims=claims,
        generator=s.generator,
        model=s.model,
        source_commit=s.source_commit,
        created_at=s.created_at,
        code_bound_at=code_bound_at,
        confidence=s.confidence,
        semantic_verdict=semantic_verdict,
        prompt=s.prompt,
    )
    session.run(
        _SUMMARIZES_MERGE,
        id=sid,
        targets=sorted(endpoints),
        edge_kind=EDGE_KIND_INFERRED,
        source_commit=s.source_commit,
        created_at=s.created_at,
        code_bound_at=code_bound_at,
        generator=s.generator,
        model=s.model,
        confidence=s.confidence,
    )


def load_summaries(driver, summaries) -> SummaryLoadResult:
    """Load externally-generated summaries into the inferred KG layer.

    Each summary is validated then, if grounded, MERGEd as a ``Summary`` node with
    ``SUMMARIZES`` edges (``edge_kind='inferred'``) to its resolved target/refs.
    A summary that is malformed or whose refs do not all resolve is REJECTED with
    a reason (summary-atomic — none of its claims load); the rest still load.
    Returns intended/loaded/rejected counts plus the rejection reasons.
    """
    summaries = list(summaries)
    rejections: list[Rejection] = []
    loaded = 0

    with driver.session() as session:
        for s in summaries:
            sid = summary_id(s.target_id, s.generator, s.model, s.source_commit)

            reason = _structural_reject_reason(s)
            if reason is not None:
                rejections.append(Rejection(sid, reason))
                continue

            endpoints = _endpoints(s)
            unresolved = sorted(endpoints - _resolve(session, endpoints))
            if unresolved:
                rejections.append(
                    Rejection(sid, f"unresolved refs: {unresolved}")
                )
                continue

            _write(session, sid, s, endpoints, _committed_at(session, s.target_id))
            loaded += 1

    return SummaryLoadResult(
        intended=len(summaries),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
