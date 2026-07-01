"""Intermediate representation (IR) for static code extraction.

A serializable domain model that a later Neo4j-ingest node consumes. It carries
the deterministic structural ontology extracted from source:

  Nodes: Repo, Package, File, Class, Method
  Edges: CONTAINS, IMPORTS, CALLS, DEPENDS_ON

Every node and edge carries git ``Provenance`` (source_commit / author /
committed_at) read once for the pinned ingest commit.

Node identity is the deterministic ``qualified_name``:
  - Package : FQN                     (kr.co.ecoletree.service.commute.service)
  - File    : repo-relative path      (src/main/java/.../CommuteService.java)
  - Class   : package.Class           (kr.co.ecoletree...service.CommuteService)
  - Method  : package.Class#m(types)  (...CommuteService#insertGotoWork(Map,HttpServletRequest))

The structure is plain dataclasses; ``to_dict()`` yields dict/JSON-serializable output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Node kinds
REPO = "Repo"
PACKAGE = "Package"
FILE = "File"
CLASS = "Class"
METHOD = "Method"

# Edge kinds (deterministic structural ontology)
CONTAINS = "CONTAINS"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
DEPENDS_ON = "DEPENDS_ON"

# Inferred semantic layer: externally-generated summaries (palimpsest is
# provider-free — it never calls an LLM; a summary is produced elsewhere and
# handed in for grounded load). A Summary node SUMMARIZES the code it grounds.
SUMMARY = "Summary"          # node label
SUMMARIZES = "SUMMARIZES"    # edge type

# ``edge_kind`` marker — the schema-enforced no-laundering separation between the
# deterministic structural layer and the inferred semantic layer. Both values are
# colocated here so the two edge_kind constants live in one place.
EDGE_KIND_DETERMINISTIC = "deterministic"
EDGE_KIND_INFERRED = "inferred"


@dataclass(frozen=True)
class Provenance:
    """Git grounding for the pinned ingest commit, read once."""

    source_commit: str
    author: str
    committed_at: str

    def to_dict(self) -> dict:
        return {
            "source_commit": self.source_commit,
            "author": self.author,
            "committed_at": self.committed_at,
        }


@dataclass
class Node:
    """A structural entity. ``qualified_name`` is the identity."""

    kind: str
    qualified_name: str
    name: str
    provenance: Provenance
    # file:line grounding — set for File / Class / Method, None for Repo / Package
    path: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None

    @property
    def id(self) -> str:
        return self.qualified_name

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "name": self.name,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class Edge:
    """A directed relation between node identities.

    ``dst`` is a node ``qualified_name``. For IMPORTS it may point at an external
    qualified name that has no corresponding node (unresolved / external code) —
    that is honest for a source-only parser.
    """

    kind: str
    src: str
    dst: str
    provenance: Provenance

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "src": self.src,
            "dst": self.dst,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class IR:
    """The extraction result: a set of nodes and edges."""

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    # --- convenience lookups (used by tests / ingest) ---

    def nodes_of(self, kind: str) -> list[Node]:
        return [n for n in self.nodes if n.kind == kind]

    def node(self, qualified_name: str) -> Optional[Node]:
        for n in self.nodes:
            if n.qualified_name == qualified_name:
                return n
        return None

    def edges_of(self, kind: str) -> list[Edge]:
        return [e for e in self.edges if e.kind == kind]

    def has_edge(self, kind: str, src: str, dst: str) -> bool:
        return any(
            e.kind == kind and e.src == src and e.dst == dst for e in self.edges
        )


@dataclass(frozen=True)
class SummaryClaim:
    """One grounded assertion inside a :class:`Summary`.

    ``source_refs`` are node ids (a symbol ``qualified_name`` or a repo-relative
    file path) that must each resolve to a real graph node — a claim with no
    resolvable ref is ungrounded prose, and the loader rejects the whole summary
    rather than launder it in.
    """

    text: str
    source_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"text": self.text, "source_refs": list(self.source_refs)}


@dataclass(frozen=True)
class Summary:
    """An externally-generated semantic summary of one code entity.

    palimpsest never calls an LLM: the summary is produced elsewhere and handed
    in for grounded, idempotent load. ``target_id`` is the summarized node (the
    ``SUMMARIZES`` anchor); ``source_commit`` is the code commit it was generated
    against — together with ``generator``/``model`` they derive the deterministic,
    namespace-isolated Summary id.

    ``code_bound_at`` is deliberately NOT a field here: freshness must follow the
    code, not the generator's wall-clock, so the loader binds it to the resolved
    target node's ``committed_at`` (a git-less external summary has no meaningful
    commit time of its own). ``created_at`` is the external generation time.
    """

    target_id: str
    claims: tuple[SummaryClaim, ...]
    generator: str
    model: str
    source_commit: str
    created_at: str
    prompt: Optional[str] = None
    confidence: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "claims": [c.to_dict() for c in self.claims],
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "prompt": self.prompt,
            "confidence": self.confidence,
        }
