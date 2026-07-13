; ECMAScript structural tags — definitions and call references.
; Per-language query (ADR-20260706 §결정6: own the build-less tree-sitter spine).
; SHARED across the whole ECMAScript family: this ONE file compiles against the
; typescript, tsx AND javascript grammars — they share these node types
; (function_declaration, class_declaration, method_definition, variable_declarator,
; call_expression, member_expression). class_declaration's name is `type_identifier`
; in TS and `identifier` in JS, so the class name is captured with a `(_)` wildcard.
;
; The name-based CALLS resolver consumes `@reference.call*`; the `@definition.*`
; captures are the reusable tag surface (mirroring the Java/Kotlin queries). The
; walker discovers definition nodes structurally, so only `@reference.call*` is
; functionally consumed here.

(function_declaration name: (identifier) @name) @definition.function

; A top-level arrow / function-expression const is a first-class Function (the
; dominant React/TS shape): `const C = () => {}` / `const f = function(){}`.
(variable_declarator
  name: (identifier) @name
  value: [(arrow_function) (function_expression)]) @definition.arrow

(class_declaration name: (_) @name) @definition.class

(method_definition name: (property_identifier) @name) @definition.method

; Call references — an unqualified callee `f()` or the trailing property of a
; member call `obj.f()`. Resolved name-based for this slice (receiver typing is
; out of scope). The `function:` field constrains the match to the callee, never
; an argument identifier.
(call_expression function: (identifier) @reference.call.name) @reference.call
(call_expression
  function: (member_expression property: (property_identifier) @reference.call.name)) @reference.call
