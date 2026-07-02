"""Load externally-generated Risk judgments into the KG (provider-free).

palimpsest calls NO LLM. A Risk is a judgment ("this code is risky") produced by
an external generator and handed to :func:`load_risks` for grounded, idempotent
load. Like the Summary inferred layer, it is kept SEPARATE from the deterministic
structural layer by two markers:

  * node label ``Risk`` (never a code label), and
  * ``edge_kind = "inferred"`` on every ``RISKS`` edge (deterministic edges are
    ``"deterministic"``) — the schema-enforced no-laundering separation.

Grounding (entity-atomic). A Risk must flag >=1 code node id, and every flag must
resolve to a real graph node. A Risk with zero flags, or any unresolved flag, is
REJECTED with a reason — never a floating judgment node, never partially loaded.
Rejecting one Risk does not stop the rest. The deterministic ``_REL_MERGE`` writer
is deliberately NOT reused: it stamps ``edge_kind='deterministic'`` (which would
launder this inferred layer) and its ``MATCH..MATCH..MERGE`` would silently no-op
on an unresolved endpoint; here every flag is resolved up front and mismatches are
rejected.

Idempotence. The Risk id is deterministic and namespace-isolated
(``risk:<hash>`` — a code ``qualified_name`` can never collide), and every write
is MERGE-on-id, so re-loading the same payload changes nothing.

Freshness. ``code_bound_at`` binds to a flagged code node's ``committed_at``
(freshness follows the code, not the generator's wall-clock). ``created_at`` is
the external generation time carried on the payload.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from palimpsest.ir import EDGE_KIND_INFERRED, Risk


@dataclass(frozen=True)
class RiskRejection:
    """A refused Risk and why — surfaced, never swallowed."""

    risk_id: str
    reason: str


@dataclass(frozen=True)
class RiskLoadResult:
    """Outcome of a load batch: counts + the explicit rejection reasons."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[RiskRejection, ...] = ()


def risk_id(title: str, source_commit: str, flags) -> str:
    """Deterministic, namespace-isolated Risk id.

    Built over a rebuild-stable key — the NUL-joined normalized ``title``,
    ``source_commit`` and SORTED ``flags`` — so the id is invariant under flag
    order (mirrors :func:`palimpsest.kg.community.community_id`). The ``risk:``
    prefix guarantees it can never equal a code ``qualified_name``, so a Risk
    never shadows a code node; the hash makes re-load idempotent.
    """
    raw = "\x00".join([title.strip(), source_commit, *sorted(flags)])
    return "risk:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Label ("Risk") and rel type ("RISKS") are closed constants baked into the query
# text; every piece of Risk DATA (id, title, flags, provenance) rides in as
# ``$params`` so adversarial title text is inert.
_RISK_MERGE = """
MERGE (r:Risk {id: $id})
SET r.title         = $title,
    r.flags         = $flags,
    r.generator     = $generator,
    r.model         = $model,
    r.source_commit = $source_commit,
    r.created_at    = $created_at,
    r.code_bound_at = $code_bound_at,
    r.confidence    = $confidence,
    r.semantic_verdict = $semantic_verdict
"""

_RISKS_MERGE = """
MATCH (r:Risk {id: $id})
UNWIND $flags AS fid
MATCH (t {id: fid})
MERGE (r)-[e:RISKS]->(t)
SET e.edge_kind     = $edge_kind,
    e.source_commit = $source_commit,
    e.created_at    = $created_at,
    e.code_bound_at = $code_bound_at,
    e.generator     = $generator,
    e.model         = $model,
    e.confidence    = $confidence
"""


def _structural_reject_reason(risk: Risk):
    """A reason the Risk is malformed on its face, or None if well-formed
    (grounding of flags is checked separately, against the live graph)."""
    if not (risk.generator and risk.generator.strip()):
        return "missing generator"
    if not (risk.model and risk.model.strip()):
        return "missing model"
    if not risk.flags:
        return "0-flag risk (no grounding)"
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


def _write(session, rid: str, risk: Risk, flags, code_bound_at) -> None:
    # Neo4j properties are primitives/arrays, not maps, so an external judge's
    # verdict is stored as a JSON string (recall parses it back).
    semantic_verdict = (
        json.dumps(risk.semantic_verdict, ensure_ascii=False)
        if risk.semantic_verdict is not None
        else None
    )
    session.run(
        _RISK_MERGE,
        id=rid,
        title=risk.title,
        flags=flags,
        generator=risk.generator,
        model=risk.model,
        source_commit=risk.source_commit,
        created_at=risk.created_at,
        code_bound_at=code_bound_at,
        confidence=risk.confidence,
        semantic_verdict=semantic_verdict,
    )
    session.run(
        _RISKS_MERGE,
        id=rid,
        flags=flags,
        edge_kind=EDGE_KIND_INFERRED,
        source_commit=risk.source_commit,
        created_at=risk.created_at,
        code_bound_at=code_bound_at,
        generator=risk.generator,
        model=risk.model,
        confidence=risk.confidence,
    )


def load_risks(driver, risks) -> RiskLoadResult:
    """Load externally-generated Risks into the inferred KG layer."""
    risks = list(risks)
    rejections: list[RiskRejection] = []
    loaded = 0
    with driver.session() as session:
        for risk in risks:
            flags = sorted(risk.flags)
            rid = risk_id(risk.title, risk.source_commit, flags)

            reason = _structural_reject_reason(risk)
            if reason is not None:
                rejections.append(RiskRejection(rid, reason))
                continue

            # Grounding, resolved up front (never a silent MATCH..MERGE no-op): a
            # flag that does not resolve rejects the WHOLE Risk (entity-atomic),
            # so no floating judgment node and no partial write.
            unresolved = sorted(set(flags) - _resolve(session, flags))
            if unresolved:
                rejections.append(
                    RiskRejection(rid, f"unresolved flag targets: {unresolved}")
                )
                continue

            # anchor freshness on the deterministically-first flag's code node.
            # NOTE: this binds the node AND every RISKS edge to flags[0]'s commit
            # (not each flag's own) — a known simplification (mirrors Summary's
            # single-anchor); harmless while single-flag, and no consumer reads
            # per-edge Risk freshness yet (no risk recall channel). See the ADR
            # change_condition ("multi-flag 신선도 재검토").
            _write(session, rid, risk, flags, _committed_at(session, flags[0]))
            loaded += 1
    return RiskLoadResult(
        intended=len(risks),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
