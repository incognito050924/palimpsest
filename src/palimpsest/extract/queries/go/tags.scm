; Per-language Go tag query (ADR-20260706 §결정6). The @definition.* captures are
; documentary — the walker does structural discrimination (function_declaration vs
; method_declaration, type_spec). The load-bearing captures the name-based resolver
; consumes are @reference.call (the whole call node) + @reference.call.name (the
; callee's trailing identifier: a plain call `f()` or a selector call `x.f()`).
(type_declaration (type_spec name: (type_identifier) @name)) @definition.type
(function_declaration name: (identifier) @name) @definition.function
(method_declaration name: (field_identifier) @name) @definition.method

(call_expression function: (identifier) @reference.call.name) @reference.call
(call_expression
  function: (selector_expression field: (field_identifier) @reference.call.name)) @reference.call
