"""Progressive, grounded, combinatorial recall over the Knowledge Graph.

Design (ac-2 recall + ac-3 honesty):

* **Seed resolution.** A query is a node ``id`` — a symbol ``qualified_name`` or
  a repo-relative file path (a File node's id *is* its path). Both resolve by a
  single ``MATCH (n {id: $id})``. No natural-language parsing in v1.

* **Progressive, bounded traversal.** Breadth-first over the deterministic
  ontology (CALLS / DEPENDS_ON / CONTAINS / IMPORTS), traversed *undirected* so a
  Method reaches its containing Class (an incoming CONTAINS) as well as its
  callees (outgoing CALLS). Two budgets bound it: ``depth`` (max hops) and
  ``limit`` (max items). The whole graph is never loaded; when the budget is
  spent the unexpanded frontier is returned as an ``expand_handle`` so the next
  hop can be pulled on demand via :func:`expand`.

* **Grounding.** Every recalled item carries ``sources`` = ``source_commit`` +
  ``path`` + ``start_line`` / ``end_line`` (the same provenance the KG stamped).
  The top-level ``sources`` list is the same grounding, kept as a *separate*
  channel.

* **Combinatorial only (ac-3).** Assembly is pure graph traversal + dict
  building. There is no LLM / generative call anywhere on this path. Output
  fields stay SEPARATED — ``{items, sources, gaps, confidence, expand_handle}``,
  never a merged prose "answer".

* **Gaps (ac-3 honesty).** If the seed does not resolve, or an *explicitly
  requested* relation has no edges on the seed, that is stated as an explicit
  gap rather than returned as a confident empty answer. Structural coupling is
  never presented as a risk / quality judgment.
"""

from __future__ import annotations

from palimpsest.ir import CALLS, DEPENDS_ON, CONTAINS, IMPORTS

# The relations recall may traverse (the deterministic structural ontology).
DEFAULT_RELATIONS = (CALLS, DEPENDS_ON, CONTAINS, IMPORTS)

# Ontology node labels, in the order we pick a node's primary kind.
_NODE_LABELS = ("Repo", "Package", "File", "Class", "Method", "Episode")

_RESOLVE = "MATCH (n {id: $id}) RETURN n, labels(n) AS labels LIMIT 1"

# One undirected hop from a set of frontier ids over the requested relations.
# DISTINCT rows; deterministic order so item selection under a limit is stable.
_NEIGHBORS = """
UNWIND $ids AS sid
MATCH (a {id: sid})-[r]-(b)
WHERE type(r) IN $rels
RETURN DISTINCT b.id AS id, type(r) AS relation, labels(b) AS labels,
       b.name AS name, b.qualified_name AS qualified_name,
       b.path AS path, b.start_line AS start_line, b.end_line AS end_line,
       b.source_commit AS source_commit, b.committed_at AS committed_at
ORDER BY id, relation
"""

# Which of the requested relations actually have an edge on the seed.
_SEED_REL_TYPES = (
    "MATCH (a {id: $id})-[r]-() WHERE type(r) IN $rels "
    "RETURN DISTINCT type(r) AS relation"
)


def _kind(labels) -> str | None:
    labels = list(labels or ())
    for known in _NODE_LABELS:
        if known in labels:
            return known
    return labels[0] if labels else None


def _sources(rec) -> dict:
    return {
        "source_commit": rec.get("source_commit"),
        "path": rec.get("path"),
        "start_line": rec.get("start_line"),
        "end_line": rec.get("end_line"),
        "committed_at": rec.get("committed_at"),
    }


def _item(rec, relation, depth) -> dict:
    return {
        "id": rec.get("id"),
        "kind": _kind(rec.get("labels")),
        "name": rec.get("name"),
        "qualified_name": rec.get("qualified_name"),
        "relation": relation,   # how it was reached (None for the seed)
        "depth": depth,
        "sources": _sources(rec),
    }


def _is_grounded(item) -> bool:
    s = item["sources"]
    return bool(s["source_commit"] and s["path"] and s["start_line"] is not None)


def _confidence(items) -> float:
    """Deterministic grounding coverage: the share of returned items that
    resolve to concrete code (commit + file:line). Combinatorial, not a model
    score. Empty recall -> 0.0."""
    if not items:
        return 0.0
    grounded = sum(1 for it in items if _is_grounded(it))
    return round(grounded / len(items), 3)


def _resolve(driver, query):
    with driver.session() as session:
        rec = session.run(_RESOLVE, id=query).single()
    if rec is None:
        return None
    node = rec["n"]
    data = dict(node)
    data["labels"] = list(rec["labels"])
    return data


def _neighbors(driver, ids, rels):
    with driver.session() as session:
        return [r.data() for r in session.run(_NEIGHBORS, ids=list(ids), rels=list(rels))]


