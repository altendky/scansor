# Exploratory cylinder fit

`experiments.mesh_cylinder_fit` fits a cylinder to a fixed set of XYZ rows
using positive area weights and ordinary weighted radial least squares. It is
separate from the synthetic-only fitting admission path and does not establish
physical accuracy or a public fitting interface. No additional dependencies
beyond NumPy are needed.

In a local orthonormal coordinate frame, parameters are
`[center_x, center_y, axis_slope_x, axis_slope_y, radius]`. The axis is proportional
to `(axis_slope_x, axis_slope_y, 1)` and its reference point lies on local `z=0`.
Choose a frame approximately aligned with the expected axis and centered near
the observations. The parameterization does not cover axes parallel to `z=0`.

Call `fit_cylinder(points, weights, initial)` with float64 arrays. The solver
uses analytic derivatives, Gauss–Newton steps, and backtracking. It rejects
ill-conditioned normal matrices and failed convergence. Every supplied row
participates; there is no trimming, robust loss, or sampling. Positive residuals
mean points outside the cylinder. The implementation holds the selected arrays
and Jacobian in memory; it is not an externally spilling solver.

Tests recover an independently constructed tilted cylinder with nonuniform
weights and compare the analytic Jacobian with central differences. For real
observations, preserve the source and selection hashes, coordinate frame,
weights, starting guesses, fit results, and residuals. Check several starting
guesses and inspect residual structure; convergence alone does not validate the
model or identify a globally unique solution. A residual trend along the axis
is a diagnostic, not a fitted cone or a physical taper measurement.

The user-designated reduced nozzle example and its fixed selection are retained
in `examples/nozzle-bayonette-simplified/`. See its README for the replay command.
The original full-resolution specimen and generated private reports remain outside
the repository.

## Joint cylinder/end-plane experiment

`experiments.mesh_cylinder_plane_fit` adds a sixth parameter `h` to the cylinder
parameter vector. In the same local frame, its plane is `point · axis = h`, where
`axis = normalize((a,b,1))` is also the cylinder axis. Perpendicularity is exact
through shared parameters and adds no penalty factor. The cylinder reference point
is `(cx,cy,0)`; the plane offset is relative to the local frame origin, not that
reference point. The shared axis may move during optimization.

Cylinder residuals are signed radial distances; plane residuals are signed
orthogonal distances. The objective is their squared sum with incident-area
weights, globally normalized across both surfaces. The report separates per-region
RMS values from the combined objective. It compares a cylinder-only fit with a
plane-offset fit holding that cylinder fixed, and checks three joint starting
guesses. An unconstrained weighted TLS plane is a diagnostic comparison only.

The saved nozzle replay verifies both ID lists and their original geometric gates,
requires disjoint selections for this example, and independently checks residuals
in world coordinates. The shared input loader also preserves the existing
cylinder-only replay. Tests cover independent generated geometry, analytic
Jacobians, the real example's constrained tradeoff, and rejection of altered gates.

The view exporter in `experiments/nozzle_joint_view.py` emits display-only PLY
meshes. It uses the exact fitted geometry as the center of thin guide tubes;
tessellation and tube thickness are visualization choices, not fitted observations.
