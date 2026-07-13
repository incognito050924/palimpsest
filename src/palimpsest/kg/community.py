"""Community detection at the Class level (deterministic, provider-free).

A Community is a connected component of Classes, where two Classes are connected
iff there is a cross-class edge between them in the RESOLVED deterministic IR:

  * a ``DEPENDS_ON`` (Class -> Class), or
  * a ``CALLS`` (Method -> Method) lifted to its declaring Classes via the
    ``Class-[CONTAINS]->Method`` edge (self-edges — same declaring class — dropped).

The partition is EXCLUSIVE and FLAT: every Class lands in exactly one Community,
an isolated Class forming its own singleton. Detection is pure Python
(union-find) over the IR — no GDS, no LLM.

Reconciliation is *materialize-in-IR*: :func:`augment_communities` appends the
``Community`` nodes and ``Class-[MEMBER_OF]->Community`` edges to the IR so the
generic ``ingest`` writers persist and provenance-stamp them like any other
node/edge (``edge_kind='deterministic'`` is set automatically).
"""

from __future__ import annotations

import hashlib

from palimpsest.ir import (
    CALLS,
    CLASS,
    COMMUNITY,
    CONTAINS,
    DEPENDS_ON,
    FILE,
    FUNCTION,
    MEMBER_OF,
    METHOD,
    Edge,
    IR,
    Node,
    Provenance,
)


def community_id(members) -> str:
    """Deterministic, namespace-isolated Community id.

    ``raw`` is the NUL-joined sorted member Class qualified_names, so the id is
    invariant under member order — rebuild-stable (ac-3). The ``community:``
    prefix guarantees it can never collide with a code ``qualified_name``.
    Mirrors :func:`palimpsest.kg.summary.summary_id`.
    """
    raw = "\x00".join(sorted(members))
    return "community:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _containers(ir: IR) -> set[str]:
    """The grouping-container qualified_names the partition ranges over.

    Mixed / hierarchical granularity: every Class is a container, AND every File
    that directly contains a top-level Function is a container (a Module). A File
    whose only members are Classes (the Java case) is NOT a container — its
    Classes are — so Class-based grouping is unchanged; the Module containers are
    purely additive for languages with top-level functions (e.g. Kotlin).
    """
    class_ids = {n.qualified_name for n in ir.nodes_of(CLASS)}
    file_ids = {n.qualified_name for n in ir.nodes_of(FILE)}
    function_ids = {n.qualified_name for n in ir.nodes_of(FUNCTION)}
    module_ids = {
        e.src
        for e in ir.edges_of(CONTAINS)
        if e.src in file_ids and e.dst in function_ids
    }
    return class_ids | module_ids


def _unit_of(ir: IR) -> dict[str, str]:
    """Map each code unit (Method or top-level Function) to its grouping
    container via CONTAINS: a Method lifts to its declaring Class, a top-level
    Function lifts to its containing File (Module).

    CONTAINS is overloaded (Repo->Package, Package->File, File->Class,
    Class->Method, File->Function); we keep only the container->unit edges
    (Class->Method and File->Function) by checking both endpoints against the
    node index.
    """
    class_ids = {n.qualified_name for n in ir.nodes_of(CLASS)}
    file_ids = {n.qualified_name for n in ir.nodes_of(FILE)}
    method_ids = {n.qualified_name for n in ir.nodes_of(METHOD)}
    function_ids = {n.qualified_name for n in ir.nodes_of(FUNCTION)}
    mapping: dict[str, str] = {}
    for e in ir.edges_of(CONTAINS):
        if e.src in class_ids and e.dst in method_ids:
            mapping[e.dst] = e.src
        elif e.src in file_ids and e.dst in function_ids:
            mapping[e.dst] = e.src
    return mapping


def _unit_level_pairs(ir: IR) -> set[frozenset]:
    """Undirected cross-container links: DEPENDS_ON between containers plus CALLS
    lifted to declaring containers (self-links dropped). Inputs are the RESOLVED
    IR edges. Generalizes the former Class-only lifting to Class ∪ Module so a
    top-level Function->Function call joins the two Files (Modules)."""
    container_ids = _containers(ir)
    u2c = _unit_of(ir)
    pairs: set[frozenset] = set()
    for e in ir.edges_of(DEPENDS_ON):
        if e.src in container_ids and e.dst in container_ids and e.src != e.dst:
            pairs.add(frozenset((e.src, e.dst)))
    for e in ir.edges_of(CALLS):
        cs, cd = u2c.get(e.src), u2c.get(e.dst)
        if cs and cd and cs != cd:
            pairs.add(frozenset((cs, cd)))
    return pairs


def compute_communities(ir: IR) -> list[list[str]]:
    """Partition the IR's grouping containers into connected components (union-
    find). Containers are Classes plus Modules (Files carrying top-level
    Functions) — see :func:`_containers`.

    Returns a deterministically-ordered list of Communities, each a sorted list
    of member container qualified_names. Every container appears in exactly one.
    """
    container_ids = sorted(_containers(ir))
    parent = {c: c for c in container_ids}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # Attach the lexicographically-larger root under the smaller so the
            # component root is order-independent (rebuild-stable).
            parent[max(ra, rb)] = min(ra, rb)

    for pair in _unit_level_pairs(ir):
        a, b = tuple(pair)
        union(a, b)

    groups: dict[str, list[str]] = {}
    for c in container_ids:
        groups.setdefault(find(c), []).append(c)
    return sorted(sorted(members) for members in groups.values())


def augment_communities(ir: IR, provenance: Provenance) -> IR:
    """Materialize the Class-level Community partition into ``ir`` (in place).

    Appends one ``Community`` node per component and a ``Class-[MEMBER_OF]->
    Community`` edge per member, each stamped with the corpus-level
    ``provenance`` (Community nodes carry no path/line grounding, like Repo /
    Package). The existing ``ingest`` then persists them generically.
    """
    for members in compute_communities(ir):
        cid = community_id(members)
        ir.nodes.append(
            Node(kind=COMMUNITY, qualified_name=cid, name=cid, provenance=provenance)
        )
        for cls in members:
            ir.edges.append(
                Edge(kind=MEMBER_OF, src=cls, dst=cid, provenance=provenance)
            )
    return ir
