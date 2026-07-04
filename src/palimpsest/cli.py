"""palimpsest 커맨드라인 진입점: extract -> ingest 및 근거결박 회상.

stdlib argparse 서브커맨드 둘이 결정론적 슬라이스를 끝에서 끝까지 엮는다:

  ingest --repo PATH [--commit HEAD]
      PATH 아래 Java 트리를 추출하고 pin된 커밋의 git provenance를 읽은 뒤,
      create_constraints + IR을 Neo4j에 ingest한다.

  backfill --repo PATH
      바로 그 extract -> ingest 파이프라인을 전체 git 이력(모든 커밋,
      oldest -> newest)에 재생해, projection이 HEAD만이 아니라 전체 타임라인을
      반영하게 한다. provider-free, 결정론적, 멱등(idempotent); repo는 건드리지 않는다.

  query SYMBOL_OR_FILE [--depth N] [--limit M]
      seed(심볼 qualified_name 또는 repo 상대 파일 경로)에서 bounded·근거결박
      회상을 수행하고, 결과를 명확히 분리된(SEPARATED) 섹션으로 출력한다 —
      items(각각 commit + file:line 출처를 지님), inferred summaries(자기 채널에
      머물며 items에 절대 병합되지 않음), gaps, confidence — 병합된 산문 답이
      아니다.

  load PAYLOAD.json | PAYLOAD_DIR/
      외부에서 생성된 summary JSON payload(각각 summary 객체 배열)를 근거결박·
      summary 단위 적재로 inferred 층에 적재한다. 단일 파일 또는 디렉터리를
      받는다 — 디렉터리가 git으로 추적되는 source-of-truth(git = SoT,
      Neo4j = 재빌드 가능한 projection)이므로, 그 안의 모든 *.json이 일괄 적재되고
      Neo4j를 날려도 load를 다시 돌리면 재빌드된다(결정론적 id 덕에 멱등). 적재된
      건수와 모든(EVERY) 거부 사유를 출력한다 — 거부는 드러나며 조용히 버려지지
      않는다. palimpsest는 어떤 모델도 호출하지 않는다; payload는 외부에서 생성된다.

Neo4j 연결은 환경에서 온다(localhost 기본값):
  NEO4J_URI (bolt://localhost:7687), NEO4J_USER (neo4j), NEO4J_PASSWORD (neo4j).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from neo4j import GraphDatabase

from palimpsest.backfill import backfill
from palimpsest.extract import extract, read_provenance
from palimpsest.ir import Summary
from palimpsest.kg import augment_communities, create_constraints, ingest, load_summaries
from palimpsest.kg.summary import create_vector_index
from palimpsest.recall import recall, recall_churn, recall_cochange
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
    ir = extract(args.repo, prov)
    # 결정론적 Class 수준 Community 분할을 IR에 실체화해, 범용 ingest 라이터가
    # Community 노드 + MEMBER_OF 엣지를 영속화하도록 한다.
    augment_communities(ir, prov)
    with _driver() as driver:
        create_constraints(driver)
        ingest(driver, ir)
    print(
        f"ingested {len(ir.nodes)} nodes, {len(ir.edges)} edges "
        f"from {args.repo} @ {prov.source_commit}"
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


def _cmd_query(args) -> None:
    with _driver() as driver:
        result = recall(driver, args.symbol, depth=args.depth, limit=args.limit)
    _print_result(args.symbol, args.depth, args.limit, result)


def _cmd_reconcile(args) -> int:
    # 명시된 branch 집합이 곧 비교 범위다(ac-6): 이 branch들만 capture되고
    # 비교된다; 명시되지 않은 branch는 쿼리에 절대 들어오지 않는다.
    branches = args.branch or []
    if not branches:
        # N=0: 정의된·정직한 동작 — 크래시가 아니다. 비교 범위를 한정할 branch가
        # 하나도 없으면 비교할 대상 자체가 없다.
        print(
            "reconcile: no branch specified; pass at least one --branch to "
            "define the comparison scope"
        )
        return 2
    with _driver() as driver:
        try:
            # capture()는 branch 집합을 git-safe하게 검증하고(--end-of-options /
            # --verify), dedup하며, shallow repo에서는 fail-closed한다; 잘못된
            # branch나 shallow repo는 예외를 던진다. 여기서 잡아 CLI가 raw Python
            # traceback을 흘리는 대신 정직한 에러를 출력하게 한다.
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
    """peer에 결박된 표시 전용(DISPLAY-ONLY) inferred 층을 간결한
    (verdict, confidence, source) 라인으로 평탄화한다. palimpsest는 이 중 무엇도
    생성하지 않는다 — 외부 judge가 이미 적재한 것을 드러낼 뿐이다. author는 제외."""
    lines = []
    for channel in ("summaries", "risks", "decisions", "relations"):
        for entry in semantic.get(channel, []):
            verdict = entry.get("semantic_verdict")
            has_conf = entry.get("confidence") is not None
            if verdict is None and not has_conf:
                continue
            v = verdict.get("verdict") if isinstance(verdict, dict) else verdict
            src = entry.get("source_commit") or entry.get("code_bound_at")
            lines.append(
                f"verdict={v} confidence={entry.get('confidence')} source={src}"
            )
    return lines


def _print_reconcile(symbol, result) -> None:
    # 분리된(SEPARATED) 섹션(_print_result과 동일 구조): branch별 PEERS, 이어서
    # 계산된 CODE DIVERGENCE, 드러난 CONFLICTS, 그다음 GAPS — 병합된 산문 답이
    # 아니며, 특권을 가진 branch도 없다.
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
    # 디렉터리가 git으로 추적되는 source-of-truth다: 그 안의 모든 *.json payload
    # 파일을 일괄 적재(정렬, 결정론적)해 Neo4j를 날려도 git에서 재빌드할 수 있게
    # 한다. 단일 파일은 원래의 단일 payload 동작을 유지한다.
    if os.path.isdir(args.payload):
        summaries = [
            s for p in sorted(Path(args.payload).glob("*.json"))
            for s in _read_payload_file(p)
        ]
    else:
        summaries = _read_payload_file(args.payload)
    with _driver() as driver:
        result = load_summaries(driver, summaries)
        # load 경로에서 Summary 임베딩 VECTOR INDEX를 마련한다(_cmd_ingest가
        # create_constraints를 부르는 것과 같은 방식): Neo4j를 날린 뒤 다시 load하면
        # git으로 추적되는 payload에서 인덱스가 재생성되므로, 임베딩 + 인덱스가 drop을
        # 견딘다. 멱등(IF NOT EXISTS); ONLINE이 될 때까지 대기한다.
        create_vector_index(driver)
    _print_load_result(args.payload, result)


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
    # MODIFIES 채널마다 분리된(SEPARATE) 섹션(_print_result과 동일 구조): 순위가
    # 매겨진 Files(각각 commit + file:line 출처와 순위를 정한 count를 지님), 이어서
    # GAPS — ``(none)`` 빈 경우까지 두어, 확신에 찬 빈 답을 절대 내지 않는다.
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

    p_bf = sub.add_parser(
        "backfill",
        help="replay extract+ingest over the FULL git history (every commit)",
    )
    p_bf.add_argument("--repo", required=True, help="path to the Java repository root")
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
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # 기존 서브커맨드는 None을 반환한다(-> 0, byte 단위 동일); reconcile은 명시적
    # exit code를 반환해 정직한 거부(잘못된/누락된 branch)가 0이 아니게 한다.
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
