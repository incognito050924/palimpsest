; Java structural tags — definitions and call references.
; Per-language query (ADR-20260706 §결정6: own the build-less tree-sitter spine).
; The receiver-typed CALLS resolver consumes `@reference.call*`; the
; `@definition.*` captures are the reusable tag surface a new language mirrors.

(class_declaration name: (identifier) @name) @definition.class
(interface_declaration name: (identifier) @name) @definition.class
(enum_declaration name: (identifier) @name) @definition.class
(record_declaration name: (identifier) @name) @definition.class

(method_declaration name: (identifier) @name) @definition.method
(constructor_declaration name: (identifier) @name) @definition.method

; A call reference carries its optional receiver so CALLS can be resolved by
; the receiver's static type instead of by simple method name alone.
(method_invocation
  object: (_)? @reference.call.receiver
  name: (identifier) @reference.call.name) @reference.call
