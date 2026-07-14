"""Command-line entry for palimpsest: extract -> ingest and grounded recall.

Two stdlib-argparse subcommands wire the deterministic slice end to end:

  ingest --repo PATH [--commit HEAD]
      Extract the source tree under PATH (every language present — Java/Kotlin/
      Python/Rust/ECMAScript, routed by extension) + read git provenance for the pinned
      commit, then create_constraints + ingest the IR into Neo4j.

  backfill --repo PATH
      Replay that same extract -> ingest over the FULL git history (every commit,
      oldest -> newest) so the projection reflects the whole timeline, not just
      HEAD. Provider-free, deterministic, idempotent; leaves the repo untouched.

  query SYMBOL_OR_FILE [--depth N] [--limit M]
      Run bounded, grounded recall from a seed (a symbol qualified_name or a
      repo-relative file path) and print the result as clearly SEPARATED
      sections — items (each with its commit + file:line source), inferred
      summaries (in their own channel, never merged into items), gaps, and
      confidence — never a merged prose answer.

  load PAYLOAD.json | PAYLOAD_DIR/
      Load externally-produced summary JSON payloads (each an array of summary
      objects) into the inferred layer via grounded, summary-atomic load. Accepts
      a single file OR a DIRECTORY — the directory is the git-tracked
      source-of-truth (git = SoT, Neo4j = re-buildable projection), so every
      *.json inside it is batch-loaded and a dropped Neo4j can be rebuilt by
      re-running load (deterministic ids make it idempotent). Prints the loaded
      count and EVERY rejection reason — rejections are surfaced, never silently
      dropped. palimpsest calls no model; payloads are generated elsewhere.

The Neo4j connection comes from the environment (localhost defaults):
  NEO4J_URI (bolt://localhost:7687), NEO4J_USER (neo4j), NEO4J_PASSWORD (neo4j).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from neo4j import GraphDatabase

from palimpsest.backfill import backfill
from palimpsest.extract import dispatch, read_provenance
from palimpsest.extract.proxy_config import read_proxy_rewrite
from palimpsest.ir import Summary
from palimpsest.kg import augment_communities, create_constraints, ingest, load_summaries
from palimpsest.kg.calls_api import load_calls_api
from palimpsest.kg.coverage import CoverageRecord, load_coverage
from palimpsest.kg.summary import create_vector_index, summary_id
from palimpsest.recall import (
    recall,
    recall_churn,
    recall_cochange,
    recall_test_impact,
    recall_changeset_impact,
    recall_runtime_test_impact,
    recall_edge_precision,
    recall_callgraph_locality,
    recall_refactor_candidates,
)
from palimpsest.recall.graphrag import reconcile_recall
from palimpsest.reconcile import capture

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
    ir = dispatch(args.repo, prov)
    # Materialize the deterministic Class-level Community partition into the IR so
    # the generic ingest writers persist the Community nodes + MEMBER_OF edges.
    augment_communities(ir, prov)
    with _driver() as driver:
        create_constraints(driver)
        ingest(driver, ir)
        # Post-ingest inferred layer: compute + MERGE the cross-tier CALLS_API edges
        # (front-end ApiCall -> back-end/f-e Endpoint by canonical route). It queries
        # the just-ingested deterministic nodes, so it runs AFTER ingest — via the
        # dedicated inferred loader, never the generic edge writer (Frozen Invariant 3).
        # The repo's dev-proxy rewrite (mechanism A) is read host-neutrally from the
        # repo root and threaded in so a front-end /api call resolves to the back-end
        # route through the proxy (wi_260713iah part 4).
        calls_api = load_calls_api(driver, proxy=read_proxy_rewrite(args.repo))
    print(
        f"ingested {len(ir.nodes)} nodes, {len(ir.edges)} edges "
        f"from {args.repo} @ {prov.source_commit}"
    )
    print(
        f"linked {calls_api.loaded} CALLS_API cross-tier edge(s) "
        f"({calls_api.api_calls} ApiCall x {calls_api.endpoints} Endpoint)"
    )


def _cmd_backfill(args) -> None:
    with _driver() as driver:
        result = backfill(driver, args.repo)
    print(
        f"backfilled {result.commits} commits from {args.repo} "
        f"({result.nodes} nodes, {result.edges} edges at HEAD, "
        f"{result.modifies} MODIFIES edges)"
    )


def _cmd_churn(args) -> None:
    with _driver() as driver:
        result = recall_churn(driver, limit=args.limit)
    _print_channel("CHURN", "churn", result, args.limit)


def _cmd_cochange(args) -> None:
    with _driver() as driver:
        result = recall_cochange(driver, args.file, limit=args.limit)
    _print_channel("CO-CHANGE", "cochange", result, args.limit, seed=args.file)


def _cmd_test_impact(args) -> None:
    with _driver() as driver:
        result = recall_test_impact(
            driver, args.method_id, depth=args.depth, limit=args.limit
        )
    _print_test_impact(args.method_id, args.depth, args.limit, result)


def _cmd_edge_precision(args) -> None:
    with _driver() as driver:
        result = recall_edge_precision(driver, limit=args.limit)
    # The shared channel printer: ``resolution`` is the per-item field it prints,
    # so each low-precision edge shows ``[resolution=name]`` beside its grounding.
    _print_channel("EDGE-PRECISION", "resolution", result, args.limit)


def _cmd_call_locality(args) -> None:
    with _driver() as driver:
        result = recall_callgraph_locality(driver, limit=args.limit)
    # ``cross_ratio`` is the per-item scalar the shared printer shows — always a real
    # float (0/0 guarded to 0.0), so it never prints ``[cross_ratio=None]``.
    _print_channel("CALL-LOCALITY", "cross_ratio", result, args.limit)


def _cmd_changeset_impact(args) -> int:
    # Require at least one seed input — a friendly error (exit 2), never a confident
    # empty run. The channel itself also reports an empty changeset as a gap; this is
    # the CLI-surface guard so the user learns what to pass.
    if not (args.file or args.method or args.commit):
        print(
            "changeset-impact: no seed; pass at least one of --file / --method / --commit"
        )
        return 2
    with _driver() as driver:
        result = recall_changeset_impact(
            driver, files=args.file, methods=args.method, commit=args.commit,
            depth=args.depth, limit=args.limit,
        )
    _print_changeset_impact(
        args.file, args.method, args.commit, args.depth, args.limit, result
    )
    return 0


def _cmd_runtime_test_impact(args) -> None:
    # The runtime-coverage OVERLAY of test-impact (ADR-20260714): reads the
    # COVERS(edge_kind='runtime') edges to surface the tests OBSERVED at runtime to cover a
    # production Method — the AUGMENT that catches the reflection/DI/polymorphic callers static
    # CALLS misses. A SEPARATE channel from `test-impact`; provenance-distinct (relation=COVERS).
    with _driver() as driver:
        result = recall_runtime_test_impact(driver, args.method_id, limit=args.limit)
    _print_runtime_test_impact(args.method_id, args.limit, result)


def _cmd_query(args) -> None:
    with _driver() as driver:
        result = recall(driver, args.symbol, depth=args.depth, limit=args.limit)
    _print_result(args.symbol, args.depth, args.limit, result)


def _cmd_reconcile(args) -> int:
    # The explicit branch set IS the comparison scope (ac-6): only these branches
    # are captured + compared; unspecified branches never enter the query.
    branches = args.branch or []
    if not branches:
        # N=0: defined, honest behavior — never a crash. There is nothing to
        # compare without at least one branch to scope the comparison to.
        print(
            "reconcile: no branch specified; pass at least one --branch to "
            "define the comparison scope"
        )
        return 2
    with _driver() as driver:
        try:
            # capture() validates the branch set git-safely (--end-of-options /
            # --verify), dedups, and fails closed on a shallow repo; a bad branch
            # or shallow repo raises. Catch it here so the CLI prints an honest
            # error instead of leaking a raw Python traceback.
            capture(driver, args.repo, branches)
            result = reconcile_recall(
                driver, args.symbol, branches, limit=args.limit
            )
        except (ValueError, RuntimeError) as exc:
            print(f"reconcile error: {exc}")
            return 2
    _print_reconcile(args.symbol, result)
    return 0


def _semantic_annotations(semantic) -> list[str]:
    """Flatten the DISPLAY-ONLY inferred layer bound to a peer into terse
    (verdict, coverage, confidence, source) lines. palimpsest generates none of it —
    this only surfaces what an external judge already loaded. Author-omitted.

    ``coverage`` (the summaries-only code->claim completeness axis) rides alongside
    the faithfulness ``verdict`` and is shown only when a coverage_verdict is present,
    so a coverage-only entry still surfaces (guard below) without adding noise to the
    other channels that never carry one."""
    lines = []
    for channel in ("summaries", "risks", "decisions", "relations"):
        for entry in semantic.get(channel, []):
            verdict = entry.get("semantic_verdict")
            coverage = entry.get("coverage_verdict")  # summaries-only axis
            has_conf = entry.get("confidence") is not None
            if verdict is None and coverage is None and not has_conf:
                continue
            v = verdict.get("verdict") if isinstance(verdict, dict) else verdict
            src = entry.get("source_commit") or entry.get("code_bound_at")
            cov = ""
            if coverage is not None:
                cv = coverage.get("verdict") if isinstance(coverage, dict) else coverage
                cov = f" coverage={cv}"
            lines.append(
                f"verdict={v}{cov} confidence={entry.get('confidence')} source={src}"
            )
    return lines


def _print_reconcile(symbol, result) -> None:
    # SEPARATED sections (mirrors _print_result): per-branch PEERS, then the
    # computed CODE DIVERGENCE, then the surfaced CONFLICTS, then GAPS — never a
    # merged prose answer, and no privileged branch.
    peers = result["peers"]
    branches = result["branches"]
    print(f"RECONCILE: {symbol}  (branches={', '.join(branches)})")
    print()
    print(f"PEERS ({len(peers)})")
    if not peers:
        print("  (none)")
    for p in peers:
        rank = "freshest" if p["freshest"] else "older"
        print(f"  - [{p['branch']}] {p['qualified_name'] or p['id']}  ({rank})")
        print(f"      source: {_fmt_source(p['sources'])}")
        for ann in _semantic_annotations(p["semantic"]):
            print(f"      semantic: {ann}")
    print()
    div = result["code_divergence"]
    print(f"CODE DIVERGENCE: diverged={div['diverged']}")
    for c in div["source_commits"]:
        print(f"  - {c}")
    print()
    conflicts = result["conflict_edges"]
    print(f"CONFLICTS ({len(conflicts)})")
    if not conflicts:
        print("  (none)")
    for e in conflicts:
        print(f"  - {e['source_id']} CONFLICTS_WITH {e['target_id']}")
    print()
    gaps = result["gaps"]
    print(f"GAPS ({len(gaps)})")
    if not gaps:
        print("  (none)")
    for g in gaps:
        print(f"  - {g}")


def _read_payload_file(path) -> list:
    with open(path, encoding="utf-8") as f:
        return [Summary.from_dict(d) for d in json.load(f)]


def _cmd_load(args) -> None:
    # A DIRECTORY is the git-tracked source-of-truth: batch-load every *.json
    # payload file inside it (sorted, deterministic) so a Neo4j drop can be
    # rebuilt from git. A single file keeps the original one-payload behavior.
    if os.path.isdir(args.payload):
        summaries = [
            s for p in sorted(Path(args.payload).glob("*.json"))
            for s in _read_payload_file(p)
        ]
    else:
        summaries = _read_payload_file(args.payload)
    with _driver() as driver:
        result = load_summaries(driver, summaries)
        # Provision the Summary embedding VECTOR INDEX on the load path (mirrors
        # how _cmd_ingest calls create_constraints): a reload after a Neo4j drop
        # re-creates the index from the git-tracked payload, so embeddings +
        # index survive a drop. Idempotent (IF NOT EXISTS); awaits ONLINE.
        create_vector_index(driver)
    _print_load_result(args.payload, result)


def _payload_path(outdir: Path, request) -> Path:
    """The deterministic materialisation path for a request: ``<out>/<id>.json``. The
    id is the provenance-keyed summary_id (shared with the loader); the ``summary:``
    prefix is dropped for a filesystem-safe name (the id itself is unchanged in the
    graph)."""
    sid = summary_id(request.target_id, request.generator, request.model, request.source_commit)
    return outdir / f"{sid.split(':', 1)[1]}.json"


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` ATOMICALLY (temp file in the same dir + os.replace)
    so a crash mid-write never leaves a TORN file that the directory batch-loader would
    then ingest as corrupt JSON. The temp is removed on any failure."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _materialize_idempotent(path: Path, content: str) -> None:
    """Freeze ``content`` to ``path`` idempotently: if the file already exists for this
    (provenance-keyed) id, re-materialising is a no-op when identical and a REFUSAL when
    the content diverges — the LLM output is frozen to git-SoT, so a re-run must not
    silently rewrite a summary under the same provenance. First write is atomic."""
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return  # identical -> frozen, no-op
        raise ValueError(
            f"refusing to overwrite {path} with DIVERGENT content under the same "
            f"provenance (summary_id is provenance-keyed; the frozen LLM output must "
            f"not change on re-run) — delete the file to regenerate"
        )
    _atomic_write_text(path, content)


def _read_coverage_file(path) -> list:
    with open(path, encoding="utf-8") as f:
        return [CoverageRecord.from_dict(d) for d in json.load(f)]


def _print_coverage_load_result(payload, result) -> None:
    print(f"LOAD-COVERAGE: {payload}")
    print(
        f"  records={result.intended}  covers_edges={result.loaded}  "
        f"rejected={result.rejected}"
    )
    # Rejections / unresolved targets are SURFACED, never silently dropped (ac-3 honesty).
    print(f"  reasons ({len(result.reasons)})")
    if not result.reasons:
        print("    (none)")
    for r in result.reasons:
        print(f"    - {r}")


def _cmd_load_coverage(args) -> None:
    # The runtime-coverage overlay load (ADR-20260714): a DIRECTORY is the git-tracked
    # source-of-truth (mirrors `load`), each *.json an array of per-test coverage records
    # ({test_qualified_name, covered[], source_commit}) produced OUTSIDE palimpsest. Materialises
    # COVERS(edge_kind='runtime') edges against the CURRENT HEAD projection — run this AFTER
    # `ingest`, NEVER inside `backfill` (HEAD-only isolation). palimpsest runs no build.
    if os.path.isdir(args.payload):
        records = [
            r for p in sorted(Path(args.payload).glob("*.json"))
            for r in _read_coverage_file(p)
        ]
    else:
        records = _read_coverage_file(args.payload)
    with _driver() as driver:
        result = load_coverage(driver, records)
    _print_coverage_load_result(args.payload, result)


def _cmd_curate(args) -> None:
    # The isolated generative producer is imported LAZILY here (never at cli
    # module load) so `import palimpsest.cli` stays outside curate's import
    # closure — the path-scoped provider-free probes keep passing. The CLI, not
    # the producer, owns materialisation: it derives the deterministic id (shared
    # with the loader) and freezes the payload to git-SoT. Loading stays with the
    # existing `load` subcommand.
    from palimpsest.curate import CurateRequest, default_generate, produce

    request = CurateRequest(
        target_id=args.target,
        grounding_ids=tuple(args.ground),
        facts=args.facts,
        source_commit=args.source_commit,
        created_at=args.created_at,
        generator=args.generator,
        model=args.model,
    )
    # The model the payload records for provenance is the model actually invoked.
    payload = produce(
        request, generate=lambda prompt: default_generate(prompt, model=request.model)
    )

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    # The loader reads a directory of JSON ARRAYS (cli._read_payload_file), so the
    # materialised file is a one-element array, frozen atomically + idempotently.
    path = _payload_path(outdir, request)
    _materialize_idempotent(path, json.dumps([payload], ensure_ascii=False, indent=2))
    print(f"CURATED {path} (target={request.target_id}, model={request.model})")


def _cmd_refactor_candidates(args) -> int:
    # facet-3 composite flow: the recall-side deterministic composite SELECTS the
    # candidates (provider-free, id-ordered LIMIT — the LLM never selects, F-Q6),
    # then the isolated producer SYNTHESISES a grounded observation per pre-selected
    # tuple, materialised atomically + idempotently to git-SoT. Loading stays with
    # `load`. The producer is imported LAZILY (isolation probe stays green).
    from palimpsest.curate import CurateRequest, default_generate, produce

    with _driver() as driver:
        result = recall_refactor_candidates(driver, limit=args.max)
    items = result["items"]
    if not items:
        # Honest empty: print the gaps so a 0-candidate run is never a silent
        # all-clear (a precision-resolved corpus legitimately has no composite).
        print("REFACTOR-CANDIDATES: 0 composite candidates")
        for gap in result["gaps"]:
            print(f"  gap: {gap}")
        return 0

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    written = 0
    for it in items:
        request = CurateRequest(
            target_id=it["id"],
            grounding_ids=tuple(it["grounding_ids"]),
            facts=it["facts"],
            # The commit summarised is the candidate's OWN grounded commit (honest
            # provenance), not a caller-supplied one.
            source_commit=it["sources"]["source_commit"],
            created_at=args.created_at,
            generator=args.generator,
            model=args.model,
        )
        payload = produce(
            request, generate=lambda prompt: default_generate(prompt, model=request.model)
        )
        path = _payload_path(outdir, request)
        _materialize_idempotent(path, json.dumps([payload], ensure_ascii=False, indent=2))
        written += 1
        print(f"CURATED {path} (target={request.target_id})")
    print(f"REFACTOR-CANDIDATES: {written} materialised into {outdir}")
    return 0


def _print_load_result(path, result) -> None:
    print(f"LOADED {result.loaded}/{result.intended} summaries from {path}")
    print(f"REJECTED ({result.rejected})")
    for rej in result.rejections:
        print(f"  - {rej.summary_id}: {rej.reason}")


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
    summaries = result.get("summaries", [])
    print(f"SUMMARIES ({len(summaries)})")
    if not summaries:
        print("  (none)")
    for s in summaries:
        print(f"  - inferred summary of {s['target_id']}  [bound {s['code_bound_at']}]")
        for c in s["claims"]:
            refs = ", ".join(c.get("source_refs", []))
            print(f"      {c['text']}  (grounds: {refs})")
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


def _print_channel(title, count_key, result, limit, seed=None) -> None:
    # A SEPARATE section per MODIFIES channel (mirrors _print_result): the ranked
    # Files (each with its commit + file:line source and the count that ranked it),
    # then the GAPS — with a ``(none)`` empty case, never a confident empty answer.
    items = result["items"]
    head = f"{title}: {seed}  (limit={limit})" if seed else f"{title}  (limit={limit})"
    print(head)
    print()
    print(f"{title} ({len(items)})")
    if not items:
        print("  (none)")
    for it in items:
        n = it.get(count_key)
        print(f"  - {it['kind']} {it['qualified_name'] or it['id']}  [{count_key}={n}]")
        print(f"      source: {_fmt_source(it['sources'])}")
    print()
    gaps = result["gaps"]
    print(f"GAPS ({len(gaps)})")
    if not gaps:
        print("  (none)")
    for g in gaps:
        print(f"  - {g}")


def _print_test_impact(method_id, depth, limit, result) -> None:
    # SEPARATE sections (mirrors _print_result/_print_channel): the grounded test-caller
    # Methods, then the GAPS (always the static-lower-bound note — completeness is never
    # claimed on this channel), then CONFIDENCE — never a merged prose answer.
    items = result["items"]
    print(f"TEST-IMPACT: {method_id}  (depth={depth}, limit={limit})")
    print()
    print(f"TEST CALLERS ({len(items)})")
    if not items:
        print("  (none)")
    for it in items:
        print(
            f"  - {it['kind']} {it['qualified_name'] or it['id']}  "
            f"[via CALLS @depth {it['depth']}]"
        )
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


def _print_changeset_impact(files, methods, commit, depth, limit, result) -> None:
    # SEPARATE sections (mirrors _print_test_impact): the changeset seed inputs, the grounded
    # impacted test-caller Methods, then the GAPS (always the static-lower-bound note; the
    # over-approx note when file/commit seeds were used), then CONFIDENCE — never a merged
    # prose answer.
    items = result["items"]
    seeds = []
    if files:
        seeds.append(f"files={list(files)}")
    if methods:
        seeds.append(f"methods={list(methods)}")
    if commit:
        seeds.append(f"commit={commit}")
    print(f"CHANGESET-IMPACT: {', '.join(seeds)}  (depth={depth}, limit={limit})")
    print()
    print(f"IMPACTED TEST CALLERS ({len(items)})")
    if not items:
        print("  (none)")
    for it in items:
        print(
            f"  - {it['kind']} {it['qualified_name'] or it['id']}  "
            f"[via CALLS @depth {it['depth']}]"
        )
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


def _print_runtime_test_impact(method_id, limit, result) -> None:
    # SEPARATE sections (mirrors _print_changeset_impact): the runtime overlay's covering test
    # Methods, tagged [via COVERS] to keep them provenance-distinct from the static [via CALLS]
    # channel, then the GAPS (always the opt-in/HEAD-only disclosure), then CONFIDENCE.
    items = result["items"]
    print(f"RUNTIME-TEST-IMPACT: {method_id}  (limit={limit})")
    print()
    print(f"RUNTIME-COVERING TESTS ({len(items)})")
    if not items:
        print("  (none)")
    for it in items:
        print(
            f"  - {it['kind']} {it['qualified_name'] or it['id']}  "
            f"[via COVERS, edge_kind={it.get('edge_kind')}]"
        )
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="palimpsest", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser(
        "ingest", help="extract a source tree (any language) + provenance and ingest into Neo4j"
    )
    p_ing.add_argument("--repo", required=True, help="path to the repository root")
    p_ing.add_argument(
        "--commit", default="HEAD", help="git commit to pin provenance to (default HEAD)"
    )
    p_ing.set_defaults(func=_cmd_ingest)

    p_bf = sub.add_parser(
        "backfill",
        help="replay extract+ingest over the FULL git history (every commit)",
    )
    p_bf.add_argument("--repo", required=True, help="path to the repository root")
    p_bf.set_defaults(func=_cmd_backfill)

    p_q = sub.add_parser(
        "query", help="grounded recall for a symbol or repo-relative file path"
    )
    p_q.add_argument("symbol", help="a symbol qualified_name or repo-relative file path")
    p_q.add_argument("--depth", type=int, default=1, help="max traversal hops (default 1)")
    p_q.add_argument("--limit", type=int, default=25, help="max items (default 25)")
    p_q.set_defaults(func=_cmd_query)

    p_load = sub.add_parser(
        "load",
        help="load an externally-produced summary JSON payload (provider-free)",
    )
    p_load.add_argument(
        "payload",
        help="a JSON file (array of summary objects) OR a directory of such "
        "files — the git-tracked source-of-truth to rebuild Neo4j from",
    )
    p_load.set_defaults(func=_cmd_load)

    p_cur = sub.add_parser(
        "curate",
        help="generate a grounded summary payload (grounding+gap+confidence) with "
        "the isolated generative producer and MATERIALISE it to git-SoT "
        "(loading is left to `load` — this is opt-in, LLM-using, provider-gated)",
    )
    p_cur.add_argument("--target", required=True, help="node id being summarised (SUMMARIZES anchor)")
    p_cur.add_argument(
        "--ground", action="append", required=True, metavar="ID",
        help="a real node id a claim may ground in; repeat for the candidate set",
    )
    p_cur.add_argument("--facts", required=True, help="the code/KB facts the model summarises")
    p_cur.add_argument("--generator", required=True, help="the producing tool/agent (not palimpsest)")
    p_cur.add_argument("--model", required=True, help="the actual generation model (not palimpsest)")
    p_cur.add_argument("--source-commit", required=True, dest="source_commit", help="code commit summarised against")
    p_cur.add_argument("--created-at", required=True, dest="created_at", help="external generation time (ISO-8601)")
    p_cur.add_argument("--out", required=True, help="git-tracked summaries directory to materialise into")
    p_cur.set_defaults(func=_cmd_curate)

    p_rc = sub.add_parser(
        "refactor-candidates",
        help="composite facet-3: the recall-side deterministic composite (low-precision "
        "AND low-locality Java Methods) SELECTS candidates, the isolated producer "
        "SYNTHESISES a grounded observation per tuple, materialised atomically + "
        "idempotently to git-SoT (LLM never selects; loading is left to `load`)",
    )
    p_rc.add_argument("--generator", required=True, help="the producing tool/agent id (not a person's name — PII; not palimpsest)")
    p_rc.add_argument("--model", required=True, help="the actual generation model (not palimpsest)")
    p_rc.add_argument("--created-at", required=True, dest="created_at", help="external generation time (ISO-8601)")
    p_rc.add_argument("--out", required=True, help="git-tracked summaries directory to materialise into")
    p_rc.add_argument("--max", type=int, default=25, dest="max", help="max candidates (composite LIMIT + LLM fan-out cap, default 25)")
    p_rc.set_defaults(func=_cmd_refactor_candidates)

    p_rec = sub.add_parser(
        "reconcile",
        help="N-way branch comparison for one symbol (capture then compare "
        "branch-scoped peers as equals, sections kept separate)",
    )
    p_rec.add_argument("symbol", help="the target symbol qualified_name to compare")
    p_rec.add_argument(
        "--branch", action="append", metavar="BRANCH",
        help="a branch in the comparison scope; repeat for N branches. The "
        "explicit set IS the scope — unspecified branches are excluded.",
    )
    p_rec.add_argument("--repo", required=True, help="path to the git repository to capture")
    p_rec.add_argument("--limit", type=int, default=25, help="max peers per branch (default 25)")
    p_rec.set_defaults(func=_cmd_reconcile)

    p_churn = sub.add_parser(
        "churn",
        help="the churn hotspots — Files ranked by how many commits touched them",
    )
    p_churn.add_argument("--limit", type=int, default=25, help="max hotspots (default 25)")
    p_churn.set_defaults(func=_cmd_churn)

    p_cc = sub.add_parser(
        "cochange",
        help="Files co-changed with a File (touched by the same commits)",
    )
    p_cc.add_argument("file", help="a repo-relative File path (a File node id)")
    p_cc.add_argument("--limit", type=int, default=25, help="max co-changed files (default 25)")
    p_cc.set_defaults(func=_cmd_cochange)

    p_ti = sub.add_parser(
        "test-impact",
        help="test Methods that transitively call a production Method (backward CALLS)",
    )
    p_ti.add_argument("method_id", help="a production Method node id (the changed seed)")
    p_ti.add_argument("--depth", type=int, default=10, help="transitive-hop ceiling (default 10)")
    p_ti.add_argument("--limit", type=int, default=25, help="max test callers (default 25)")
    p_ti.set_defaults(func=_cmd_test_impact)

    p_ep = sub.add_parser(
        "edge-precision",
        help="name-resolved (low-precision) Java CALLS / DEPENDS_ON edges (resolution='name')",
    )
    p_ep.add_argument("--limit", type=int, default=25, help="max flagged edges (default 25)")
    p_ep.set_defaults(func=_cmd_edge_precision)

    p_cl = sub.add_parser(
        "call-locality",
        help="Java callers by cross-package CALLS locality (typed cross/same/unresolved triple)",
    )
    p_cl.add_argument("--limit", type=int, default=25, help="max flagged callers (default 25)")
    p_cl.set_defaults(func=_cmd_call_locality)

    p_ci = sub.add_parser(
        "changeset-impact",
        help="test Methods impacted by a CHANGESET (changed files / methods / a commit) "
        "via backward CALLS",
    )
    p_ci.add_argument(
        "--file", action="append", metavar="PATH",
        help="a repo-relative changed File path; repeat for several (file-granularity)",
    )
    p_ci.add_argument(
        "--method", action="append", metavar="ID",
        help="a changed production Method id / qualified_name; repeat for several",
    )
    p_ci.add_argument(
        "--commit", help="a commit id whose MODIFIED files seed the changeset (file-granularity)",
    )
    p_ci.add_argument("--depth", type=int, default=10, help="transitive-hop ceiling (default 10)")
    p_ci.add_argument("--limit", type=int, default=25, help="max impacted test callers (default 25)")
    p_ci.set_defaults(func=_cmd_changeset_impact)

    p_lc = sub.add_parser(
        "load-coverage",
        help="load an external producer's per-test coverage payload as HEAD-only "
        "COVERS(edge_kind='runtime') edges (opt-in overlay; palimpsest runs no build)",
    )
    p_lc.add_argument(
        "payload",
        help="a JSON file (array of {test_qualified_name, covered[], source_commit}) OR a "
        "directory of such files — the git-tracked source-of-truth to rebuild Neo4j from",
    )
    p_lc.set_defaults(func=_cmd_load_coverage)

    p_rti = sub.add_parser(
        "runtime-test-impact",
        help="tests OBSERVED at runtime to cover a production Method (backward COVERS) — "
        "the runtime overlay that AUGMENTS test-impact, provenance-distinct from static CALLS",
    )
    p_rti.add_argument("method_id", help="a production Method node id")
    p_rti.add_argument("--limit", type=int, default=25, help="max covering tests (default 25)")
    p_rti.set_defaults(func=_cmd_runtime_test_impact)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # Existing subcommands return None (-> 0, byte-identical); reconcile returns an
    # explicit exit code so honest rejections (bad/missing branch) are non-zero.
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
