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

import re
from dataclasses import dataclass, field, replace
from typing import Optional

# Unit Separator (0x1f) — joins a branch namespace to a qualified_name in a
# branch-scoped id. Like the ``summary:`` / ``community:`` prefixes it guarantees
# a scoped id can never collide with a bare qualified_name.
_BRANCH_US = "\x1f"


def branch_scoped_id(branch: Optional[str], qualified_name: str) -> str:
    """The pure identity fn shared by node ids AND edge endpoints.

    ``branch=None`` returns the bare ``qualified_name`` (byte-identical to
    single-branch capture — additive). A named branch folds into the MERGE key
    as ``branch:<branch>\\x1f<qualified_name>`` so N branches of one symbol
    coexist as distinct nodes (ac-1). Using the SAME fn for node ids and edge
    src/dst keeps ids and endpoints consistent and capture-order-invariant.
    """
    if branch is None:
        return qualified_name
    return f"branch:{branch}{_BRANCH_US}{qualified_name}"


# ---------------------------------------------------------------------------
# Canonical HTTP route functions (Decisions 2 & 3 of the API-semantics contract).
# Pure, and shared by BOTH the extractors (extract/*) and the cross-tier matcher
# (kg/calls_api.py) — the ``branch_scoped_id`` precedent: one identity fn imported
# both ways, so route identity and route matching can never drift apart.
#
# TWO normalization LEVELS, deliberately NOT conflated:
#   * IDENTITY level (``normalize_endpoint_path``) PRESERVES native param names —
#     erasing them in identity would collapse sibling routes into one node.
#   * MATCH level (``canonical_route_path``) erases param names to "{}" / "{**}" so
#     a caller's ApiCall and a server's Endpoint compare equal across tiers.
# ---------------------------------------------------------------------------

# Method sentinel for a method-less mapping (@RequestMapping without a method): the
# route answers ANY verb. The matcher treats it (and SvelteKit's "fallback") as a
# wildcard method with reduced confidence (Decision 2e).
ANY_METHOD = "*"

# A template-literal interpolation (``${...}``) inside a URL literal — collapses to
# the single-segment placeholder when building an ApiCall route (Decision 3).
_URL_INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def normalize_endpoint_path(raw_path: str) -> str:
    """IDENTITY-level path normalization (Decision 2a) — PRESERVES param names.

    Single leading ``/``, collapse repeated ``//``, strip a trailing ``/`` (except
    root). Native param syntax ({id}, {id:[0-9]+}, [slug], [id=int]) is preserved
    verbatim: it is part of a route's IDENTITY, so erasing it here would collapse
    distinct sibling routes into one node. Empty input -> root ``/``.
    """
    segments = [s for s in raw_path.split("/") if s != ""]
    if not segments:
        return "/"
    return "/" + "/".join(segments)


def _canonical_segment(seg: str) -> str:
    """Reduce ONE path segment to its match-level form (helper for
    :func:`canonical_route_path`)."""
    # Already-canonical forms first -> idempotence (they are the fixed points).
    if seg == "{**}":
        return "{**}"
    if seg == "{}":
        return "{}"
    # Rest / catch-all forms -> "{**}"  (SvelteKit [...rest] / [[...rest]],
    # Ant-style **, Spring 6 {*name}).
    if (
        (seg.startswith("[[...") and seg.endswith("]]"))
        or (seg.startswith("[...") and seg.endswith("]"))
        or (seg == "**")
        or (seg.startswith("{*") and seg.endswith("}"))
    ):
        return "{**}"
    # Single-variable forms -> "{}"  (SvelteKit [id]/[id=int], optional [[opt]],
    # brace {id}/{id:regex}, Express/Spring :id, template ${...}).
    if (
        (seg.startswith("[[") and seg.endswith("]]"))
        or (seg.startswith("[") and seg.endswith("]"))
        or (seg.startswith("${") and seg.endswith("}"))
        or (seg.startswith("{") and seg.endswith("}"))
        or seg.startswith(":")
    ):
        return "{}"
    # Literal segment — unchanged, case-sensitive.
    return seg


