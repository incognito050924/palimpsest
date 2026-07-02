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

from palimpsest.ir import (
    IR,
    REPO,
    PACKAGE,
    FILE,
    CLASS,
    METHOD,
    COMMUNITY,
    CONTAINS,
    IMPORTS,
    CALLS,
    DEPENDS_ON,
    MEMBER_OF,
    SUMMARY,
    EDGE_KIND_DETERMINISTIC,
)

# The ontology, closed and explicit (no dynamic labels from data). ``Summary`` is
# the inferred-layer label; it carries no deterministic IR node, but is listed
# here so ``create_constraints`` provisions its uniqueness CONSTRAINT. ``Community``
# is a deterministic node materialized in the IR by ``augment_communities``.
NODE_LABELS = [REPO, PACKAGE, FILE, CLASS, METHOD, "Episode", SUMMARY, COMMUNITY]
REL_TYPES = [CONTAINS, IMPORTS, CALLS, DEPENDS_ON, MEMBER_OF]

# A MERGE-on-id per label; property SET is uniform (unused props resolve to
# null, which Neo4j drops — Repo/Package simply carry no path/line grounding).
_NODE_MERGE = """
UNWIND $rows AS row
MERGE (n:`{label}` {{id: row.id}})
SET n.name          = row.name,
    n.qualified_name = row.qualified_name,
    n.path          = row.path,
    n.start_line    = row.start_line,
    n.end_line      = row.end_line,
    n.source_commit = row.source_commit,
    n.author        = row.author,
    n.committed_at  = row.committed_at,
    n.code_bound_at = row.code_bound_at
"""

# Endpoints are MATCHed (not merged): an edge whose target is unresolved/external
# (e.g. IMPORTS java.util.Map — honest for a source-only parser) has no node to
# attach to in the typed ontology, so it is dropped rather than materialised as
# an untyped phantom node.
_REL_MERGE = """
UNWIND $rows AS row
MATCH (a {{id: row.src}})
MATCH (b {{id: row.dst}})
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
        "path": node.path,
        "start_line": node.start_line,
        "end_line": node.end_line,
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

    edges_by_type = {rel: [] for rel in REL_TYPES}
    for e in ir.edges:
        edges_by_type[e.kind].append(_edge_row(e))

    episodes = _episode_rows(ir)

    def _write(tx):
        if episodes:
            tx.run(_EPISODE_MERGE, rows=episodes)
        for label, rows in nodes_by_label.items():
            if rows:
                tx.run(_NODE_MERGE.format(label=label), rows=rows)
        for rel, rows in edges_by_type.items():
            if rows:
                tx.run(
                    _REL_MERGE.format(rel=rel),
                    rows=rows,
                    edge_kind=EDGE_KIND_DETERMINISTIC,
                )

    with driver.session() as session:
        session.execute_write(_write)
