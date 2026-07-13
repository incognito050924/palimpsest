; Rust structural tags — definitions and call references.
; Per-language query (ADR-20260706 §결정6: own the build-less tree-sitter spine).
; The name-based CALLS resolver consumes `@reference.call*`; the `@definition.*`
; captures are the reusable tag surface mirroring the Kotlin/Java queries.
;
; Rust has NO class: a free `fn`, an `impl` method, and a `trait` method all share
; ONE grammar node (function_item). Parent context is the ONLY discriminator — a
; function_item directly under a module is a Function, one under an impl/trait body
; is a Method. The walker performs that structural discrimination; these captures
; document the definition surface.

(struct_item (type_identifier) @name) @definition.class
(enum_item (type_identifier) @name) @definition.class
(trait_item (type_identifier) @name) @definition.class

(function_item (identifier) @name) @definition.function

(impl_item (declaration_list (function_item (identifier) @name))) @definition.method
(trait_item (declaration_list (function_item (identifier) @name))) @definition.method

; Call references — a bare callee `f()`, the trailing name of a path call
; `Type::f()`, or a method call `recv.f()`. Resolved name-based for this first
; slice (receiver / turbofish typing is out of scope).
(call_expression function: (identifier) @reference.call.name) @reference.call
(call_expression
  function: (scoped_identifier name: (identifier) @reference.call.name)) @reference.call
(call_expression
  function: (field_expression field: (field_identifier) @reference.call.name)) @reference.call
