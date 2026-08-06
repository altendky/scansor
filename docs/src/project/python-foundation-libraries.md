# Python Prototype Foundation Libraries

## Status

**Provisional, snapshot dated 2026-07-24.** These choices support the first
Python evidence-generating prototype. They do not select the production
language, production solver, durable public schemas, or final CLI contract. No
product implementation exists. The first internal, provisional CLI slice now
exercises explicit Cyclopts TOML/environment/CLI resolution into Pydantic models,
but it does not complete every integration-evidence item below or validate a
production foundation.

## Selection Posture

Prefer a well-maintained third-party library over a stabilized but materially
more limited standard-library alternative when the library improves the API,
typing, validation, diagnostics, or testability. This is a selection criterion,
not a rule to add dependencies without a concrete benefit.

## CLI

Use [Cyclopts][cyclopts] as the provisional CLI library. Its annotation-driven
commands, support for Pydantic models and modern Python types, generated help,
validation hooks, and configurable input sources fit the prototype better than
building equivalent behavior directly on `argparse`.

Selecting Cyclopts does not settle the command tree, option names, output
conventions, or eventual interaction model. Those remain open and should be
kept small until prototype use provides evidence.

## Models, Validation, and Serialization

Use [Pydantic v2][pydantic] for typed application-boundary models, validation,
provisional JSON Schema generation, and JSON serialization. Favor explicit
constraints, forbidden unknown fields, finite numeric values, validated
defaults, discriminated unions where appropriate, and immutable models where
mutation is not part of the contract.

Settings inputs necessarily begin as text in some sources, so a blanket strict
mode is not assumed for settings models. Their accepted coercions must be
deliberate and tested. Persisted model, observation, mapping, and fit-result
contracts may require stricter rules. Pydantic-generated schemas remain
provisional until language-neutral schemas and compatibility policy are
designed and accepted.

Use Pydantic's serialization path initially rather than adding a second JSON
implementation such as orjson. Besides overlapping capability, non-finite
number behavior must be configured to fail validation rather than silently
become JSON `null`, for example through explicit finite-value constraints,
`allow_inf_nan=False`, validated defaults, or equivalent model policy.

## Settings Resolution

Cyclopts should own source discovery and precedence. A plain Pydantic
`BaseModel`, not `pydantic-settings` `BaseSettings`, should own the resolved
settings schema and final validation. This avoids two independent loaders,
conversion paths, and precedence systems.

The intended precedence is:

1. Explicit CLI values
2. Environment variables
3. An explicitly selected configuration file
4. A standard user configuration file, if one is adopted
5. Pydantic model defaults

Cyclopts applies configured sources in priority order and injects their values
before final conversion and validation. TOML should be the initial human-edited
configuration format. JSON-compatible control and audit records remain distinct
from referenced bulk numeric artifacts and from user preferences. Additional
settings formats are not selected.

Environment-based settings should initially cover operational concerns such as
service endpoints, credentials, logging, and cache locations. Ambient
environment variables should not silently change fitting semantics. Any
eventual CLI or configuration override of a fit request must be represented in
the resolved request and authoritative fit-result record.

Audit-oriented records must not persist secret values merely because they were
resolved. They should retain only redacted values, references, or provenance
for secrets while keeping non-secret resolved settings inspectable.

Do not add `pydantic-settings` for the initial prototype. It remains a coherent
alternative if broad settings-source support becomes more important than the
Cyclopts CLI. Combining its source loading with Cyclopts source loading is not
the intended architecture.

## Static Type Checking

Use [basedpyright][basedpyright] as the required static type checker. The internal
CLI fixture now runs it from `recommended` mode with explicit exceptions for
dynamic third-party boundaries. Its broad Pyright coverage, strict defaults,
`Any` diagnostics, and additional correctness rules outweigh the speed advantage
of [ty][ty] for the initial small codebase. The complete representative typing
fixture below remains open.

Treat ty as a future challenger, not a second equal gate. Its performance,
diagnostics, language server, narrowing, and Astral-tool integration are
promising, but its documented rule mapping still identifies checks without a
direct equivalent. Running both as required gates would create conflicting
inference and suppression pressure. Reconsider the choice with a representative
typing fixture rather than adopting checker-specific language extensions.

## Required Integration Evidence

Before treating this foundation as validated, implement a small fixture that
checks:

- CLI, environment, explicit-file, standard-file, and model-default precedence
- nested partial overrides and collection replacement or merge behavior
- identical effective validation for representative values from every source
- unknown, missing, malformed, non-finite, and cross-field-invalid values
- lists, mappings, booleans, enums, paths, nulls, aliases, and tagged unions
- secret input and redaction behavior
- useful source attribution in errors without exposing secret values
- capture of resolved values and per-field provenance for audit records without
  persisting secret values
- Pydantic JSON Schema generation and representative serialization round trips
- basedpyright behavior for Pydantic, Cyclopts, NumPy, SciPy callback, and
  adapter-protocol types
- Cyclopts behavior with postponed annotations and cross-module inherited
  dataclass-like models for the selected Python version

Known risks to probe include ambiguity in nested Cyclopts environment-variable
names, silently ignored unknown prefixed environment variables, source-specific
text parsing, validation of overridden lower-priority values, and access to
Cyclopts' internal token provenance after binding.

## Deferred Choices

The next support layer now has provisional directions, explicit deferrals, and
required experiments in the [support library evaluation](python-support-libraries.md).
Neither evaluation selects exact dependency versions or a production stack.

[basedpyright]: https://docs.basedpyright.com/latest/
[cyclopts]: https://cyclopts.readthedocs.io/en/latest/
[pydantic]: https://docs.pydantic.dev/latest/
[ty]: https://docs.astral.sh/ty/