def _seed_relation_gaps(driver, query, relations):
    """For explicitly requested relations only: any that have no edge on the
    seed is an honest gap (a confident empty answer would be dishonest)."""
    with driver.session() as session:
        present = {
            r["relation"]
            for r in session.run(_SEED_REL_TYPES, id=query, rels=list(relations))
        }
    return [
        f"requested relation {rel} has no edges on seed '{query}'"
        for rel in relations
        if rel not in present
    ]


def _hop(driver, frontier, visited, relations, depth, budget):
    """One undirected BFS hop. Emits up to ``budget`` new items (deterministic
    order). Returns (items, emitted_ids, truncated)."""
    items, emitted = [], []
    truncated = False
    for rec in _neighbors(driver, frontier, relations):
        nid = rec["id"]
        if nid in visited:
            continue
        if len(items) >= budget:
            truncated = True
            break
        visited.add(nid)
        items.append(_item(rec, rec["relation"], depth))
        emitted.append(nid)
    return items, emitted, truncated


def _result(items, gaps, handle):
    return {
        "items": items,
        # Separate grounding channel (id-keyed), mirrors items — never merged.
        "sources": [{"id": it["id"], **it["sources"]} for it in items],
        "gaps": gaps,
        "confidence": _confidence(items),
        "expand_handle": handle,
    }


def _handle(frontier, visited, next_depth, relations):
    """A pull-handle for the next hop, or None when the frontier is empty."""
    if not frontier:
        return None
    return {
        "frontier": list(frontier),
        "visited": list(visited),
        "depth": next_depth,
        "relations": list(relations),
    }


def recall(driver, query, depth=1, limit=25, relations=None):
    """Progressive, grounded, combinatorial recall from a seed node.

    ``query`` is a node id (symbol ``qualified_name`` or repo-relative path).
    Traverses CALLS / DEPENDS_ON / CONTAINS / IMPORTS up to ``depth`` hops,
    returning at most ``limit`` items. Returns
    ``{items, sources, gaps, confidence, expand_handle}``.
    """
    # A gap is only raised per-relation when the caller *explicitly* narrows the
    # relation set; the default (all four) reports gaps only for an isolated or
    # unresolved seed.
    explicit_relations = relations is not None
    relations = tuple(relations) if relations is not None else DEFAULT_RELATIONS

    seed = _resolve(driver, query)
    if seed is None:
        gap = f"seed '{query}' did not resolve to any node in the graph"
        return _result([], [gap], None)

    seed_item = _item(seed, None, 0)
    items = [seed_item]
    visited = {seed["id"]}
    frontier = [seed["id"]]
    cur_depth = 0
    truncated = False

    while frontier and cur_depth < depth and len(items) < limit:
        cur_depth += 1
        new_items, emitted, truncated = _hop(
            driver, frontier, visited, relations, cur_depth, limit - len(items)
        )
        items.extend(new_items)
        if truncated:
            # Budget spent mid-level: resume from the level we were expanding so
            # the skipped siblings (unvisited neighbours of ``frontier``) are the
            # next hop pulled.
            break
        frontier = emitted

    # More to pull? Either the limit cut a level short, or the depth bound was
    # reached with an outer frontier that still has unvisited neighbours.
    if truncated:
        handle = _handle(frontier, visited, cur_depth, relations)
    elif frontier and _neighbors_beyond(driver, frontier, visited, relations):
        handle = _handle(frontier, visited, cur_depth + 1, relations)
    else:
        handle = None

    gaps = _seed_relation_gaps(driver, query, relations) if explicit_relations else []
    return _result(items, gaps, handle)


def _neighbors_beyond(driver, frontier, visited, relations) -> bool:
    """True if the frontier has at least one still-unvisited neighbour."""
    for rec in _neighbors(driver, frontier, relations):
        if rec["id"] not in visited:
            return True
    return False


def expand(driver, handle, limit=25):
    """Pull the next hop from an ``expand_handle`` returned by :func:`recall`.

    Combinatorial, on-demand continuation: one more BFS hop from the handle's
    frontier, skipping already-seen nodes, bounded by ``limit``. Returns the
    same ``{items, sources, gaps, confidence, expand_handle}`` shape.
    """
    if not handle or not handle.get("frontier"):
        return _result([], ["no frontier to expand"], None)

    relations = tuple(handle.get("relations") or DEFAULT_RELATIONS)
    visited = set(handle["visited"])
    frontier = list(handle["frontier"])
    depth = handle["depth"]

    new_items, emitted, truncated = _hop(
        driver, frontier, visited, relations, depth, limit
    )

    if truncated:
        next_handle = _handle(frontier, visited, depth, relations)
    elif emitted and _neighbors_beyond(driver, emitted, visited, relations):
        next_handle = _handle(emitted, visited, depth + 1, relations)
    else:
        next_handle = None

    return _result(new_items, [], next_handle)
