# Demo video narration

**Working production script, 2026-09-24.** This script accompanies the
[two-workflow demo plan](demo-video.md). It describes an exploratory prototype,
not a product release or an accuracy claim. The working voice is Kokoro
`af_heart` at normal speed. Pronounce Scansor as “SCAN-sor.”

## 00 — Title

**Focus:** Title card, then the clean nozzle mesh.

**Narration:**

Scansor is an exploratory workspace for turning scan observations into explicit
geometric intent. This demonstration shows two current workflows: retaining and
fitting selections on captured data, then reusing fitted features and recovering
an output coordinate system on a generated example.

## 01 — Captured nozzle

**Focus:** Oblique nozzle overview and Fit view.

**Narration:**

We begin with a captured nozzle scan. Its surface is irregular, its units are
not confirmed, and nothing here is presented as physical validation. The mesh is
the evidence layer. Named features in the tree preserve the interpretation we
have added to that evidence.

## 02 — Retained selections

**Focus:** Several named selection features highlighted together.

**Narration:**

These colored patches are retained vertex selections, not temporary click
state. A user can select several features simply by clicking them, click again
to remove one, or clear the set. The selections can overlap, remain editable,
and serve as reusable inputs to later operations.

## 03 — Reference geometry and fits

**Focus:** `the middle`, `outer cone fit`, and `recess cone fit` guides.

**Narration:**

Analytic fits are separate graph features. Here, two cone fits refer to the same
explicit axis, called “the middle.” The axis is reference geometry with its own
identity; each fit still retains its observations and can be recomputed. This
lets geometric intent connect features without erasing where their evidence
came from.

## 04 — Residuals

**Focus:** Residual coloring and signed scale legend.

**Narration:**

Residual display replaces selection colors with signed distance from the fitted
surface. The legend shows both the display scale and the observed negative and
positive peaks. These are local fit residuals in scan coordinates. They are not,
by themselves, manufacturing error or an acceptance decision.

## 05 — Dependency graph

**Focus:** Graph workspace with the dependency lens.

**Narration:**

The feature tree remains the primary editing view, while the graph workspace
answers a different question: what depends on what? Selecting a fit reveals its
nearby inputs and outputs. Other lenses can isolate declared relationships,
joint activation, or generated ownership without changing the recipe.

## 06 — Transition

**Focus:** Reuse and coordinate recovery title card.

**Narration:**

The second workflow uses a generated, noisy fixture so repeated features,
relationships, and coordinate recovery are easy to inspect.

## 07 — Source scan frame

**Focus:** Rotated boss plate before the output transform is selected.

**Narration:**

The plate begins in its source scan frame, deliberately rotated and scaled away
from the desired output coordinates. Four bosses vary in height and orientation,
and three spherical references sit near the plate corners. The colored points
again represent retained observations on the noisy mesh.

## 08 — Feature reuse

**Focus:** Expanded Feature reuse group and visible reuse volumes.

**Narration:**

Feature reuse starts with fitted source features plus a simple reference patch
and several target patches. Their approximate matches place reusable,
surface-relative selection volumes at each target. Those volumes create fresh
target selections, and the selections drive fresh local fits. The initial match
does not become the final fitted transform.

## 09 — Exact relationships

**Focus:** Equal-radius and parallel-plane relationships with residuals.

**Narration:**

Reused fits are independent unless a relationship says otherwise. Here, selected
cylinder families share exact radii, while the plate top and chosen shoulder
planes share an exact direction. The taller boss remains offset but parallel.
These declarations influence the fitted quantities; they are different from a
later tolerance check.

## 10 — Relationship graph

**Focus:** Relationships lens across generated and user-authored features.

**Narration:**

The relationship lens shows those cross-feature declarations alongside generated
ownership. It makes the distinction visible: dependency edges describe
evaluation order and lineage, while relationship edges say which geometric
quantities are solved together. A joint is available when several declarations
need one explicit combined solve.

## 11 — Measurable datums

**Focus:** Sphere fits, point datums, directed axis, frame, and scale.

**Narration:**

The three sphere fits provide measurable centers. Two centers initialize point
datums, and the ordered pair defines a directed axis. A coordinate frame maps
chosen direction references onto explicit output axes. Separately, one or more
known distances between point datums determine a least-squares uniform scale,
so extra readings can contribute rather than being discarded.

## 12 — Applied transform and export

**Focus:** Select Output transform, switch to Top, then open Rhino export.

**Narration:**

The Output transform composes the chosen frame and scale. Selecting it applies
that similarity transform to the view: the model moves into its intended output
orientation and size, while upstream fitted values remain in source coordinates.
The same transform is available at export, together with an explicit unit choice,
so the mesh, analytic guides, residual markers, and exported geometry share one
placement.

## 13 — Close

**Focus:** Aligned model with the combined graph.

**Narration:**

Together, the two examples show the current direction for Scansor: preserve user
observations, fit simple analytic primitives, express exact geometric intent,
reuse selection effort without freezing local fits, and make output scale and
coordinates explicit. The prototype is still evolving, but these operations are
working, inspectable graph features rather than a flattened final result.
