; TypeScript-only type-annotation captures for DEPENDS_ON (ac-4).
; Per-language query (ADR-20260706 §결정6: own the build-less tree-sitter spine).
;
; This file is the STRUCTURAL half of the DEPENDS_ON asymmetry. It references node
; types that exist ONLY in the TypeScript grammar — `public_field_definition`,
; `required_parameter`, `type_annotation` — so it COMPILES against the typescript /
; tsx grammars and RAISES a QueryError against the javascript grammar. A JS build
; therefore can never even load a type query: the asymmetry is enforced at the
; query-compile boundary, not merely by the `collect_types` flag.
;
; The ECMAScript walker discovers these type references structurally (it already
; visits class bodies, methods and parameters and knows each ref's enclosing
; container fqn), so — mirroring how `ecmascript/tags.scm`'s `@definition.*` tags
; are the reusable surface while only `@reference.call*` is functionally consumed —
; these captures document the TS-only surface and are load-gated at import
; (`typescript.py` compiles this file against both TS grammars).

; A class field annotation: `dep: Foo` -> the enclosing Class DEPENDS_ON Foo.
(public_field_definition
  type: (type_annotation) @reference.type) @definition.field

; A typed parameter: `p: Bar` -> the enclosing container (Class for a method param,
; File/Module for a top-level-function param) DEPENDS_ON Bar.
(required_parameter
  type: (type_annotation) @reference.type) @definition.param
