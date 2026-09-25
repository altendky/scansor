# Demo video narration

**Working production script, 2026-09-25.** This script accompanies the
[two-workflow demo plan](demo-video.md). It describes an exploratory prototype,
not a product release or an accuracy claim. The working voice is Kokoro
`af_heart` at normal speed. Pronounce Scansor as “SCAN-sor.”

## 00 — Title

**Focus:** Title card, then the clean nozzle mesh.

**Narration:**

Scansor is an exploratory workspace for turning scan observations into explicit
geometric intent. This demonstration builds up parts of two workflows: retaining
and fitting selections on captured data, then reusing fitted features and
recovering an output coordinate system on a generated example.

## 01 — Captured nozzle

**Focus:** Oblique nozzle overview with a short, restrained orbit.

**Narration:**

We begin with a captured nozzle scan. Its surface is irregular, its units are
not confirmed, and nothing here is presented as physical validation. The mesh is
the evidence layer. Named features in the tree preserve the interpretation we
add to that evidence.

## 02 — Build retained selections

**Focus:** Add several retained selections to the visible inspection set.

**Narration:**

The colored patches appear as we select named regions from the feature tree.
They are retained vertex selections, not temporary click state. Several can be
inspected together without a modifier key, clicked again to remove them, or
cleared as a set. They remain editable inputs for later operations.

## 03 — Build reference geometry and fits

**Focus:** Add the center axis and two cone fits to the inspection set.

**Narration:**

The next layer is analytic geometry. A free axis is an explicit datum with its
own identity. Cone fits are separate graph features that retain their source
selections and can be recomputed. A fit may stand alone, refer to fixed geometry,
or participate in a combined solve when the design intent requires it.

## 04 — Rotational symmetry

**Focus:** Highlight the three repeated recess-slope selections around the nozzle.

**Narration:**

These three slope patches repeat around the center. After fitting the patches,
Scansor can declare exact threefold rotational symmetry about an axis. The three
observations then influence a shared solve while keeping their individual
selection lineage. Rotational symmetry is an explicit relationship, not an
assumption inferred merely because the patches look similar.

## 05 — Residuals

**Focus:** Switch to residual coloring and pause on the signed scale legend.

**Narration:**

Residual display replaces selection colors with signed distance from the fitted
surface. The legend shows both the display scale and the observed negative and
positive peaks. These are local fit residuals in scan coordinates. They are not,
by themselves, manufacturing error or an acceptance decision.

## 06 — Transition

**Focus:** Reuse and coordinate recovery title card.

**Narration:**

The second workflow uses a generated, noisy fixture so repeated features,
relationships, and coordinate recovery are easy to inspect.

## 07 — Source scan frame

**Focus:** Rotated boss plate with a small orbit before applying the output transform.

**Narration:**

The plate begins in its source scan frame, deliberately rotated and scaled away
from the desired output coordinates. Four bosses vary in height and orientation,
and three spherical references sit near the plate corners. The colored points
again represent retained observations on the noisy mesh.

## 08 — Build a reusable source

**Focus:** Add source selections, datums, and a cylinder fit to the inspection set.

**Narration:**

The first boss supplies a partial reusable definition. Its named outer, bore,
shoulder, and clock regions preserve distinct observations. An axis and two
planes provide a local frame, while cylinder and plane fits describe surfaces.
These pieces stay general: the reusable unit is a graph lineage, not a special
hard-coded boss feature.

## 09 — Feature reuse

**Focus:** Expand Feature reuse and enable the visible reuse volumes.

**Narration:**

Feature reuse combines those source fits with one simple reference patch and
several target patches. Approximate matches place surface-relative selection
volumes at each target. The volumes generate new target selections, and those
selections drive fresh local fits. The initial match is only a starting pose;
it does not become the final fitted transform or force all results to be equal.

## 10 — Exact relationships

**Focus:** Inspect equal-radius and parallel-plane relationships with residuals.

**Narration:**

Reused fits remain independent unless a relationship says otherwise. Here,
selected cylinder families share exact radii, while the plate top and chosen
shoulder planes share an exact direction. The taller boss remains offset but
parallel. These declarations influence fitted quantities and are distinct from
a later tolerance or closeness check.

## 11 — Measurable datums

**Focus:** Add sphere fits, point datums, a directed axis, frame, and scale.

**Narration:**

The three sphere fits provide measurable centers. Two centers initialize point
datums, and their ordered pair defines a directed axis. A coordinate frame maps
chosen direction references onto explicit output axes. Separately, one or more
known distances between points determine a least-squares uniform scale, so
additional readings can contribute rather than being discarded.

## 12 — Applied transform

**Focus:** Select Output transform, switch to Top, then make one small view adjustment.

**Narration:**

The Output transform composes the chosen frame and scale. Selecting it applies
that similarity transform to the view: the model moves into its intended output
orientation and size, while upstream fitted values remain in source coordinates.
The mesh, selections, analytic guides, and residual markers all share the same
display placement.

## 13 — Export boundary

**Focus:** Open Rhino export and inspect the chosen transform and units.

**Narration:**

The same transform is available at export together with an explicit unit choice.
That makes coordinate recovery a visible handoff decision instead of silently
rewriting the fitted geometry. This export dialog demonstrates the current
boundary; it is not a claim of a finished production CAD integration.

## 14 — Close

**Focus:** Closing card.

**Narration:**

Together, the examples show the current direction for Scansor: preserve user
observations, fit simple analytic primitives, express exact geometric intent,
reuse selection effort without freezing local fits, and make output scale and
coordinates explicit. The prototype is still evolving, but these operations
remain inspectable graph features rather than a flattened final result.
