# Terminology and Scope

## Status

**Current direction.** This page defines the intended problem and initial
product boundary. It is not a claim of implemented capability.

## Formal Terminology

The canonical formal term is **constrained parametric geometric model fitting**.
Useful equivalent descriptions, depending on audience, include:

- joint constrained fitting
- geometric model calibration
- bounded robust nonlinear least-squares estimation of a user-defined
  generative model
- simultaneous pose/shape estimation
- analysis-by-synthesis

These terms describe the optimization problem. They do not imply automatic
topology discovery or general reverse engineering.

## Problem Contract

Users declare:

- topology and supported geometric elements
- which observations correspond to which elements
- fixed, free, and derived parameters
- parameter bounds
- hard, soft, and diagnostic relationships

Scansor optimizes only free parameters. It should produce fit diagnostics,
auditable parameter values, and CAD output. Observation correspondence is
explicit input rather than an inference the initial product promises to solve.

## Initial Product Boundary

A solid, bounded expert tool appears plausible. The first product should focus
on:

- declared, topology-stable analytic primitives
- line/arc profiles used in revolved and extruded forms
- exact-by-construction constraints
- explicit observation regions and element memberships
- robust fitting in the presence of outliers
- held-out observations for validation
- identifiability and observation-coverage diagnostics
- one or a few deliberately bounded adapters

## Explicit Exclusions

The initial product should not promise:

- automatic topology or feature recognition
- arbitrary CAD feature trees, unrestricted assembly semantics, or free-form
  NURBS fitting
- guaranteed global optima
- universal editable CAD history
- universal automatic scan-to-CAD conversion
- unsupported metrology or accuracy claims

Future expansion may alter supported model families or adapters, but should not
blur the distinction between verified capability and aspiration.

## Working Name

**Open.** `Scansor` is a working name derived from a gecko adhesive toe-pad
structure. Unrelated commercial SAP-monitoring software already uses Scansor.
The separate markets may make coexistence plausible, but public-name clearance
has not been completed.
