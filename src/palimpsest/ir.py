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
# A Community groups Classes that form a connected component at the Class level
# (via cross-class CALLS / DEPENDS_ON) — a deterministic, rebuild-stable partition.
COMMUNITY = "Community"

# Edge kinds (deterministic structural ontology)
CONTAINS = "CONTAINS"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
DEPENDS_ON = "DEPENDS_ON"
# A Class is a MEMBER_OF exactly one Community (exclusive, flat partition).
MEMBER_OF = "MEMBER_OF"

# Inferred semantic layer: externally-generated summaries (palimpsest is
# provider-free — it never calls an LLM; a summary is produced elsewhere and
# handed in for grounded load). A Summary node SUMMARIZES the code it grounds.
SUMMARY = "Summary"          # node label
SUMMARIZES = "SUMMARIZES"    # edge type

# Inferred semantic layer (first-class judgment entity): an externally-generated
# Risk — a "this code is risky" judgment with its own identity. A Risk node RISKS
# the code node(s) it flags. Like Summary, produced elsewhere and handed in for
# grounded load; palimpsest never judges.
RISK = "Risk"                # node label
RISKS = "RISKS"              # edge type

# Inferred semantic layer (first-class decision entity): an externally-generated
# DesignDecision — "this is a design decision" with its own identity. It DECIDES
# code node(s) or other decisions, SUPERSEDES other decisions, and ADDRESSES_RISK
# Risk nodes. Like Risk, produced elsewhere and handed in for grounded load;
# palimpsest never judges.
DESIGN_DECISION = "DesignDecision"    # node label
DECIDES = "DECIDES"                   # edge type (DesignDecision -> code | DesignDecision)
SUPERSEDES = "SUPERSEDES"            # edge type (DesignDecision -> DesignDecision)
ADDRESSES_RISK = "ADDRESSES_RISK"    # edge type (DesignDecision -> Risk)

# Inferred GENERIC relations between two EXISTING entities (no new node): an
# external generator asserts a relation; palimpsest loads it grounded (both
# endpoints resolve) with edge_kind='inferred'. rel_type is restricted to this
# closed set — never free-form data in the query.
CAUSALLY_RELATES = "CAUSALLY_RELATES"   # directed: cause -> effect
RELATES_TO = "RELATES_TO"               # association
CONFLICTS_WITH = "CONFLICTS_WITH"       # conflict (숨은 의도 충돌 표시)
INFERRED_RELATION_TYPES = frozenset({CAUSALLY_RELATES, RELATES_TO, CONFLICTS_WITH})

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

    @classmethod
    def from_dict(cls, data: dict) -> "SummaryClaim":
        return cls(
            text=data["text"],
            source_refs=tuple(data.get("source_refs", ())),
        )


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
    # An EXTERNAL judge's semantic verdict (e.g. ditto), NOT the generator's
    # self-report — kept on its own field, never overloading ``confidence``.
    # ``{"verdict": "faithful"|"unfaithful"|"unverified", "judge": str, "model": str}``.
    # palimpsest never produces this; it only ingests it. Absent -> None (unverified).
    semantic_verdict: Optional[dict] = None

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
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Summary":
        return cls(
            target_id=data["target_id"],
            claims=tuple(SummaryClaim.from_dict(c) for c in data.get("claims", ())),
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            prompt=data.get("prompt"),
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )


@dataclass(frozen=True)
class Risk:
    """An externally-generated risk judgment over one or more code entities.

    palimpsest never calls an LLM: the judgment ("this code is risky") is produced
    elsewhere and handed in for grounded, idempotent load. ``flags`` are the code
    node ids this risk flags (the ``RISKS`` targets); each must resolve to a real
    graph node, and a Risk must flag >=1 — a risk with no resolvable flag is a
    floating judgment, and the loader rejects it rather than launder it in.

    Like :class:`Summary`, ``code_bound_at`` is deliberately NOT a field: freshness
    must follow the code, so the loader binds it to a flagged node's
    ``committed_at``. ``author`` is likewise absent — authorship lives on the
    grounded deterministic code nodes; the judgment's origin is attributed via
    ``generator``/``model``. ``created_at`` is the external generation time.
    """

    title: str
    flags: tuple[str, ...]
    generator: str
    model: str
    source_commit: str
    created_at: str
    confidence: Optional[float] = None
    # An EXTERNAL judge's semantic verdict, NOT the generator's self-report — kept
    # on its own field, never overloading ``confidence`` (mirrors Summary). Absent
    # -> None (unverified). palimpsest never produces this; it only ingests it.
    semantic_verdict: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "flags": list(self.flags),
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Risk":
        return cls(
            title=data["title"],
            flags=tuple(data.get("flags", ())),
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )


