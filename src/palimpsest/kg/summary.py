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

from palimpsest.ir import EDGE_KIND_INFERRED, EMBEDDING_DIM, SUMMARY, Summary

# The Summary vector index (single, closed name): cosine over EMBEDDING_DIM.
VECTOR_INDEX_NAME = "summary_embedding_cosine"


@dataclass(frozen=True)
class Rejection:
    """A refused summary and why — surfaced, never swallowed."""

    summary_id: str
    reason: str


@dataclass(frozen=True)
class SummaryLoadResult:
    """Outcome of a load batch: counts + the explicit rejection reasons.

    ``embedded`` is how many loaded summaries carried a (valid) embedding;
    ``indexed`` is how many of them are actually queryable through the vector
    index right now (0 if the index is absent/not-online) — the two make a
    silently-unindexed vector visible instead of silently unsearchable.
    """

    intended: int
    loaded: int
    rejected: int
    rejections: tuple[Rejection, ...] = ()
    embedded: int = 0
    indexed: int = 0


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
    s.prompt        = $prompt,
    s.embedding     = $embedding,
    s.embedding_model = $embedding_model,
    s.embedding_dim = $embedding_dim
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
    # Embedding is optional (back-compat), but if present it must be well-formed:
    # the dimension check uses the SAME EMBEDDING_DIM as the vector index DDL, so
    # a wrong-dim vector is rejected here rather than silently skipped by Neo4j.
    if s.embedding is not None:
        if len(s.embedding) != EMBEDDING_DIM:
            return (
                f"embedding dim mismatch: expected {EMBEDDING_DIM}, "
                f"got {len(s.embedding)}"
            )
        if not (s.embedding_model and s.embedding_model.strip()):
            return "embedding without embedding_model"
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


# A CommunityReport is a Summary whose target is a Community node — recognised by
# the ``community:`` id namespace (mirrors kg.community.community_id). Such a
# report carries the extra membership-grounding rule below.
_COMMUNITY_NS = "community:"


def _in_community(session, community_id: str, refs: set[str]) -> set[str]:
    """Of ``refs``, the ids that belong to the target Community — a member Class,
    or a node contained by a member Class (e.g. a Method of a member).

    Enforces membership-grounding for a CommunityReport: a report ABOUT a
    community must ground its claims in that community's members, not arbitrary
    code. Refs not returned here are non-members and reject the whole report.
    """
    if not refs:
        return set()
    rows = session.run(
        """
        UNWIND $refs AS rid
        MATCH (n {id: rid})
        OPTIONAL MATCH (n)-[:MEMBER_OF]->(dc:Community {id: $cid})
        OPTIONAL MATCH (owner:Class)-[:CONTAINS]->(n)
        OPTIONAL MATCH (owner)-[:MEMBER_OF]->(oc:Community {id: $cid})
        WITH rid, dc, oc
        WHERE dc IS NOT NULL OR oc IS NOT NULL
        RETURN DISTINCT rid AS id
        """,
        refs=list(refs),
        cid=community_id,
    )
    return {r["id"] for r in rows}


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
        embedding=s.embedding,
        embedding_model=s.embedding_model,
        embedding_dim=s.embedding_dim,
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


# EMBEDDING_DIM and 'cosine' are trusted internal constants (like the baked
# Summary label), never payload data — safe to inline into the DDL text.
_CREATE_VECTOR_INDEX = (
    f"CREATE VECTOR INDEX `{VECTOR_INDEX_NAME}` IF NOT EXISTS "
    f"FOR (s:`{SUMMARY}`) ON (s.embedding) "
    f"OPTIONS {{indexConfig: {{"
    f"`vector.dimensions`: {EMBEDDING_DIM}, "
    f"`vector.similarity_function`: 'cosine'}}}}"
)


def create_vector_index(driver) -> None:
    """Provision the Summary embedding VECTOR INDEX (cosine, EMBEDDING_DIM).

    Idempotent (``IF NOT EXISTS``), mirroring ``create_constraints``. Neo4j
    populates a vector index asynchronously, so this AWAITs it reaching ONLINE:
    an immediate query on a still-POPULATING index returns partial/empty results.
    """
    with driver.session() as session:
        session.run(_CREATE_VECTOR_INDEX)
        # Block until every index (this one included) finishes populating.
        session.run("CALL db.awaitIndexes($timeout)", timeout=300)


