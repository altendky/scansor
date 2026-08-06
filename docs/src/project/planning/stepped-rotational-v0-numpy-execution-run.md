# Stepped Rotational v0 NumPy Backend and Execution Run

## Status

**Provisional internal implementation design, snapshot dated 2026-08-02.** This
slice is synthetic-only, fixed-topology, non-public, and has no compatibility
promise. It applies only to `stepped-rotational-v0` and makes no production
backend, solver-quality, fit-accuracy, physical-accuracy, metrology, product
support, or public-format claim. It adds no CLI, including `fit`.

The slice does not import, modify, regenerate, or reinterpret frozen generated
solver, Track 5, reconciliation, evidence, or tolerance materials. Those remain
read-only mathematical and status-separation context.

## Bounded Backend

One adapter implementation,
`scansor.numpy-gauss-newton.stepped-rotational-v0` revision `provisional-1`,
implements the existing adapter protocol using NumPy only. It accepts exactly
the v1 adapter protocol, v2 callback protocol, application factor contract,
declared parameter order, inclusive bounds, scales, two fixture variants, and
the separate fixed-pose-shape and fixed-geometry-pose-correction problems.
Unsupported invocations fail before callback use.

The deterministic algorithm uses scaled Gauss-Newton with an SVD pseudoinverse,
never normal equations or randomness. Residual scale is `0.001 m`; the relative
SVD cutoff is `1e-10`. Required ranks are shape `6/7`, asymmetric pose `6`, and
axisymmetric pose exactly `5`. Axisymmetric pose uses full six-coordinate scaled
minimum-norm unconstrained increments orthogonal to the current analytic Jacobian
nullspace, thereby transporting the initial roll gauge without a roll
normalization, named coordinate freeze, gauge residual, or artificial constraint.
At an active box bound, feasibility takes precedence and the constrained
active-set step need not remain nullspace-orthogonal. Because the rotation-vector
chart is additive and nonlinear, gauge transport does not require a constant
`phiz` coordinate.

The provisional stopping and globalization constants are fixed implementation
inputs: residual infinity tolerance `1e-12 m`, projected-gradient infinity
`1e-12`, relative scaled-step `1e-12`, relative objective stagnation `1e-15`
for three accepted steps, Armijo `1e-4`, backtracking multiplier `0.5`, twelve
halvings/thirteen trials, and at most 64 accepted iterations. The backend callback
cap is `min(requested cap, 256)`.

Active bounds use the projected gradient and a deterministic free-coordinate SVD
re-solve. If that freeze-only active set cannot produce descent while projected
stationarity is false, negative projected gradient is the bounded fallback. The
largest feasible box step is computed analytically; only limiting coordinates
land exactly on bounds. Trial vectors are never clipped. Structurally invalid
trials are rejected before the callback. Accepted steps satisfy Armijo decrease
in the dimensionless objective, and the accepted evaluation is reused without a
final callback.

Stable backend codes distinguish tolerances, limits, stagnation, rank, SVD,
numeric, and descent failures. Only residual or projected-gradient stationarity
reports backend convergence. Stagnation never does. A validated stopped, limit,
or failure response can still be an execution disposition of
`completed-not-assessed`; neither completion nor backend convergence means
accepted.

## Provisional Execution Runs

The in-memory entry point requires caller-supplied active factor IDs and an
initial parameter vector. It performs no automatic activation or initialization.
The filesystem entry point consumes anchored inspection and mapping runs and
publishes one content-addressed execution run. Read-only verification never
instantiates or invokes the backend.

The format is
`scansor-stepped-rotational-v0-execution-run-manifest-v1`. Completed runs contain
exactly `selection.json`, `result.json`, `held-out.json`, `manifest.json`, and
`manifest.sha256`. Every other valid execution disposition omits
`held-out.json`. Held-out assessment is required only for
`completed-not-assessed` and forbidden otherwise.

The manifest binds the inspection report and canonical hashes and run ID; mapping
format, run, manifest, and mapping hashes; factor contract and set; adapter
descriptor and ID; explicit active selection and initial vector; request and
result IDs; disposition, variant, and problem; held-out identity and hash or an
explicit noncompleted state; artifact hashes; and a content-derived execution-run
ID. It references rather than duplicates canonical arrays, mapping records, or
factor records.

Control files use strict canonical ASCII JSON and exact re-encoding. Limits are
4 MiB for selection, the existing 64 MiB for the result, 32 MiB for held-out,
1 MiB for the manifest, and 256 bytes for the sidecar. Verification anchors all
three roots, verifies inspection/source and mapping replay, reconstructs factors,
selection, and request, replays the result without an adapter, recomputes
completed held-out assessment, recomputes manifest identity, and rehashes and
identity-checks every entry and root.

Publication refuses overwrite, uses descriptor-anchored inputs and parent,
creates a mode `0700` stage and mode `0600` files with no-follow/exclusive opens,
prevents output inside either input tree, verifies exact identities before and
after atomic no-replace rename, and removes only unchanged publication-owned
content through identity-safe quarantine. Atomic rename defines visibility; this
slice does not claim power-loss durability.

This cleanup is not a hostile same-UID security boundary. No-replace isolation,
full identities, and pre-unlink checks preserve detected changed or foreign
entries, but an actor that already holds a writable descriptor can race an
in-place mutation between the final check and unlink. Stronger process isolation
or a different retention policy is required before claiming protection against
that adversarial case.

## Explicit Deferrals

Production backend selection, production scaling or initialization policy,
robust loss, acceptance policy and records, public or compatible schemas,
authentication, signatures, durable storage guarantees, arbitrary clouds,
external observations, CAD use or publication, physical references, accuracy or
metrology claims, all mapping/factor/execution CLI commands, and `fit` remain
separately gated.
