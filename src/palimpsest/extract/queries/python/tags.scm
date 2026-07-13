; Python structural tags — definitions and call references.
; Per-language query (ADR-20260706 §결정6: own the build-less tree-sitter spine).
; The name-based CALLS resolver consumes `@reference.call*`; the `@definition.*`
; captures are the reusable tag surface mirroring the Java/Kotlin queries.
;
; Python (unlike Kotlin) has NO in-source package header, and its class body and
; a function body are the SAME grammar node (`block`) — so the query cannot tell
; a method from a local def, and cannot tell a top-level def from a nested one.
; That parent-context discrimination is done structurally in the walker
; (module-direct -> Function, class block -> Method, function-body def -> not
; emitted). These `@definition.*` captures are therefore documentary only; the
; load-bearing surface the resolver consumes is `@reference.call*`.

(class_definition name: (identifier) @name) @definition.class

(function_definition name: (identifier) @name) @definition.function

; Call references — an unqualified callee `f()` or the trailing name of an
; attribute receiver `obj.f()`. Resolved name-based for this first slice
; (receiver typing is out of scope).
(call function: (identifier) @reference.call.name) @reference.call
(call function: (attribute attribute: (identifier) @reference.call.name)) @reference.call