@dataclass(frozen=True)
class InferredRelation:
    """An externally-generated inferred relation between two EXISTING entities.

    palimpsest never calls an LLM: the assertion ("A relates to / causally relates
    to / conflicts with B") is produced elsewhere and handed in for grounded,
    idempotent load. ``source_id``/``target_id`` are ids of existing graph nodes —
    BOTH must resolve, else the loader rejects the relation (entity-atomic) rather
    than launder a dangling edge in. ``rel_type`` must be one of
    :data:`INFERRED_RELATION_TYPES`. No new node is created; this is a pure edge.

    Like :class:`Risk`, ``code_bound_at`` is NOT a field — the loader binds it to
    the source endpoint's ``committed_at`` so freshness follows the code. The
    assertion's origin is attributed via ``generator``/``model``; ``created_at`` is
    the external generation time.
    """

    source_id: str
    target_id: str
    rel_type: str
    generator: str
    model: str
    source_commit: str
    created_at: str
    confidence: Optional[float] = None
    semantic_verdict: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "rel_type": self.rel_type,
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InferredRelation":
        return cls(
            source_id=data["source_id"],
            target_id=data["target_id"],
            rel_type=data["rel_type"],
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )


@dataclass(frozen=True)
class DesignDecision:
    """An externally-generated design decision over code and other entities.

    palimpsest never calls an LLM: the decision ("this is a design decision") is
    produced elsewhere and handed in for grounded, idempotent load. It carries
    three inferred edge target sets: ``decides`` (the ``DECIDES`` targets — code
    nodes or other decisions), ``supersedes`` (``SUPERSEDES`` targets — other
    DesignDecisions), and ``addresses_risks`` (``ADDRESSES_RISK`` targets — Risk
    nodes). Grounding: a decision must have >=1 ``DECIDES`` target and EVERY edge
    target must resolve to a real graph node, else the loader rejects the whole
    decision (entity-atomic) rather than launder a floating decision in.

    Like :class:`Risk`/:class:`Summary`, ``code_bound_at`` is deliberately NOT a
    field: freshness must follow the code, so the loader binds it to a decided
    code node's ``committed_at``. ``author`` is likewise absent — the decision's
    origin is attributed via ``generator``/``model``. ``created_at`` is the
    external generation time.
    """

    title: str
    decides: tuple[str, ...]
    supersedes: tuple[str, ...]
    addresses_risks: tuple[str, ...]
    generator: str
    model: str
    source_commit: str
    created_at: str
    confidence: Optional[float] = None
    # An EXTERNAL judge's semantic verdict, NOT the generator's self-report — kept
    # on its own field, never overloading ``confidence`` (mirrors Risk/Summary).
    # Absent -> None (unverified). palimpsest never produces this; it only ingests.
    semantic_verdict: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "decides": list(self.decides),
            "supersedes": list(self.supersedes),
            "addresses_risks": list(self.addresses_risks),
            "generator": self.generator,
            "model": self.model,
            "source_commit": self.source_commit,
            "created_at": self.created_at,
            "confidence": self.confidence,
            "semantic_verdict": self.semantic_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DesignDecision":
        return cls(
            title=data["title"],
            decides=tuple(data.get("decides", ())),
            supersedes=tuple(data.get("supersedes", ())),
            addresses_risks=tuple(data.get("addresses_risks", ())),
            generator=data["generator"],
            model=data["model"],
            source_commit=data["source_commit"],
            created_at=data["created_at"],
            confidence=data.get("confidence"),
            semantic_verdict=data.get("semantic_verdict"),
        )
