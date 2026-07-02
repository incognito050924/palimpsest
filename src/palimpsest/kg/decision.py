"""Load externally-generated DesignDecisions into the KG (provider-free).

palimpsest calls NO LLM. A DesignDecision is a decision ("this is a design
decision") produced by an external generator and handed to
:func:`load_design_decisions` for grounded, idempotent load. Like the Risk
inferred layer, it is kept SEPARATE from the deterministic structural layer by two
markers:

  * node label ``DesignDecision`` (never a code label), and
  * ``edge_kind = "inferred"`` on every DECIDES / SUPERSEDES / ADDRESSES_RISK edge
    (deterministic edges are ``"deterministic"``) — the schema-enforced
    no-laundering separation.

Grounding (entity-atomic). A DesignDecision must have >=1 ``DECIDES`` target, and
EVERY edge target (DECIDES / SUPERSEDES / ADDRESSES_RISK) must resolve to a real
graph node OF THE RIGHT LABEL: a ``SUPERSEDES`` target must be a ``DesignDecision``
and an ``ADDRESSES_RISK`` target must be a ``Risk`` (a ``DECIDES`` target is any
existing node — typically code, or another decision). A decision with zero DECIDES,
or any unresolved / wrong-label target, is REJECTED with a reason — never a floating
decision node, never partially loaded. Rejecting one decision does not stop the
rest. The deterministic ``_REL_MERGE`` writer is deliberately NOT reused: it stamps
``edge_kind='deterministic'`` (which would launder this inferred layer) and its
``MATCH..MATCH..MERGE`` would silently no-op on an unresolved endpoint; here every
target is resolved up front and mismatches are rejected.

Idempotence. The decision id is deterministic and namespace-isolated
(``decision:<hash>`` — a code ``qualified_name`` can never collide), and every
write is MERGE-on-id, so re-loading the same payload changes nothing.

Freshness. ``code_bound_at`` binds to a decided CODE node's ``committed_at``
(freshness follows the code, not the generator's wall-clock). ``created_at`` is the
external generation time carried on the payload.

Scope note (this slice): edge targets resolve against the LIVE graph only.
Same-batch entity resolution (a SUPERSEDES to a DesignDecision loaded earlier in
THE SAME batch call) is out of scope here — deferred refinement.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from palimpsest.ir import (
    ADDRESSES_RISK,
    DECIDES,
    DESIGN_DECISION,
    EDGE_KIND_INFERRED,
    RISK,
    SUPERSEDES,
    DesignDecision,
)

# namespace prefix of a DesignDecision id — a DECIDES target with this prefix is
# another decision (not a code node), so it carries no code ``committed_at``.
_DECISION_NS = "decision:"


@dataclass(frozen=True)
class DesignDecisionRejection:
    """A refused DesignDecision and why — surfaced, never swallowed."""

    decision_id: str
    reason: str


@dataclass(frozen=True)
class DesignDecisionLoadResult:
    """Outcome of a load batch: counts + the explicit rejection reasons."""

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[DesignDecisionRejection, ...] = ()


def decision_id(title: str, source_commit: str, targets) -> str:
    """Deterministic, namespace-isolated DesignDecision id.

    Built over a rebuild-stable key — the NUL-joined normalized ``title``,
    ``source_commit`` and the SORTED union of all edge targets (DECIDES +
    SUPERSEDES + ADDRESSES_RISK) — so the id is invariant under target order
    (mirrors :func:`palimpsest.kg.risk.risk_id`). The ``decision:`` prefix
    guarantees it can never equal a code ``qualified_name``, so a decision never
    shadows a code node; the hash makes re-load idempotent.
    """
    raw = "\x00".join([title.strip(), source_commit, *sorted(targets)])
    return "decision:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Label ("DesignDecision") and rel types are closed constants baked into the query
# text; every piece of decision DATA (id, title, targets, provenance) rides in as
# ``$params`` so adversarial title text is inert.
_DECISION_MERGE = """
MERGE (d:DesignDecision {id: $id})
ON CREATE SET d.valid_from = $created_at, d.valid_to = null
SET d.title           = $title,
    d.decides         = $decides,
    d.supersedes      = $supersedes,
    d.addresses_risks = $addresses_risks,
    d.generator       = $generator,
    d.model           = $model,
    d.source_commit   = $source_commit,
    d.created_at      = $created_at,
    d.code_bound_at   = $code_bound_at,
    d.confidence      = $confidence,
    d.semantic_verdict = $semantic_verdict