def _index_online(session, name: str) -> bool:
    rec = session.run(
        "SHOW INDEXES YIELD name, state WHERE name = $n RETURN state",
        n=name,
    ).single()
    return rec is not None and rec["state"] == "ONLINE"


def _indexed_count(session) -> int:
    """How many embedded Summary nodes are queryable through the vector index now.

    0 if the index is absent/not-online. Otherwise a k>=total queryNodes probe
    returns every indexed node (queryNodes yields up to k regardless of score),
    so counting the distinct hits gives the actually-indexed total — catching a
    vector Neo4j silently failed to index (silent-unsearchable visibility)."""
    if not _index_online(session, VECTOR_INDEX_NAME):
        return 0
    session.run("CALL db.awaitIndexes($timeout)", timeout=300)
    total = session.run(
        "MATCH (s:Summary) WHERE s.embedding IS NOT NULL RETURN count(s) AS c"
    ).single()["c"]
    if total == 0:
        return 0
    rows = session.run(
        "CALL db.index.vector.queryNodes($name, $k, $probe) "
        "YIELD node RETURN count(DISTINCT node) AS c",
        name=VECTOR_INDEX_NAME,
        k=total,
        probe=[1.0] * EMBEDDING_DIM,
    ).single()
    return rows["c"] if rows else 0


def _established_embedding_model(session):
    """The embedding_model already bound to any Summary in the graph, or None.

    A cosine vector index is single-model — comparing vectors from different
    models is meaningless even at equal dimension — so the first model loaded
    establishes the index's model and later loads must match it."""
    rec = session.run(
        "MATCH (s:Summary) WHERE s.embedding_model IS NOT NULL "
        "RETURN s.embedding_model AS m LIMIT 1"
    ).single()
    return rec["m"] if rec else None


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
    embedded = 0

    with driver.session() as session:
        # The model already established for the index (from prior loads); the
        # first embedded summary in THIS batch establishes it if none exists yet.
        established_model = _established_embedding_model(session)
        for s in summaries:
            sid = summary_id(s.target_id, s.generator, s.model, s.source_commit)

            reason = _structural_reject_reason(s)
            if reason is not None:
                rejections.append(Rejection(sid, reason))
                continue

            # Single-embedding-model-per-index: a well-formed embedding whose
            # model differs from the established one is rejected (rest still load).
            if s.embedding is not None:
                if established_model is None:
                    established_model = s.embedding_model
                elif s.embedding_model != established_model:
                    rejections.append(
                        Rejection(
                            sid,
                            f"embedding model mismatch: index established with "
                            f"'{established_model}', got '{s.embedding_model}'",
                        )
                    )
                    continue

            endpoints = _endpoints(s)
            unresolved = sorted(endpoints - _resolve(session, endpoints))
            if unresolved:
                rejections.append(
                    Rejection(sid, f"unresolved refs: {unresolved}")
                )
                continue

            # Membership-grounding: a CommunityReport (target is a community: node)
            # must ground every claim ref in a member of that community.
            if s.target_id.startswith(_COMMUNITY_NS):
                claim_refs = {ref for claim in s.claims for ref in claim.source_refs}
                non_member = sorted(
                    claim_refs - _in_community(session, s.target_id, claim_refs)
                )
                if non_member:
                    rejections.append(
                        Rejection(sid, f"non-member refs (not in target community): {non_member}")
                    )
                    continue

            _write(session, sid, s, endpoints, _committed_at(session, s.target_id))
            loaded += 1
            if s.embedding is not None:
                embedded += 1

        indexed = _indexed_count(session)

    return SummaryLoadResult(
        intended=len(summaries),
        loaded=loaded,
        rejected=len(rejections),
        rejections=tuple(rejections),
        embedded=embedded,
        indexed=indexed,
    )
