# Stepped Rotational v0 Generated Noisy-Cloud Vertical Slice

## Status

**Provisional internal implementation design and bounded implementation
evidence, snapshot dated 2026-08-07.** This slice adds one deterministic,
synthetic-only generated noisy-cloud workflow before any CAD-derived workflow.
It does not establish physical accuracy, arbitrary-cloud support, automated fit
acceptance, a public format, compatibility, production support, or CAD
publication.

## Boundary

The generated revision supports exactly the `asymmetric-datum-flat` variant,
nominal seven-parameter truth, fixed-pose shape fitting, the application-owned
`guarded-grid-v1` profile, explicit seed and positive noise sigma at most
`25e-6 m`, and no outliers. Revision 1 remains available unchanged.

The profile analytically samples the three cylinders, four axial planes, and
datum flat away from bounded transitions. It fixes row order and coherent
training/held-out roles before noise. A revisioned SHA-256 counter construction
produces normal samples, rejects values beyond four sigma, and quantizes accepted
normal offsets to `1e-9 m`. Training and held-out draws are domain-separated by
role and stable fixture observation ID.

Exact byte replay is provisional for the same supported Python implementation,
platform, and mathematical-library environment. SHA-256 counter inputs and noise
offsets are specified and quantized, but analytic grid trigonometry and the
uniform-to-normal transform still use the platform `libm`. Verification fails
closed on different bytes; this slice makes no cross-platform byte-identity
claim. A future support matrix would require retained cross-platform evidence or
a separately approved portable mathematical construction.

Stable fixture IDs bind fixture revision, variant, profile, element sampling
domain, grid indices, and role. They exclude seed, sigma, realization, paths, and
pipeline run IDs. Existing observation, mapping, factor, and execution IDs remain
content- and revision-scoped.

## Artifacts And Ownership

A generation run contains exactly `observations.ply`, `provenance.json`,
`ground-truth.json`, `manifest.json`, and `manifest.sha256`. The PLY is binary
little-endian float64 XYZ in metres and contains no identities or semantic
properties. Provenance owns row IDs and roles but no expected-support labels.
Ground truth owns nominal parameters, noiseless points, analytic normals,
expected supports, and realized offsets.

`inspect` remains unaware of generated semantics. Generated mapping admission
verifies the generation run and exact PLY/inspection relationship, then persists
enough provenance for later mapping replay without the generation path. Mapping
still assigns every training point independently from coordinates, the explicit
transform, nominal supports, and explicit thresholds. Ground truth never enters
mapping, factors, initialization, activation, execution, or held-out assessment.

`compare-truth` is read-only human presentation. It verifies all four supplied
run roots, reports raw parameter errors and residual summaries for completed
execution, and reports comparison unavailable for another valid execution
disposition. It publishes no artifact and defines no quality threshold.

## Staged Walkthrough

Generate and inspect one realization:

```console
uv run scansor generate-stepped-rotational runs/generated \
  --variant asymmetric-datum-flat \
  --sampling-profile guarded-grid-v1 \
  --seed 7 \
  --noise-sigma-m 0.00002
uv run scansor verify-generation runs/generated
uv run scansor inspect runs/generated/observations.ply runs/inspection \
  --unit m \
  --frame stepped-rotational-v0-synthetic-model-frame
uv run scansor verify runs/inspection
```

Create a map TOML using the existing required identity transform, metre/frame
assertions, all six explicit mapping thresholds, variant, inspection/output
paths, `generation_run = "runs/generated"`, and the exact
`held_out_row_indices` array from `runs/generated/provenance.json`. Then run:

```console
uv run scansor --config map.toml map
uv run scansor verify-mapping runs/mapping runs/inspection
```

Create a fit TOML for `fixed-pose-shape`, metre units,
`all-instantiated-primary-training-v0`, and the explicit in-bounds walkthrough
initial vector `[0.0122, 0.0178, 0.0142, 0.0202, 0.0498, 0.0802, 0.0162]`.
Then run:

```console
uv run scansor --config fit.toml fit
uv run scansor verify-fit runs/execution runs/inspection runs/mapping
uv run scansor compare-truth \
  runs/generated runs/inspection runs/mapping runs/execution
```

Generation publication and verification return `0` on success and `2` for an
invalid request, artifact, path, or publication. Mapping and fitting retain their
existing `0`, `2`, and `3` meanings. Truth comparison returns `0` for mutually
consistent valid artifacts, including comparison-unavailable execution, and `2`
for invalid or incompatible inputs; it never returns `3`.

## Explicit Deferrals

Axisymmetric noisy generation, pose correction, joint fitting, configurable
outliers, robust loss, tangential or sensor-derived noise, automatic
initialization, arbitrary PLY mapping, machine-readable comparison, acceptance,
CAD/Onshape sampling or publication, external adapters, physical validation,
metrology, production packaging, and compatibility remain separately gated.
