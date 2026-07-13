"""Batch-ingest the extraction IR into Neo4j (raw Cypher, UNWIND + MERGE).

Deterministic, minimal ontology (see the approved design):

  Node labels : Repo, Package, File, Class, Method, Episode(commit)
  Rel types   : CONTAINS, CALLS, DEPENDS_ON, IMPORTS

Identity is the IR ``qualified_name`` (``Node.id``): one uniqueness CONSTRAINT
per label on ``id``, and every write is a MERGE-on-id, so re-ingest is
idempotent (git is the source of truth; the Neo4j projection is rebuildable).

Every node/edge is stamped with git provenance (source_commit / author /
committed_at) and freshness (``code_bound_at`` — v1 single-commit = the ingested
commit's committed_at). Every edge additionally carries
``edge_kind = "deterministic"``: v1 has only structural/deterministic edges, and
the property is present so a later inferred layer can never be confused with
these (schema-enforced no-laundering separation).
"""

from __future__ import annotations

from collections import defaultdict

from palimpsest.ir import (
    IR,
    REPO,
    PACKAGE,
    FILE,
    CLASS,
    METHOD,
    FUNCTION,
    VARIABLE,
    COMMUNITY,
    ROUTE,
    ENDPOINT,
    LAYOUT,
    HOOK,
    API_CALL,
    CONTAINS,
    IMPORTS,
    CALLS,
    DEPENDS_ON,
    MEMBER_OF,
    MODIFIES,
    REALIZES,
    HANDLES,
    LOADS,
    GUARDS,
    SUMMARY,
    RISK,
    DESIGN_DECISION,
    EDGE_KIND_DETERMINISTIC,
)

# The ontology, closed and explicit (no dynamic labels from data). ``Summary``,
# ``Risk`` and ``DesignDecision`` are inferred-layer labels; they carry no
# deterministic IR node, but are listed here so ``create_constraints`` provisions
# their uniqueness CONSTRAINT. ``Community`` is a deterministic node materialized in
# the IR by ``augment_communities``. Their inferred edges (SUMMARIZES / RISKS /
# DECIDES / SUPERSEDES / ADDRESSES_RISK) are deliberately ABSENT from ``REL_TYPES``
# — the generic writer must never stamp them ``edge_kind='deterministic'``; their
# dedicated loaders write them as inferred.
# A provider-free structural label recording partial-capture honesty for an
# N-way branch capture (written by ``reconcile``, not an IR node kind). Listed
# here only so ``create_constraints`` provisions its ``id`` uniqueness CONSTRAINT.
CAPTURE_MANIFEST = "CaptureManifest"

NODE_LABELS = [
    REPO, PACKAGE, FILE, CLASS, METHOD, FUNCTION, VARIABLE, "Episode", SUMMARY,
    COMMUNITY, RISK, DESIGN_DECISION, CAPTURE_MANIFEST,
    ROUTE, ENDPOINT, LAYOUT, HOOK, API_CALL,
]
# MODIFIES is a deterministic rel type, but it is written by a DEDICATED loader
# (``ingest_modifies``), never the generic ``_REL_MERGE`` path: its src is a bare
# Episode (a commit SHA) that lives OUTSIDE ``ir.nodes``, so ``ingest``'s
# ``id_to_label`` map has no entry for it and the generic path would silently drop
# every MODIFIES edge. Listed here for the ontology registry only.
REL_TYPES = [
    CONTAINS, IMPORTS, CALLS, DEPENDS_ON, MEMBER_OF, MODIFIES,
    REALIZES, HANDLES, LOADS, GUARDS,
]

# A MERGE-on-id per label; property SET is uniform (unused props resolve to
# null, which Neo4j drops — Repo/Package simply carry no path/line grounding).
_NODE_MERGE = """
UNWIND $rows AS row
MERGE (n:`{label}` {{id: row.id}})
SET n.name          = row.name,
    n.qualified_name = row.qualified_name,
    n.branch        = row.branch,
    n.path          = row.path,
    n.start_line    = row.start_line,
    n.end_line      = row.end_line,
    n.is_test       = row.is_test,
    n.server_only   = row.server_only,
    n.role          = row.role,
    n.source_commit = row.source_commit,
    n.author        = row.author,
    n.committed_at  = row.committed_at,
    n.code_bound_at = row.code_bound_at
"""

