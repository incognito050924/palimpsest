; Kotlin structural tags — definitions and call references.
; Per-language query (ADR-20260706 §결정6: own the build-less tree-sitter spine).
; The name-based CALLS resolver consumes `@reference.call*`; the `@definition.*`
; captures are the reusable tag surface mirroring the Java query.
;
; Kotlin has BOTH top-level functions and class methods on ONE grammar node
; (function_declaration); parent context is the ONLY discriminator — a
; function_declaration directly under source_file is a top-level Function, one
; under class_body is a Method.

(class_declaration (identifier) @name) @definition.class

(source_file (function_declaration (identifier) @name)) @definition.function

(class_body (function_declaration (identifier) @name)) @definition.method

; Call references — an unqualified callee `f()` or the trailing name of a
; navigation receiver `obj.f()`. Resolved name-based for this first slice
; (receiver typing is out of scope).
(call_expression (identifier) @reference.call.name) @reference.call
(call_expression
  (navigation_expression (identifier) (identifier) @reference.call.name)) @reference.call
