"""TDD for the Go static extractor (issue #11).

Hermetic: extracts an IR from an inline ``{relpath: go-source}`` corpus written
under ``tmp_path`` — never an external path. Mirrors the Kotlin extractor's
contract (ADR-20260706 §결정6: per-language queries/<lang>/*.scm, language-neutral
resolver) and the de-Class ontology (commit 138db70: FUNCTION node + File-as-module
container).

The load-bearing Go facts these tests pin:
  - Go has NO classes. Top-level `func` (function_declaration, no receiver) is a
    Function contained by its File; a receiver method (method_declaration) is a
    Method contained by its receiver *type* (struct/interface/named → Class).
  - Discrimination is by grammar node type (function_declaration vs
    method_declaration) + receiver, not parent-context nesting.
  - Package identity is the repo-relative directory (Go's import-path model, A-Q6),
    so two files in the same directory share one Package.
  - DEPENDS_ON is emitted (Go is statically typed, ADR-20260713 결정1); interface
    method signatures are NOT emitted as nodes (no body, no call sites).
"""

from pathlib import Path

from palimpsest.ir import Provenance, PACKAGE, FILE, CLASS, METHOD, FUNCTION
from palimpsest.extract.go import extract

PROV = Provenance(
    source_commit="c20b7332d8c60ce73794427a4c28120b085c134d",
    author="jeongjin <jeongjin@ecoletree.com>",
    committed_at="2025-09-03T16:22:54+09:00",
)

# Two files in ONE package (internal/config) to pin: (a) directory-based package
# identity, (b) a method whose receiver type is declared in a *different* file of
# the same package still resolves its Class container (no dangling CONTAINS).
SPEC_GO = """package config

import (
	"os"
	"github.com/acme/app/internal/domain"
)

type Reader interface {
	Read() error
}

type Spec struct {
	Name    string
	Entries []Entry
	dom     domain.Doc
}

type Entry struct {
	Repo string
}

func LoadSpec(path string) (Spec, error) {
	return decode()
}

func decode() (Spec, error) {
	return Spec{}, nil
}
"""

METHODS_GO = """package config

func (s *Spec) Validate(strict bool) error {
	return s.check()
}

func (s *Spec) check() error {
	return nil
}

func (e *Entry) Uses(s Spec) error {
	return nil
}
"""

# A vendored third-party file: MUST be excluded from extraction.
VENDOR_GO = """package ext

func Vendored() {}
"""

CORPUS = {
    "internal/config/spec.go": SPEC_GO,
    "internal/config/methods.go": METHODS_GO,
    "vendor/ext/lib.go": VENDOR_GO,
}


def _extract(tmp_path, files):
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src)
    return extract(tmp_path, PROV, repo_name="G")