def canonical_route_path(path: str) -> str:
    """MATCH-level path canonicalization (Decision 2b), idempotent.

    Per ``/`` segment: any single path variable ([id], [id=m], {id}, {id:regex},
    :id, ${...}, {}, optional [[opt]]) -> "{}"; any rest/catch-all ([...rest], **,
    {*x}, {**}) -> "{**}"; a literal segment is unchanged (case-sensitive). The
    placeholders "{}" / "{**}" are fixed points, so applying it twice yields the
    same string (a route already at match level is stable).
    """
    segments = [s for s in path.split("/") if s != ""]
    if not segments:
        return "/"
    return "/" + "/".join(_canonical_segment(s) for s in segments)


def canonical_match_key(qualified_name: str) -> tuple[str, str]:
    """The cross-tier matcher's ONLY entry point (Decision 2b).

    Strips an optional ``<ns>:`` namespace prefix (only when the ``:`` occurs
    BEFORE the first space, so a ``:`` inside a path/regex param is untouched),
    splits the remainder into ``method`` and ``path``, and returns
    ``(method, canonical_route_path(path))``. So "GET /api/users/[id]",
    "spring:GET /api/users/{userId}" and "apicall:GET /api/users/{}" all reduce to
    ``("GET", "/api/users/{}")``.
    """
    s = qualified_name
    space = s.find(" ")
    colon = s.find(":")
    if colon != -1 and (space == -1 or colon < space):
        s = s[colon + 1:]
    method, _, path = s.partition(" ")
    return (method, canonical_route_path(path))


def api_call_qualified_name(method: str, raw_url: str) -> Optional[str]:
    """Build an :data:`API_CALL` node identity from a call-site URL (Decision 3).

    Returns ``f"apicall:{method} {path}"`` where ``path`` is the STATIC template of
    the URL with each template-literal interpolation (``${...}``) collapsed to
    ``{}`` — e.g. ("GET", "`/api/orders/${id}`") -> "apicall:GET /api/orders/{}".

    Returns ``None`` (the ApiCall is NOT emitted) when the URL is a non-templatable
    dynamic value: a bare variable or a runtime concatenation (no enclosing
    string/template literal), or a literal that has NO static segment at all
    (purely an interpolation). This is the ac-6 disclosed gap — an unresolvable URL
    is an honest absence, never a false "endpoint unused".
    """
    text = raw_url.strip()
    # A string or template literal is templatable; a bare variable / runtime
    # concat (no matching enclosing quote) is a dynamic URL -> disclosed gap.
    if len(text) >= 2 and text[0] in "\"'`" and text[-1] == text[0]:
        inner = text[1:-1]
    else:
        return None
    templated = normalize_endpoint_path(_URL_INTERPOLATION.sub("{}", inner))
    # A literal that reduces to only placeholders (e.g. `${url}`) is a dressed-up
    # bare variable — no static route to match on -> disclosed gap.
    if not any(seg not in ("{}", "{**}") for seg in templated.split("/") if seg):
        return None
    return f"apicall:{method} {templated}"


# Node kinds
REPO = "Repo"
PACKAGE = "Package"
FILE = "File"
CLASS = "Class"
METHOD = "Method"
# A module-level function (no declaring class) — a first-class callable symbol
# distinct from METHOD. Participates in the same deterministic CALLS ontology.
FUNCTION = "Function"
# A named variable symbol (module- or class-level binding). Carries only the
# variable NAME as identity — no literal value field on the Node dataclass.
VARIABLE = "Variable"
# A Community groups Classes that form a connected component at the Class level
# (via cross-class CALLS / DEPENDS_ON) — a deterministic, rebuild-stable partition.
COMMUNITY = "Community"
# SvelteKit routing ontology kinds (deterministic structural). A Route is a page
# route whose URL pattern is its identity (e.g. "/blog/[slug]"); an Endpoint is a
# server request handler (e.g. "GET /api/x"); a Layout is a nested layout shell;
# a Hook is a request-lifecycle hook (hooks.server.ts).
ROUTE = "Route"
ENDPOINT = "Endpoint"
LAYOUT = "Layout"
HOOK = "Hook"
# HTTP-API call-site node (deterministic structural): a recognized outbound HTTP
# call (fetch / axios / RestTemplate / ...) whose identity is its
# "apicall:{method} {path}" route template. The cross-tier peer of an Endpoint — a
# front-end ApiCall is later matched to a back-end Endpoint by a dedicated inferred
# CALLS_API loader.
API_CALL = "ApiCall"

