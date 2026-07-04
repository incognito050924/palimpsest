"""Java 소스를 palimpsest IR로 정적 추출한다.

파서: tree-sitter-java (py-tree-sitter). 결정론적 구조 온톨로지만 다룬다.
CALLS는 단순명 기반 best-effort(전체 타입 해석 없음)이고, Lombok이 생성한
멤버는 소스 파서에 보이지 않는다 — 둘 다 v1에서는 수용 가능하다.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node as TSNode

from palimpsest.ir import IR, Node, Edge, Provenance
from palimpsest.ir import REPO, PACKAGE, FILE, CLASS, METHOD
from palimpsest.ir import CONTAINS, IMPORTS, CALLS, DEPENDS_ON

_TYPE_DECLS = ("class_declaration", "interface_declaration", "enum_declaration", "record_declaration")
_METHOD_DECLS = ("method_declaration", "constructor_declaration")

_LANGUAGE = Language(tsjava.language())


def _parser() -> Parser:
    return Parser(_LANGUAGE)


def _line(point) -> int:
    return point[0] + 1


def _simple_type_name(node: TSNode | None) -> str | None:
    """기저(비수식·비제네릭) 참조 타입명, 원시 타입이면 None."""
    if node is None:
        return None
    t = node.type
    if t == "generic_type":
        return _simple_type_name(node.named_children[0]) if node.named_children else None
    if t == "scoped_type_identifier":
        return node.text.decode().split("<")[0].split(".")[-1]
    if t == "type_identifier":
        return node.text.decode()
    if t == "array_type":
        return _simple_type_name(node.child_by_field_name("element"))
    if t == "annotated_type":
        for c in reversed(node.named_children):
            r = _simple_type_name(c)
            if r:
                return r
        return None
    # void_type / boolean_type / integral_type / floating_point_type / ...
    return None  # 원시 타입은 참조 타입명이 없다


def _param_types(method: TSNode) -> list[str]:
    params = method.child_by_field_name("parameters")
    out: list[str] = []
    if params is None:
        return out
    for fp in params.named_children:
        if fp.type not in ("formal_parameter", "spread_parameter"):
            continue
        t = fp.child_by_field_name("type")
        name = _simple_type_name(t)
        if name is None and t is not None:
            name = t.text.decode()  # 원시 타입 (boolean, int, void, ...)
        out.append(name or "?")
    return out


def _package_fqn(root: TSNode) -> str:
    for c in root.named_children:
        if c.type == "package_declaration":
            return c.named_children[0].text.decode()
    return ""


class _FileWalker:
    """파싱된 Java 파일 하나에서 노드 + 엣지를 수집한다."""

    def __init__(self, rel_path: str, source: bytes, root: TSNode, prov: Provenance):
        self.rel_path = rel_path
        self.source = source
        self.root = root
        self.prov = prov
        self.pkg = _package_fqn(root)
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        # class fqn -> tree-sitter body 노드 (이후 엣지 슬라이스용)
        self.class_bodies: dict[str, TSNode] = {}
        # class fqn -> 참조된 단순 타입명 (필드 + 파라미터 + import)
        self.class_refs: dict[str, set[str]] = {}
        # 이 파일의 단일 타입 import들의 단순명 (파일 내 클래스에 귀속)
        self.import_simple: set[str] = set()
        self.file_classes: list[str] = []
        # method fqn -> 본문에서 호출한 단순명 (단순명 기반 CALLS용)
        self.method_calls: dict[str, set[str]] = {}

    def _edge(self, kind: str, src: str, dst: str) -> None:
        self.edges.append(Edge(kind=kind, src=src, dst=dst, provenance=self.prov))

    def run(self) -> None:
        total_lines = self.source.count(b"\n") + 1
        file_node = Node(
            kind=FILE,
            qualified_name=self.rel_path,
            name=Path(self.rel_path).name,
            provenance=self.prov,
            path=self.rel_path,
            start_line=1,
            end_line=total_lines,
        )
        self.nodes.append(file_node)
        # Package -> File
        if self.pkg:
            self._edge(CONTAINS, self.pkg, self.rel_path)
        # import + 최상위 타입 선언
        for child in self.root.named_children:
            if child.type == "import_declaration":
                self._import_decl(child)
            elif child.type in _TYPE_DECLS:
                self._type_decl(child, enclosing_fqn=None, container_id=self.rel_path)
        # import된 타입은 이 파일에 선언된 모든 클래스의 의존으로 친다
        for fqn in self.file_classes:
            self.class_refs[fqn].update(self.import_simple)

    def _import_decl(self, node: TSNode) -> None:
        target = None
        wildcard = any(c.type == "asterisk" for c in node.children)
        for c in node.named_children:
            if c.type in ("scoped_identifier", "identifier"):
                target = c.text.decode()
        if target:
            # File이 참조된 수식명을 IMPORTS한다 (단일 타입 import면 Class,
            # `a.b.*`면 Package). 해석은 node-id 매칭으로 한다.
            self._edge(IMPORTS, self.rel_path, target)
            if not wildcard:
                self.import_simple.add(target.split(".")[-1])

    def _type_decl(self, node: TSNode, enclosing_fqn: str | None, container_id: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        if enclosing_fqn:
            fqn = f"{enclosing_fqn}.{name}"
        elif self.pkg:
            fqn = f"{self.pkg}.{name}"
        else:
            fqn = name
        self.nodes.append(
            Node(
                kind=CLASS,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
            )
        )
        # 컨테이너(File 또는 감싸는 Class)가 이 Class를 CONTAINS한다
        self._edge(CONTAINS, container_id, fqn)
        self.file_classes.append(fqn)
        self.class_refs.setdefault(fqn, set())

        body = node.child_by_field_name("body")
        if body is None:
            return
        self.class_bodies[fqn] = body
        for member in body.named_children:
            if member.type in _METHOD_DECLS:
                self._method_decl(member, class_fqn=fqn)
            elif member.type in _TYPE_DECLS:
                self._type_decl(member, enclosing_fqn=fqn, container_id=fqn)
            elif member.type == "field_declaration":
                ref = _simple_type_name(member.child_by_field_name("type"))
                if ref:
                    self.class_refs[fqn].add(ref)

    def _method_decl(self, node: TSNode, class_fqn: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = name_node.text.decode()
        param_type_names = _param_types(node)
        params = ",".join(param_type_names)
        fqn = f"{class_fqn}#{name}({params})"
        # 파라미터 타입은 선언한 클래스의 의존이다
        for ref in param_type_names:
            if ref and ref != "?":
                self.class_refs.setdefault(class_fqn, set()).add(ref)
        self.nodes.append(
            Node(
                kind=METHOD,
                qualified_name=fqn,
                name=name,
                provenance=self.prov,
                path=self.rel_path,
                start_line=_line(node.start_point),
                end_line=_line(node.end_point),
            )
        )
        self._edge(CONTAINS, class_fqn, fqn)
        self.method_calls[fqn] = _collect_call_names(node)


def _collect_call_names(method: TSNode) -> set[str]:
    """``method`` 서브트리 어디에서든 호출된 단순 메서드명들."""
    names: set[str] = set()
    stack = [method]
    while stack:
        n = stack.pop()
        if n.type == "method_invocation":
            nm = n.child_by_field_name("name")
            if nm is not None:
                names.add(nm.text.decode())
        stack.extend(n.children)
    return names


def _index_by_simple_name(nodes: list[Node], kind: str) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = {}
    for n in nodes:
        if n.kind == kind:
            idx.setdefault(n.name, []).append(n.qualified_name)
    return idx


def _depends_on_edges(
    nodes: list[Node], class_refs: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    by_simple = _index_by_simple_name(nodes, CLASS)
    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for src_fqn, refs in class_refs.items():
        for ref in refs:
            for dst_fqn in by_simple.get(ref, ()):
                if dst_fqn == src_fqn or (src_fqn, dst_fqn) in seen:
                    continue
                seen.add((src_fqn, dst_fqn))
                out.append(Edge(kind=DEPENDS_ON, src=src_fqn, dst=dst_fqn, provenance=prov))
    return out


def _calls_edges(
    nodes: list[Node], method_calls: dict[str, set[str]], prov: Provenance
) -> list[Edge]:
    # Method 노드를 단순명으로 색인한다. CALLS는 단순명 기반 best-effort다:
    # `foo(...)` 호출은 `foo`라는 이름의 알려진 모든 메서드로 연결된다.
    # 자기 루프(호출자 자신과 같은 이름)는 억제한다 — 타입 해석이 없으면
    # 가장 흔한 오탐이기 때문이다.
    by_simple = _index_by_simple_name(nodes, METHOD)
    seen: set[tuple[str, str]] = set()
    out: list[Edge] = []
    for src_fqn, names in method_calls.items():
        for name in names:
            for dst_fqn in by_simple.get(name, ()):
                if dst_fqn == src_fqn or (src_fqn, dst_fqn) in seen:
                    continue
                seen.add((src_fqn, dst_fqn))
                out.append(Edge(kind=CALLS, src=src_fqn, dst=dst_fqn, provenance=prov))
    return out


def _iter_java_files(root: Path):
    for p in sorted(root.rglob("*.java")):
        if p.is_file():
            yield p


def extract(root: Path | str, provenance: Provenance, repo_name: str | None = None) -> IR:
    """``root`` 아래의 모든 ``*.java`` 파일을 :class:`IR`로 파싱한다.

    ``root``를 저장소 루트로 취급한다. File 노드 경로는 이 루트 기준
    repo-상대 경로로 기록한다. 모든 노드와 엣지는 ``provenance``를 지닌다.
    """
    root = Path(root)
    repo_name = repo_name or root.name
    parser = _parser()

    nodes: list[Node] = []
    edges: list[Edge] = []
    packages: set[str] = set()
    class_refs: dict[str, set[str]] = {}
    method_calls: dict[str, set[str]] = {}

    for path in _iter_java_files(root):
        source = path.read_bytes()
        tree = parser.parse(source)
        rel = path.relative_to(root).as_posix()
        walker = _FileWalker(rel, source, tree.root_node, provenance)
        walker.run()
        nodes.extend(walker.nodes)
        edges.extend(walker.edges)
        if walker.pkg:
            packages.add(walker.pkg)
        for fqn, refs in walker.class_refs.items():
            class_refs.setdefault(fqn, set()).update(refs)
        method_calls.update(walker.method_calls)

    # DEPENDS_ON (Class->Class): 참조된 단순 타입명을 알려진 클래스들의
    # 비수식명과 맞춰 해석한다 (best-effort, 전체 타입 해석 없음).
    edges.extend(_depends_on_edges(nodes, class_refs, provenance))
    # CALLS (Method->Method): 단순명 기반 best-effort 해석.
    edges.extend(_calls_edges(nodes, method_calls, provenance))

    # Repo 노드 + Package 노드들 + Repo->Package CONTAINS
    repo = Node(kind=REPO, qualified_name=repo_name, name=repo_name, provenance=provenance)
    repo_and_pkgs: list[Node] = [repo]
    repo_edges: list[Edge] = []
    for pkg in sorted(packages):
        repo_and_pkgs.append(
            Node(
                kind=PACKAGE,
                qualified_name=pkg,
                name=pkg.rsplit(".", 1)[-1],
                provenance=provenance,
            )
        )
        repo_edges.append(Edge(kind=CONTAINS, src=repo_name, dst=pkg, provenance=provenance))

    return IR(nodes=repo_and_pkgs + nodes, edges=repo_edges + edges)
