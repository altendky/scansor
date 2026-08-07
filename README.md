# Scansor

Scansor is a concept-stage reusable product for fitting declared parametric
geometric models to scan observations while preserving explicit constraints and
producing auditable results.

It is not currently an implemented product, and it is not intended to be a
universal automatic scan-to-CAD system.

## Status

**Concept stage.** The problem contract and initial product boundary are
documented. Architecture is provisional, key integration experiments remain to
be run, and no production implementation stack or documentation generator has
been chosen. Python and SciPy are selected only for the first evidence-generating
prototype. One experiment-local generated solver/evaluator gate now exists; it is
not a product implementation or production-stack selection. One internal,
provisional CLI fixture performs bounded local PLY inspection and read-only
replay. A synthetic-only successor exposes the fixed `stepped-rotational-v0`
mapping and NumPy execution-run pipeline with read-only verification. Its
commands and formats remain non-public, compatibility-free implementation
evidence rather than a product fitting interface.
One additional bounded slice generates and replays an asymmetric synthetic noisy
XYZ cloud, carries stable fixture identities through mapping, and presents a
read-only raw comparison with nominal generator truth. It does not add arbitrary
clouds, fit acceptance, physical validation, or CAD publication.

The working name comes from a gecko adhesive toe-pad structure. Public-name
clearance remains open because unrelated commercial SAP-monitoring software
already uses Scansor; market separation may make coexistence plausible but has
not been established.

## Documentation

- [Documentation home](docs/src/index.md) - project orientation and status
- [Project documentation](docs/src/project/index.md) - annotated source hub for
  scope, requirements, architecture, research, decisions, and open questions

## License

Licensed under either of:

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT License ([LICENSE-MIT](LICENSE-MIT))

at your option.