# Edge kinds (deterministic structural ontology)
CONTAINS = "CONTAINS"
IMPORTS = "IMPORTS"
CALLS = "CALLS"
DEPENDS_ON = "DEPENDS_ON"
# A Class is a MEMBER_OF exactly one Community (exclusive, flat partition).
MEMBER_OF = "MEMBER_OF"
# An Episode (a commit) MODIFIES the File(s) that commit changed — the churn /
# co-change spine. Deterministic (derived from ``git diff-tree``, no judgment):
# src is a bare commit-scoped Episode (never an ``ir.nodes`` entry), dst a
# branch-scoped File id. Written by a DEDICATED loader (Episodes live outside the
# generic node/edge path), and deliberately absent from recall's traversal
# whitelist so a churn edge never drags an author-bearing Episode into items.
MODIFIES = "MODIFIES"

# SvelteKit routing ontology edges (deterministic structural), distinct from the
# generic structural set above: a File REALIZES the Route/Endpoint/Layout it
# defines; a Function HANDLES an Endpoint (its request handler); a Function LOADS
# a Route/Layout (its load() data function); a Hook GUARDS the Endpoint/Route it
# gates.
REALIZES = "REALIZES"
HANDLES = "HANDLES"
LOADS = "LOADS"
GUARDS = "GUARDS"

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

# Inferred cross-tier edge (dedicated loader, SUMMARIZES/RISKS/DECIDES precedent):
# a front-end ApiCall CALLS_API a back-end Endpoint when their canonical routes
# match. DELIBERATELY absent from ``REL_TYPES`` (Frozen Invariant 3) so the generic
# deterministic writer can never stamp it; ``kg/calls_api.py`` is the ONLY producer
# and writes it with ``edge_kind='inferred'``. The ingest fail-closed guard rejects
# an accidental CALLS_API in ``ir.edges``, forcing use of that dedicated loader.
CALLS_API = "CALLS_API"    # edge type (ApiCall -> Endpoint), inferred

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