# Endpoints are MATCHed (not merged) BY LABEL: an edge whose target is
# unresolved/external (e.g. IMPORTS java.util.Map — honest for a source-only
# parser) has no typed IR node, so it is dropped in ``ingest`` before the query
# rather than materialised as an untyped phantom node. The MATCH carries the
# endpoint's label so it uses the per-label id uniqueness index (a labelless
# ``MATCH ({id: ...})`` cannot — Neo4j 5 has no labelless property index — and
# plans as an AllNodesScan, making backfill superlinear as the graph grows).
_REL_MERGE = """
UNWIND $rows AS row
MATCH (a:`{src_label}` {{id: row.src}})
MATCH (b:`{dst_label}` {{id: row.dst}})
MERGE (a)-[r:`{rel}`]->(b)
SET r.edge_kind     = $edge_kind,
    r.source_commit = row.source_commit,
    r.author        = row.author,
    r.committed_at  = row.committed_at,
    r.code_bound_at = row.committed_at
"""

_EPISODE_MERGE = """
UNWIND $rows AS row
MERGE (e:Episode {id: row.id})
SET e.name          = row.id,
    e.qualified_name = row.id,
    e.source_commit = row.id,
    e.author        = row.author,
    e.committed_at  = row.committed_at,
    e.code_bound_at = row.committed_at
"""

# Episode -[:MODIFIES]-> File, written by the DEDICATED loader below. BOTH
# endpoints are MATCHed (never merged): the Episode is written by the commit's
# own ingest, and the File is a HEAD-projection node — a changed path with no File
# node (e.g. deleted and never re-added) is silently skipped rather than
# materialised as a phantom File (ac-2: File keeps its HEAD-MERGE invariant). The
# edge MERGE is idempotent, so re-ingest / re-backfill converge with no
# duplicates. ``count(r)`` reports how many edges actually landed (rows whose File
# did not resolve produce no row).
_MODIFIES_MERGE = """
UNWIND $rows AS row
MATCH (e:Episode {id: row.episode_id})
MATCH (f:File {id: row.file_id})
MERGE (e)-[r:MODIFIES]->(f)
SET r.edge_kind     = $edge_kind,
    r.source_commit = row.episode_id,
    r.committed_at  = row.committed_at,
    r.code_bound_at = row.committed_at
RETURN count(r) AS n
"""


def ingest_modifies(driver, rows) -> int:
    """Write Episode -[:MODIFIES]-> File edges via the dedicated loader.

    ``rows`` is a list of ``{episode_id, file_id, committed_at}``. Returns the
    number of edges that actually landed (a row whose File id has no HEAD node is
    skipped, never a phantom File — ac-2). Idempotent (edge MERGE), so re-running
    backfill converges. Deterministic and provider-free (no LLM anywhere).
    """
    if not rows:
        return 0

    def _write(tx):
        rec = tx.run(
            _MODIFIES_MERGE, rows=list(rows), edge_kind=EDGE_KIND_DETERMINISTIC
        ).single()
        return rec["n"] if rec else 0

    with driver.session() as session:
        return session.execute_write(_write)


# Branch-plane GC, both keyed on the ``branch`` node property (INGEST contract).
#
# (2a) scoped-rebuild: wipe a named branch's whole plane, then re-project it
# (delete-then-project) so shrink/rebase/tip-move leave no stale nodes. Run ONCE
# at the start of a branch's backfill (like create_constraints), never per-commit
# — a per-commit wipe would erase the branch's own earlier commits.
_WIPE_BRANCH_PLANE = "MATCH (n {branch:$branch}) DETACH DELETE n"

# (2b) reaper: drop every named-branch plane whose branch is not git-present. The
# ``branch IS NOT NULL`` guard means the bare-id (unspecified) plane is NEVER
# reaped (ac-6) — an empty ``$live`` still spares the bare plane.
_REAP_DEAD_BRANCHES = (
    "MATCH (n) WHERE n.branch IS NOT NULL AND NOT n.branch IN $live "
    "DETACH DELETE n"
)


def wipe_branch_plane(driver, branch: str) -> None:
    """Delete a named branch's whole plane before re-projecting it (2a).

    ``branch`` must be a real branch name — never ``None``. Wiping ``None`` would
    match the bare-id plane, which is MERGE-accumulate and must not be reaped.
    """
    if branch is None:
        raise ValueError("wipe_branch_plane requires a branch name, not None")
    with driver.session() as session:
        session.run(_WIPE_BRANCH_PLANE, branch=branch)


def reap_dead_branches(driver, live) -> None:
    """Drop every named-branch plane whose branch is not in ``live`` (2b).

    ``live`` = the git-present tracked branch names. The bare-id plane is spared
    by the ``branch IS NOT NULL`` guard.
    """
    with driver.session() as session:
        session.run(_REAP_DEAD_BRANCHES, live=list(live))


