"""Knowledge Graph ingest: extraction IR -> Neo4j (deterministic ontology).

Realizes the minimal, deterministic ontology in Neo4j and batch-ingests the
extraction ``IR`` idempotently (MERGE-on-id; git is SoT so the projection is
rebuildable) with git provenance + freshness stamping.
"""

from palimpsest.kg.ingest import create_constraints, ingest

__all__ = ["create_constraints", "ingest"]
