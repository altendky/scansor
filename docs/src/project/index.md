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
  physical truth, full-data accountability, recipe-backed large inputs,
  validation, and the bounded expert-tool posture
- [Architecture and integrations](architecture-and-integrations.md) -
  **Current and provisional:** local authoritative fit and external heavy-GUI
  boundaries; provisional generated evidence flow, canonical imports, adapters,
  constraint-derived calibration, snapshots, and integrations
- [Integrated planning program](planning/index.md) - **Provisional:** shared
  terminology, deterministic generated-first critical path, staged dependency
  graph, evidence gates, deferred physical validation, and five owned tracks
- [Full-resolution mesh ingestion and visual audit](planning/full-resolution-mesh-ingestion.md)
  - **Provisional design:** external triangle-mesh profile, arbitrary units,
    complete accounting, bounded storage, area weights, portable recipes,
    CloudCompare views, and implementation gates; not implemented
- [Mesh ingestion library evaluation](planning/full-resolution-mesh-library-evaluation.md)
  - **Research and provisional selection:** library/license comparison and small
    storage/random-bit probes; isolated PLY I/O and explicit NumPy-based boundary
- [Declared analytic model contract](planning/declared-analytic-model-contract.md)
  - **Provisional:** implemented internal declaration records and shared analytic
  evaluator for declared planes, coaxial cylinders, bounded domains, shape
  residuals, and derivatives, now consumed by a shared generated two-topology
  fixed-pose workflow; no public schema, compatibility, or product-support claim
- [Declared analytic model generated workflow](planning/declared-analytic-model-generated-workflow.md)
  - **Provisional:** shared declaration-bound generation, mapping, factors,
  bounded execution, held-out assessment, publication, truth comparison, and
  read-only verification for asymmetric stepped and coaxial tube fixtures; no
  arbitrary sampling, physical-validation, acceptance, or public-interface claim
- [Declared analytic model observation mapping](planning/declared-analytic-model-observation-mapping.md)
  - **Provisional:** implemented compatibility-free, model-bound synthetic-only
  mapping using declared elements, evaluator geometry/Jacobians, declaration-owned
  admission policy, declaration-bound generated provenance, and read-only replay
- [Declared analytic model factor and preflight](planning/declared-analytic-model-factor-preflight.md)
  - **Provisional:** implemented compatibility-free, model-bound fixed-pose
  factor construction, explicit activation, shared-evaluator residuals and
  Jacobians, and declaration-owned optimizer-independent preflight
- [Declared analytic model execution](planning/declared-analytic-model-execution.md)
  - **Provisional:** model-bound guarded fixed-pose execution, bounded NumPy
  adapter, nominal-support held-out assessment, internal publication, and
  read-only replay; shared two-topology generated and stepped regression evidence
- [Stepped rotational v0 observation mapping](planning/stepped-rotational-v0-observation-mapping.md)
  - **Superseded snapshot:** original stepped-only observation/mapping contract
- [Stepped rotational v0 factor contract](planning/stepped-rotational-v0-factor-contract.md)
  - **Superseded snapshot:** original stepped factor/preflight contract retained
  for the legacy execution and pose-correction path
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
- [Retained experiment type diagnostics](experiments/retained-source-type-diagnostics.md)
  - **Bounded cleanup:** all 250 original exceptions inventoried, 146 corrected
  through an approved regeneration chain, and 104 retained with documented reasons
- [Tool landscape](tool-landscape.md) - **Research finding:** adjacent products
  and the unfilled complete-contract gap, without claims beyond current public
  verification
- [Ceres Python and Rust bindings](ceres-python-rust-bindings.md) - **Research
  finding:** binding capabilities, gaps, and risks that inform the separate
  provisional Python/SciPy prototype decision
- [Decisions](decisions.md) - **Current and provisional:** accepted directions,
  rejected naming, and deliberately unmade choices
- [Open questions](open-questions.md) - **Open:** fixture-registry and sampling
  authority, broader declaration authoring, arbitrary-domain and combined adverse
  evidence, fixed-outlier robustness policy, external full-data imports,
  calibration/registration, later physical evidence, schemas, validation, naming,
  and boundaries