def create_constraints(driver) -> None:
    """One uniqueness CONSTRAINT per node label on the deterministic ``id``."""
    with driver.session() as session:
        for label in NODE_LABELS:
            session.run(
                f"CREATE CONSTRAINT `{label.lower()}_id_unique` IF NOT EXISTS "
                f"FOR (n:`{label}`) REQUIRE n.id IS UNIQUE"
            )


def _node_row(node) -> dict:
    p = node.provenance
    return {
        "id": node.id,
        "name": node.name,
        "qualified_name": node.qualified_name,
        # Branch namespace (the GC discriminator); null for the bare-id plane.
        "branch": node.branch,
        "path": node.path,
        "start_line": node.start_line,
        "end_line": node.end_line,
        # test-impact marker (ADR-20260706 §결정6); null for non-marked labels
        # (Repo/Package/Community/... + non-Java) -> Neo4j drops the property.
        "is_test": node.is_test,
        # SvelteKit routing server-only marker; null for non-routing nodes ->
        # Neo4j drops the property (additive, never a phantom-false).
        "server_only": node.server_only,
        # Spring DI/stereotype role (Decision 6); null for non-Class / unmarked
        # nodes -> Neo4j drops the property (additive, mirrors server_only).
        "role": node.role,
        "source_commit": p.source_commit,
        "author": p.author,
        "committed_at": p.committed_at,
        # freshness — v1 single-commit: bound at the ingested commit's time.
        "code_bound_at": p.committed_at,
    }


def _edge_row(edge) -> dict:
    p = edge.provenance
    return {
        "src": edge.src,
        "dst": edge.dst,
        "source_commit": p.source_commit,
        "author": p.author,
        "committed_at": p.committed_at,
    }


def _episode_rows(ir: IR) -> list[dict]:
    seen: dict[str, dict] = {}
    for n in ir.nodes:
        p = n.provenance
        seen.setdefault(
            p.source_commit,
            {"id": p.source_commit, "author": p.author,
             "committed_at": p.committed_at},
        )
    return list(seen.values())


def ingest(driver, ir: IR) -> None:
    """Idempotently ingest ``ir`` into Neo4j via ``driver``.

    One write transaction: Episode(s), then nodes MERGEd by label, then edges
    MERGEd by rel-type (endpoints already written in-tx are visible to MATCH).
    """
    nodes_by_label = {label: [] for label in NODE_LABELS}
    for n in ir.nodes:
        nodes_by_label[n.kind].append(_node_row(n))

    # Resolve each endpoint's label from THIS IR so the relation MERGE can MATCH
    # by label (indexed). An edge materialises iff BOTH endpoints are real IR
    # nodes; an unresolved endpoint (external target with no typed node) is
    # skipped here — exactly the drop the old labelless MATCH produced. Grouping
    # is keyed by (rel_type, src_label, dst_label) so each group runs one indexed
    # query.
    id_to_label = {n.id: n.kind for n in ir.nodes}
    edges_by_group: dict = defaultdict(list)
    for e in ir.edges:
        src_label = id_to_label.get(e.src)
        dst_label = id_to_label.get(e.dst)
        if src_label is None or dst_label is None:
            continue
        # Fail closed on an unregistered rel type before it reaches the
        # ``_REL_MERGE`` ``.format`` interpolation — symmetric to the node-label
        # guard (``nodes_by_label[n.kind]`` raises ``KeyError`` for an unregistered
        # kind). Enforces the load-bearing invariant "edge kinds are REL_TYPES
        # constants" at the ingest boundary, so a rel type ever derived from parsed
        # source can never be interpolated into Cypher.
        if e.kind not in REL_TYPES:
            raise KeyError(
                f"edge kind {e.kind!r} is not a registered REL_TYPES member "
                f"({REL_TYPES})"
            )
        edges_by_group[(e.kind, src_label, dst_label)].append(_edge_row(e))

    episodes = _episode_rows(ir)

    def _write(tx):
        if episodes:
            tx.run(_EPISODE_MERGE, rows=episodes)
        for label, rows in nodes_by_label.items():
            if rows:
                tx.run(_NODE_MERGE.format(label=label), rows=rows)
        for (rel, src_label, dst_label), rows in edges_by_group.items():
            tx.run(
                _REL_MERGE.format(
                    rel=rel, src_label=src_label, dst_label=dst_label
                ),
                rows=rows,
                edge_kind=EDGE_KIND_DETERMINISTIC,
            )

    with driver.session() as session:
        session.execute_write(_write)