# The single shared embedding dimension. This ONE constant is used by BOTH the
# Neo4j VECTOR INDEX DDL and the per-summary dimension validator — never two
# literals: Neo4j silently SKIPS a vector property whose size != the index
# dimension (silent-unsearchable on drift), so the guard and the index must agree
# by construction. palimpsest is provider-free: the embedding arrives on the
# payload (produced elsewhere); palimpsest never generates one.
EMBEDDING_DIM = 1536


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
    # Branch namespace folded into IDENTITY (the MERGE key), None for the bare
    # single-branch plane. Set by ``scope_to_branch``; persisted as a node
    # property (the GC discriminator).
    branch: Optional[str] = None
    # Deterministic test-impact marker (ADR-20260706 §결정6): True on File/Class/
    # Method nodes classified as test code (src/test path / @Test / junit import),
    # else None. A pure PROPERTY — deliberately OFF ``id``/``branch_scoped_id`` so it
    # never perturbs node identity; ``scope_to_branch``'s ``replace`` preserves it.
    is_test: Optional[bool] = None
    # SvelteKit routing marker: True on a server-only node (a +page.server.ts /
    # +server.ts module, hooks.server.ts), else None. Like ``is_test`` a pure
    # PROPERTY — deliberately OFF ``id``/``branch_scoped_id`` so it never perturbs
    # node identity; ``scope_to_branch``'s ``replace`` preserves it.
    server_only: Optional[bool] = None
    # DI / stereotype role marker (Decision 6): the Spring stereotype a Class plays
    # ("controller" / "repository" / "service" / "component"), else None. Like
    # ``is_test`` / ``server_only`` a pure PROPERTY — deliberately OFF
    # ``id``/``branch_scoped_id`` so it never perturbs node identity;
    # ``scope_to_branch``'s ``replace`` preserves it.
    role: Optional[str] = None

    @property
    def id(self) -> str:
        return branch_scoped_id(self.branch, self.qualified_name)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "qualified_name": self.qualified_name,
            "name": self.name,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "is_test": self.is_test,
            "server_only": self.server_only,
            "role": self.role,
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
    # Per-edge resolution-precision marker ("typed" | "name" | None): how ``dst``
    # was resolved for reference edges (CALLS/DEPENDS_ON). Orthogonal to
    # ``edge_kind`` (deterministic/inferred) — a pure PROPERTY, set by the
    # language extractor. Additive default so positional constructions keep
    # working; None means unmarked (structural edges never carry it).
    resolution: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "src": self.src,
            "dst": self.dst,
            "provenance": self.provenance.to_dict(),
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Edge":
        # Absent "resolution" -> "name": a pre-marker payload was in practice
        # name-resolved, so backward-compat loads conservatively (never null).
        return cls(
            kind=data["kind"],
            src=data["src"],
            dst=data["dst"],
            provenance=Provenance(**data["provenance"]),
            resolution=data.get("resolution", "name"),
        )


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


def scope_to_branch(ir: IR, branch: Optional[str]) -> IR:
    """Pure transform: return a NEW IR whose identities are folded into ``branch``.

    Stamps ``branch`` on every code Node and rewrites every edge's src/dst via the
    SAME pure fn (:func:`branch_scoped_id`) so scoped node ids and edge endpoints
    stay consistent and capture-order-invariant. The input IR is NOT mutated, so
    one extracted IR can be fanned out to several branches. ``branch=None`` yields
    a byte-identical copy (Episodes stay bare — they are commit-scoped and derived
    from provenance at ingest, never in ``ir.nodes``).
    """
    nodes = [replace(n, branch=branch) for n in ir.nodes]
    edges = [
        replace(
            e,
            src=branch_scoped_id(branch, e.src),
            dst=branch_scoped_id(branch, e.dst),
        )
        for e in ir.edges
    ]
    return IR(nodes=nodes, edges=edges)


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
    # An EXTERNAL judge's COVERAGE verdict — the code->claim completeness axis,
    # INDEPENDENT of ``semantic_verdict`` (claim->code faithfulness). Kept on its own
    # field so the two axes never overload each other. Shape is the judge's contract
    # (e.g. ``{"verdict": "complete"|"incomplete"|"unverified", "uncovered": [...],
    # "judge": str, "model": str}``); palimpsest never produces it, only ingests it.
    # Absent -> None (uncovered/unverified).
    coverage_verdict: Optional[dict] = None
    # An EXTERNAL embedding of the summary, produced elsewhere and handed in
    # (provider-free: palimpsest never generates one). ``embedding`` is the vector
    # (float[EMBEDDING_DIM]); ``embedding_model`` names the model that produced it
    # (a cosine index is single-model — mixing models is meaningless even at equal
    # dim); ``embedding_dim`` is the declared dimension. All absent -> None so a
    # pre-existing embedding-less payload loads unchanged.
    embedding: Optional[list[float]] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None

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
            "coverage_verdict": self.coverage_verdict,
            "embedding": list(self.embedding) if self.embedding is not None else None,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Summary":
        embedding = data.get("embedding")
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
            coverage_verdict=data.get("coverage_verdict"),
            embedding=list(embedding) if embedding is not None else None,
            embedding_model=data.get("embedding_model"),
            embedding_dim=data.get("embedding_dim"),
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