"""

# Endpoints are pre-resolved above (unresolved / wrong-label -> the whole decision
# is rejected, never a silent MATCH..MATCH..MERGE no-op), so every MERGE here
# materialises. ``{rel}`` is a closed constant (DECIDES / SUPERSEDES /
# ADDRESSES_RISK), never data — mirrors kg/ingest.py's ``_REL_MERGE`` templating.
_EDGE_MERGE = """
MATCH (d:DesignDecision {{id: $id}})
UNWIND $targets AS tid
MATCH (t {{id: tid}})
MERGE (d)-[e:`{rel}`]->(t)
SET e.edge_kind     = $edge_kind,
    e.source_commit = $source_commit,
    e.created_at    = $created_at,
    e.code_bound_at = $code_bound_at,
    e.generator     = $generator,
    e.model         = $model,
    e.confidence    = $confidence
"""


# Decision-lineage freshness (2nd axis): loading a decision that SUPERSEDES a prior
# one INVALIDATES the prior — set its ``valid_to`` to the superseder's ``created_at``
# (when it stopped being current). The prior node is PRESERVED, never deleted (전이력
# 보존); "live" is derived at read time as ``valid_to IS NULL``. Targets are already
# resolved + label-checked (DesignDecision) up front, so every MATCH here materialises.
_SUPERSEDE_INVALIDATE = """
UNWIND $targets AS tid
MATCH (t:DesignDecision {id: tid})
SET t.valid_to = $valid_to
"""


def _structural_reject_reason(d: DesignDecision):
    """A reason the decision is malformed on its face, or None if well-formed
    (grounding of targets is checked separately, against the live graph)."""
    if not (d.generator and d.generator.strip()):
        return "missing generator"
    if not (d.model and d.model.strip()):
        return "missing model"
    if not d.decides:
        return "0-DECIDES decision (no grounding)"
    return None


def _resolve(session, ids, label=None) -> set:
    """The subset of ``ids`` that resolve to a real graph node. When ``label`` is
    given (a closed constant, never data), only nodes carrying THAT label count —
    so a wrong-label target is treated as unresolved (invalid)."""
    if not ids:
        return set()
    match = f"MATCH (n:`{label}` {{id: id}})" if label else "MATCH (n {id: id})"
    rows = session.run(
        f"UNWIND $ids AS id {match} RETURN DISTINCT n.id AS id",
        ids=list(ids),
    )
    return {r["id"] for r in rows}


def _committed_at(session, node_id: str):
    rec = session.run(
        "MATCH (n {id: $id}) RETURN n.committed_at AS committed_at LIMIT 1",
        id=node_id,
    ).single()
    return rec["committed_at"] if rec else None


def _write(session, did, d, decides, supersedes, addresses_risks, code_bound_at):
    # Neo4j properties are primitives/arrays, not maps, so an external judge's
    # verdict is stored as a JSON string (recall parses it back).
    semantic_verdict = (
        json.dumps(d.semantic_verdict, ensure_ascii=False)
        if d.semantic_verdict is not None
        else None
    )
    session.run(
        _DECISION_MERGE,
        id=did,
        title=d.title,
        decides=decides,
        supersedes=supersedes,
        addresses_risks=addresses_risks,
        generator=d.generator,
        model=d.model,
        source_commit=d.source_commit,
        created_at=d.created_at,
        code_bound_at=code_bound_at,
        confidence=d.confidence,
        semantic_verdict=semantic_verdict,
    )
    for rel, targets in (
        (DECIDES, decides),
        (SUPERSEDES, supersedes),
        (ADDRESSES_RISK, addresses_risks),
    ):
        if not targets:
            continue
        session.run(
            _EDGE_MERGE.format(rel=rel),
            id=did,
            targets=targets,
            edge_kind=EDGE_KIND_INFERRED,
            source_commit=d.source_commit,
            created_at=d.created_at,
            code_bound_at=code_bound_at,
            generator=d.generator,
            model=d.model,
            confidence=d.confidence,
        )
    # Decision-lineage freshness: invalidate (not delete) each superseded decision —
    # its currency ends at THIS decision's created_at. Idempotent SET; the superseded
    # node is preserved (전이력 보존), and re-loading it never resets valid_to (that is
    # ON-CREATE-only on the node's own MERGE).
    if supersedes:
        session.run(_SUPERSEDE_INVALIDATE, targets=supersedes, valid_to=d.created_at)


def load_design_decisions(driver, decisions) -> DesignDecisionLoadResult:
    """Load externally-generated DesignDecisions into the inferred KG layer.

    Each decision is validated then, if grounded, MERGEd as a ``DesignDecision``
    node with DECIDES / SUPERSEDES / ADDRESSES_RISK edges (``edge_kind='inferred'``)
    to its resolved targets. A decision that is malformed, has zero DECIDES, or has
    any unresolved / wrong-label target is REJECTED with a reason (entity-atomic —
    nothing written); the rest still load. Returns intended/loaded/rejected counts
    plus the rejection reasons.
    """
    decisions = list(decisions)
    rejections: list[DesignDecisionRejection] = []
    loaded = 0
    with driver.session() as session:
        for d in decisions:
            decides = sorted(set(d.decides))
            supersedes = sorted(set(d.supersedes))
            addresses_risks = sorted(set(d.addresses_risks))
            all_targets = sorted(set(decides) | set(supersedes) | set(addresses_risks))
            did = decision_id(d.title, d.source_commit, all_targets)

            reason = _structural_reject_reason(d)
            if reason is not None:
                rejections.append(DesignDecisionRejection(did, reason))
                continue

            # Grounding, resolved up front (never a silent MATCH..MERGE no-op): any
            # unresolved DECIDES target, or a SUPERSEDES/ADDRESSES_RISK target that
            # is absent OR carries the wrong label, rejects the WHOLE decision
            # (entity-atomic) — no floating node, no partial write.
            unresolved = sorted(
                (set(decides) - _resolve(session, decides))
                | (set(supersedes) - _resolve(session, supersedes, DESIGN_DECISION))
                | (set(addresses_risks) - _resolve(session, addresses_risks, RISK))
            )
            if unresolved:
                rejections.append(
                    DesignDecisionRejection(did, f"unresolved edge targets: {unresolved}")
                )
                continue

            # Freshness anchors on the deterministically-first DECIDES *code*
            # target's committed_at. A ``decision:``-namespaced DECIDES target is
            # another DesignDecision (no code committed_at), so it is skipped.
            # NOTE: like Risk's flags[0] anchor, this binds the node AND every edge
            # to ONE target's commit, not each edge's own — a known simplification
            # (exact for single-code-target decisions). See the ADR change_condition
            # ("multi-target 신선도 재검토").
            code_decides = [t for t in decides if not t.startswith(_DECISION_NS)]
            code_bound_at = (
                _committed_at(session, code_decides[0]) if code_decides else None
            )
            _write(
                session, did, d, decides, supersedes, addresses_risks, code_bound_at
            )
            loaded += 1
    return DesignDecisionLoadResult(
        intended=len(decisions),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
    )
