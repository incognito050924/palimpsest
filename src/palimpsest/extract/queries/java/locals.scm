; Java typed bindings — the name->type facts a receiver identifier is resolved
; against. Fields and locals/params are captured SEPARATELY: a field binds at the
; class it is declared in; a local/parameter binds only inside its own method.
; Keeping them distinct stops a local (e.g. inside an anonymous class) from ever
; being mistaken for a field. Per-language query (ADR-20260706 §결정6); the
; resolver stays language-agnostic over these capture names.

(field_declaration
  type: (_) @field.type
  declarator: (variable_declarator name: (identifier) @field.name))

(formal_parameter
  type: (_) @local.type
  name: (identifier) @local.name)

(local_variable_declaration
  type: (_) @local.type
  declarator: (variable_declarator name: (identifier) @local.name))