def test_top_level_func_is_function_contained_by_file(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # top-level `func LoadSpec` / `func decode` are Functions (Go has no class to
    # hold them) — grounded to their File, package = the directory.
    fn = ir.node("internal/config.LoadSpec(string)")
    assert fn is not None and fn.kind == FUNCTION
    assert fn.name == "LoadSpec"
    assert fn.path == "internal/config/spec.go"
    assert fn.start_line == 22  # `func LoadSpec(path string) (Spec, error) {`
    assert ir.node("internal/config.decode()") is not None
    assert ir.node("internal/config.decode()").kind == FUNCTION
    # (:File)-[:CONTAINS]->(:Function)
    file_node = ir.node("internal/config/spec.go")
    assert file_node is not None and file_node.kind == FILE
    assert ir.has_edge("CONTAINS", "internal/config/spec.go", "internal/config.LoadSpec(string)")
    assert ir.has_edge("CONTAINS", "internal/config/spec.go", "internal/config.decode()")


def test_package_is_directory_and_repo_contains_it(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # Package identity = repo-relative directory (Go import-path model, A-Q6).
    pkg = ir.node("internal/config")
    assert pkg is not None and pkg.kind == PACKAGE
    assert pkg.name == "config"  # the `package config` clause name
    assert ir.has_edge("CONTAINS", "G", "internal/config")
    assert ir.has_edge("CONTAINS", "internal/config", "internal/config/spec.go")
    # both files of the package share the ONE package node
    assert ir.has_edge("CONTAINS", "internal/config", "internal/config/methods.go")
    # provenance is stamped on every node/edge
    for n in ir.nodes:
        assert n.provenance == PROV
    for e in ir.edges:
        assert e.provenance == PROV


def test_vendor_dir_is_excluded(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # vendored third-party code is not part of the project graph
    assert ir.node("vendor/ext/lib.go") is None
    assert ir.node("vendor/ext.Vendored()") is None
    assert not any(n.path and n.path.startswith("vendor/") for n in ir.nodes)


def test_receiver_method_is_method_contained_by_receiver_type(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # `func (s *Spec) Validate` is a Method — discriminated by the receiver, not by
    # parent-context nesting. Its container is the receiver type's Class, even though
    # `type Spec` is declared in a DIFFERENT file of the same package (no dangling).
    m = ir.node("internal/config.Spec#Validate(bool)")
    assert m is not None and m.kind == METHOD
    assert m.name == "Validate"
    assert m.path == "internal/config/methods.go"
    assert ir.has_edge("CONTAINS", "internal/config.Spec", "internal/config.Spec#Validate(bool)")
    assert ir.node("internal/config.Spec#check()") is not None
    # a value/pointer receiver is normalized to the bare type name
    assert ir.node("internal/config.Entry#Uses(Spec)") is not None
    # a Method is NEVER emitted as a top-level Function
    assert ir.node("internal/config.Validate(bool)") is None


def test_type_specs_are_classes(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # struct AND interface AND (below) named types all become Class nodes, mirroring
    # Java (interface/enum/record → Class). Each is contained by its File.
    spec = ir.node("internal/config.Spec")
    assert spec is not None and spec.kind == CLASS and spec.name == "Spec"
    assert ir.node("internal/config.Entry") is not None
    assert ir.node("internal/config.Entry").kind == CLASS
    reader = ir.node("internal/config.Reader")
    assert reader is not None and reader.kind == CLASS  # interface → Class
    assert ir.has_edge("CONTAINS", "internal/config/spec.go", "internal/config.Spec")
    assert ir.has_edge("CONTAINS", "internal/config/spec.go", "internal/config.Reader")
    # interface method signatures are NOT emitted as nodes (no body, no call site)
    assert ir.node("internal/config.Reader#Read()") is None
    assert not any(n.name == "Read" for n in ir.nodes)


def test_calls_edges_name_based(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # a plain call `decode()` from within LoadSpec: Function -> Function
    assert ir.has_edge("CALLS", "internal/config.LoadSpec(string)", "internal/config.decode()")
    # a selector call `s.check()` from within a method: Method -> Method (the resolver
    # keys on the trailing identifier `check`, name-based)
    assert ir.has_edge("CALLS", "internal/config.Spec#Validate(bool)", "internal/config.Spec#check()")
    # no self-loops
    assert not any(e.src == e.dst for e in ir.edges_of("CALLS"))


def test_depends_on_from_field_and_param_types(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # struct field `Entries []Entry` on Spec: Class -> Class (name-based, Go is
    # statically typed — ADR-20260713 결정1)
    assert ir.has_edge("DEPENDS_ON", "internal/config.Spec", "internal/config.Entry")
    # method param `s Spec` on receiver *Entry: the enclosing container is the
    # receiver Class Entry -> Class Spec
    assert ir.has_edge("DEPENDS_ON", "internal/config.Entry", "internal/config.Spec")
    # a reference to a type with no Class node in the graph (external `domain.Doc`)
    # is NOT invented as an edge (honest: name-based, resolved only against Classes)
    assert not any(e.dst == "Doc" or e.dst.endswith(".Doc") for e in ir.edges_of("DEPENDS_ON"))
    # no self-loops
    assert not any(e.src == e.dst for e in ir.edges_of("DEPENDS_ON"))


def test_imports_from_import_declarations(tmp_path):
    ir = _extract(tmp_path, CORPUS)
    # each import spec in a grouped `import ( ... )` block -> File IMPORTS path.
    # The dst is the raw import path (external -> honest dangling, no node).
    assert ir.has_edge("IMPORTS", "internal/config/spec.go", "os")
    assert ir.has_edge(
        "IMPORTS",
        "internal/config/spec.go",
        "github.com/acme/app/internal/domain",
    )


GENERICS_GO = """package collections

type Stack[T any] struct {
	items []T
}

type Queue[T any] struct {
	items []T
}

func (s *Stack[T]) Push(x T) {
}

func (q *Queue[T]) Push(x T) {
}
"""


def test_generic_receiver_resolves_to_base_type(tmp_path):
    # A generic receiver `func (s *Stack[T]) Push` must key on the DEFINED type
    # (Stack), not the type parameter (T). Getting this wrong dangles the Method's
    # Class container and collides every generic type's same-named method onto one
    # node (`pkg.T#Push`). Go 1.18+ generics are common — the fixture/sample lacked
    # one, so this pins it explicitly.
    ir = _extract(tmp_path, {"internal/collections/col.go": GENERICS_GO})
    push = ir.node("internal/collections.Stack#Push(T)")
    assert push is not None and push.kind == METHOD
    # its Class container exists (the struct type_spec) and CONTAINS is not dangling
    assert ir.node("internal/collections.Stack") is not None
    assert ir.node("internal/collections.Stack").kind == CLASS
    assert ir.has_edge(
        "CONTAINS", "internal/collections.Stack", "internal/collections.Stack#Push(T)"
    )
    # two distinct generic types with the same method name stay distinct (no id
    # collision on a bogus `pkg.T#Push`)
    assert ir.node("internal/collections.Queue#Push(T)") is not None
    assert ir.node("internal/collections.T#Push(T)") is None
    assert not any(n.qualified_name == "internal/collections.T" for n in ir.nodes)


def test_go_extractor_registered_by_extension():
    # .go dispatches to the Go extractor; the tree-sitter-go dependency is present.
    import tree_sitter_go  # noqa: F401 — presence is the assertion (pyproject dep)
    from palimpsest.extract import EXTRACTORS_BY_EXT, extract_go
    from palimpsest.extract.go import extract as go_extract

    assert EXTRACTORS_BY_EXT[".go"] is go_extract
    assert extract_go is go_extract


def test_go_query_is_per_language_and_valid():
    # ADR-20260706 §결정6: the tags query lives as a separate per-language file the
    # extractor loads at runtime and that compiles against the Go grammar.
    from tree_sitter import Language, Query
    import tree_sitter_go as tsgo
    from palimpsest.extract import go as gomod

    qdir = Path(gomod.__file__).parent / "queries" / "go"
    tags = qdir / "tags.scm"
    assert tags.is_file() and tags.read_text().strip()
    Query(Language(tsgo.language()), tags.read_text())  # must compile
    assert gomod._QUERY_DIR == qdir
