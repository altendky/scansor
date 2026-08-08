# Scansor Project

Scansor is intended as a reusable expert product, not a one-off fitting script.
Its target is constrained fitting of user-declared, topology-stable geometric
models to explicitly grouped observations. Universal automatic scan-to-CAD is
outside the intended boundary.

## Status Labels

- **Current direction** records the product contract or boundary guiding work.
- **Provisional** records an architecture or decision requiring validation.
- **Research finding** records the conservative conclusion from investigation.
- **Open** records an unresolved question or an experiment not yet completed.

## Documentation

- [Terminology and scope](terminology-and-scope.md) - **Current direction:**
  canonical terminology, the user-to-product contract, initial model families,
  and explicit exclusions
- [Principles and requirements](principles-and-requirements.md) - **Current
  direction:** exact constraints, auditability, diagnostics, synthetic-versus-
  physical truth, validation, and the bounded expert-tool posture
- [Architecture and integrations](architecture-and-integrations.md) -
  **Current and provisional:** local authoritative fit and external heavy-GUI
  boundaries; provisional generated evidence flow, canonical flow, adapters,
  snapshots, and integrations
- [Integrated planning program](planning/index.md) - **Provisional:** shared
  terminology, deterministic generated-first critical path, staged dependency
  graph, evidence gates, deferred physical validation, and five owned tracks
- [Stepped rotational v0 observation mapping](planning/stepped-rotational-v0-observation-mapping.md)
  - **Provisional:** internal synthetic-only observation/mapping contracts,
  deterministic analytic association, diagnostics, and separate replay boundary
- [Stepped rotational v0 factor contract](planning/stepped-rotational-v0-factor-contract.md)
  - **Provisional:** application-owned declarations, explicit activation, pure
  analytic evaluation, and optimizer-independent preflight; no fit or factor CLI
- [Stepped rotational v0 execution and result contract](planning/stepped-rotational-v0-execution-result.md)
  - **Provisional:** solver-independent guarded execution, deterministic replay,
  and separate held-out assessment; no acceptance policy or CLI
- [Stepped rotational v0 NumPy backend and execution run](planning/stepped-rotational-v0-numpy-execution-run.md)
  - **Provisional:** deterministic bounded synthetic-only backend and internal
  content-addressed publication/read-only verification; no production claim,
  public compatibility, acceptance policy, or CLI
- [Stepped rotational v0 CLI vertical slice](planning/stepped-rotational-v0-cli-vertical-slice.md)
  - **Provisional:** local synthetic-only mapping and fixed NumPy execution-run
  orchestration, explicit semantic inputs, analyzed exit statuses, and read-only
  verification; no physical validation, automated acceptance, or public contract
- [Stepped rotational v0 generated noisy-cloud vertical slice](planning/stepped-rotational-v0-generated-noise-vertical-slice.md)
  - **Provisional:** deterministic asymmetric generated XYZ PLY, bounded normal
    noise, stable fixture IDs, generated mapping admission, replay, and raw
    truth comparison; no outlier fitting, acceptance, CAD path, or physical claim
- [CAD extraction research](cad-constraint-and-geometry-extraction.md) -
  **Research finding and provisional implications:** native CAD geometry and
  relationship extraction, generated geometry-only Onshape fixture, later broad
  snapshots, and identities
- [Python prototype foundation libraries](python-foundation-libraries.md) -
  **Provisional:** quality-first selection posture, Cyclopts, Pydantic, settings
  resolution, basedpyright, deferred choices, and required integration evidence
- [Python prototype support libraries](python-support-libraries.md) -
  **Provisional:** runtime diagnostics, terminal output, paths, adapter HTTP,
  tests, environment tooling, documentation, bulk data, deferrals, and fixtures
- [Repository and development tooling](repository-and-development-tooling.md) -
  **Provisional:** implemented locked mise/uv ownership, local gates, baseline CI,
  future Renovate, test seams, and explicit packaging and release deferrals
- [Tool landscape](tool-landscape.md) - **Research finding:** adjacent products
  and the unfilled complete-contract gap, without claims beyond current public
  verification
- [Ceres Python and Rust bindings](ceres-python-rust-bindings.md) - **Research
  finding:** binding capabilities, gaps, and risks that inform the separate
  provisional Python/SciPy prototype decision
- [Decisions](decisions.md) - **Current and provisional:** accepted directions,
  rejected naming, and deliberately unmade choices
- [Open questions](open-questions.md) - **Open:** fixed-outlier robustness policy,
  additional combined adverse evidence, later physical evidence, schemas,
  implementation, validation, naming, and boundaries
