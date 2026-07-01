"""Command-line entry for palimpsest: extract -> ingest and grounded recall.

Two stdlib-argparse subcommands wire the deterministic slice end to end:

  ingest --repo PATH [--commit HEAD]
      Extract the Java tree under PATH + read git provenance for the pinned
      commit, then create_constraints + ingest the IR into Neo4j.

  query SYMBOL_OR_FILE [--depth N] [--limit M]
      Run bounded, grounded recall from a seed (a symbol qualified_name or a
      repo-relative file path) and print the result as clearly SEPARATED
      sections — items (each with its commit + file:line source), gaps, and
      confidence — never a merged prose answer.

The Neo4j connection comes from the environment (localhost defaults):
  NEO4J_URI (bolt://localhost:7687), NEO4J_USER (neo4j), NEO4J_PASSWORD (neo4j).
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager

from neo4j import GraphDatabase

from palimpsest.extract import extract, read_provenance
from palimpsest.kg import create_constraints, ingest
from palimpsest.recall import recall

DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"
DEFAULT_PASSWORD = "neo4j"


@contextmanager
def _driver():
    uri = os.environ.get("NEO4J_URI", DEFAULT_URI)
    user = os.environ.get("NEO4J_USER", DEFAULT_USER)
    password = os.environ.get("NEO4J_PASSWORD", DEFAULT_PASSWORD)
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        yield driver
    finally:
        driver.close()


def _cmd_ingest(args) -> None:
    prov = read_provenance(args.repo, args.commit)
    ir = extract(args.repo, prov)
    with _driver() as driver:
        create_constraints(driver)
        ingest(driver, ir)
    print(
        f"ingested {len(ir.nodes)} nodes, {len(ir.edges)} edges "
        f"from {args.repo} @ {prov.source_commit}"
    )


def _cmd_query(args) -> None:
    with _driver() as driver:
        result = recall(driver, args.symbol, depth=args.depth, limit=args.limit)
    _print_result(args.symbol, args.depth, args.limit, result)


def _fmt_source(src) -> str:
    commit = src.get("source_commit") or "?"
    path = src.get("path")
    if not path:
        return f"{commit} (no file grounding)"
    return f"{commit} {path}:{src.get('start_line')}-{src.get('end_line')}"


def _print_result(symbol, depth, limit, result) -> None:
    items = result["items"]
    print(f"QUERY: {symbol}  (depth={depth}, limit={limit})")
    print()
    print(f"ITEMS ({len(items)})")
    if not items:
        print("  (none)")
    for it in items:
        role = "seed" if it["relation"] is None else f"via {it['relation']} @depth {it['depth']}"
        print(f"  - {it['kind']} {it['qualified_name'] or it['id']}  [{role}]")
        print(f"      source: {_fmt_source(it['sources'])}")
    print()
    gaps = result["gaps"]
    print(f"GAPS ({len(gaps)})")
    if not gaps:
        print("  (none)")
    for g in gaps:
        print(f"  - {g}")
    print()
    print(f"CONFIDENCE: {result['confidence']}")
    if result.get("expand_handle"):
        frontier = result["expand_handle"].get("frontier", [])
        print(f"MORE: {len(frontier)} frontier node(s) not expanded (bounded)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="palimpsest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser(
        "ingest", help="extract a Java tree + provenance and ingest into Neo4j"
    )
    p_ing.add_argument("--repo", required=True, help="path to the Java repository root")
    p_ing.add_argument(
        "--commit", default="HEAD", help="git commit to pin provenance to (default HEAD)"
    )
    p_ing.set_defaults(func=_cmd_ingest)

    p_q = sub.add_parser(
        "query", help="grounded recall for a symbol or repo-relative file path"
    )
    p_q.add_argument("symbol", help="a symbol qualified_name or repo-relative file path")
    p_q.add_argument("--depth", type=int, default=1, help="max traversal hops (default 1)")
    p_q.add_argument("--limit", type=int, default=25, help="max items (default 25)")
    p_q.set_defaults(func=_cmd_query)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
