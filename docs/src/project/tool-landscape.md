# Tool Landscape

## Status and Scope

**Research finding, snapshot dated 2026-09-13.** This survey compares existing
user-facing applications and packaged plugins. It is a documentation and source
review, not a hands-on product evaluation or an exhaustive market report.
Research algorithms and custom optimizer construction are not counted as
ready-made alternatives.

The primary requirement is **joint constrained fitting**: select observations
for several surfaces, declare relationships, and fit their geometry together.
Accessibility means both manageable cost (free or roughly under US$500) and a
usable interface. A GUI alone does not establish ease of use. Annual subscriptions
and perpetual purchases are reported separately; the user has not selected one
budget interpretation. Personal-use restrictions also matter.

Scansor's [product contract](terminology-and-scope.md) remains an intended
direction. Its [browser experiment](../../../experiments/browser_viewer/README.md)
provides bounded interaction and fitting evidence, not a verified replacement for
the applications below.

## Main Finding

No approachable, ready-made application under US$500 has been verified to provide
Scansor-equivalent joint fitting of selected surface observations. This is an
**unverified accessibility gap**, not proof that no such application exists.
No exclusive Scansor capability has been established.

JUniForm is a free desktop application with a genuine multi-primitive constrained
adjustment interface. It is a technical specialist application: users configure
mathematical parameters, initial estimates, and restriction equations. It should
not be presented as an equally approachable alternative to selecting surfaces and
choosing named coaxial or perpendicular relationships.

Artec Studio, QUICKSURFACE, and Mesh2Surface are especially relevant commercial
comparators. Their documentation establishes inter-primitive constraints or
constrained refitting. Whether their workflows optimize all original surface
observations together with reciprocal influence remains to be tested.

No reviewed product has been shown to satisfy the complete Scansor contract of
explicit model and observation intent, parameter roles and bounds, relationship
roles, robust joint fitting, diagnostics, point accounting, and traceable CAD
publication. That broad conjunction is not itself evidence of a useful product
distinction. Joint fitting and its usability are the priority for comparison.

## What Counts as Joint Fitting

Three workflows must be distinguished:

1. Fit one surface, then constrain a second surface to the first surface's fixed
   geometry.
2. Fit surfaces independently, then adjust the fitted primitives to satisfy
   relationships.
3. Optimize the original observation residuals for all participating surfaces
   together under the relationships, allowing shared geometry to move.

The third is the relevant Scansor comparison. For example, observations of both
cylinders and an end plane should influence a common axis while the radii and
plane offset are estimated. Terms such as "parametric," "constraints," and "refit"
do not by themselves identify which workflow is implemented.

## Closest Commercial Applications

All products in this table are proprietary. Prices are advertised amounts
observed on the snapshot date, not confirmed checkout totals. Editions, taxes,
host applications, and license terms require checking before purchase. Capability
descriptions apply to the documented product family, not automatically to its
cheapest edition; some detailed vendor manuals describe older versions.

| Application | Documented overlap | Access and unresolved comparison |
| --- | --- | --- |
| [Artec Studio](https://docs.artec3d.com/as/20/en/cad.html) | Selected-region primitive fitting; parallel, perpendicular, angular and coaxial relationships; fixed/equal dimensions; locking and refitting; visible constraint failures | [Pro US$1,700/year or US$4,300 perpetual for Studio 20](https://www.artec3d.com/prices). Strong GUI comparator; joint observation objective unverified. |
| [QUICKSURFACE](https://www.quicksurface3d.com/3d-scanning-and-reverse-engineering/?p=product_quicksurface) | Constrained best fit and primitive parallelism, perpendicularity, coincidence, and offset, beyond sketch constraints alone | [Pro €1,700/year or €5,250 perpetual; Lite €480/year](https://www.quicksurface.com/price/). Reciprocal joint optimization and edition-specific fitting controls need evaluation. |
| [Mesh2Surface for Rhino](https://mesh2surface.com/help/Helpfile.pdf) | Primitive fitting rerun with orientation constraints; fitted geometry as a modeling reference | [€1,245 perpetual, excluding taxes](https://www.mesh2surface.com/pricing-mesh2surface-for-rhinoceros/), plus Rhino. Constrained individual refitting documented; complete joint objective unverified. |
| [Geomagic Design X](https://hexagon.com/products/geomagic-design-x/geomagic-design-x-plans) | Scan regions, primitive/surface fitting, constrained sketches, feature-based reconstruction, deviation analysis, and CAD history transfer | Go starts at US$1,900/year; Pro requires a quote. Broader reconstruction scope; joint fitting semantics unverified. |
| [SpatialAnalyzer](https://www.spatialanalyzer.com/about/newsletterarticlerelationshipsinsa.php) | Simultaneous weighted relationships, constrained fitting, source associations, and measurement diagnostics | [Quote required](https://www.kinematics.com/request/index.php). Relationship minimization examples emphasize transformations/alignment, not verified joint estimation of changing shape dimensions. |
| [PolyWorks Inspector](https://www.polyworks.com/en-us/products/polyworks-inspector) and [Modeler](https://www.polyworks.com/en-us/products/polyworks-modeler) | Inspector: constrained/weighted alignment and measurement. Modeler: geometric extraction and constrained parametric sketches for CAD | Separate commercial products, sales contact; no official public price verified. Coupled 3D shape-fitting workflow unverified. |

Artec's [edition comparison](https://www.artec3d.com/3d-software/artec-studio/prices)
states that its 30-day Pro trial cannot save projects or export. A trial can
provide interface evidence without being a usable free production edition.

## Free and Lower-Cost Candidates

| Application | Access | Joint-fitting finding |
| --- | --- | --- |
| [JUniForm, distributed with JAG3D](https://github.com/applied-geodesy/jag3d) | Free, open source, GPLv3 | Actual GUI multi-primitive constrained adjustment, verified by tracing UI and solver source. Technical setup; nozzle configuration and practical usability untested. |
| [CloudCompare](https://www.cloudcompare.org/main.html) | Free, open source, GPL, including commercial use | Selection, inspection, segmentation, and [RANSAC primitive detection with supporting point subsets](https://www.cloudcompare.org/doc/wiki/index.php/RANSAC_Shape_Detection_%28plugin%29). No equivalent GUI joint constraint workflow verified. |
| [FreeCAD with Detessellate](https://github.com/DesignWeaver3D/Detessellate) | Free, open source; Detessellate is LGPL-2.1 | Newer workbench. [Selected points can produce a fitted datum plane and sketch references](https://github.com/DesignWeaver3D/Detessellate/blob/main/Documentation/PointPlaneSketch.md); full constrained model-to-observation fitting unverified. |
| [ZEISS INSPECT Optical 3D](https://www.zeiss.com/metrology/en/software/zeiss-inspect/zeiss-inspect-optical-3d.html) | Proprietary, ongoing limited free edition; Pro quote-based | Useful inspection, deviation, and reporting comparator. Neither joint shape fitting nor all paid capabilities are established for the free edition. |
| [Artec Studio Lite Individual](https://www.artec3d.com/prices) | Proprietary, US$480/year or US$48/month, personal/noncommercial | Lower-cost lead only. Precise Lite constraint availability, import/export boundaries, and equivalent joint objective were not verified. Photogrammetry positioning does not establish support of the proposed workflow. |
| [QUICKSURFACE Personal](https://www.quicksurface.com/quicksurface-personal/) | Proprietary, €20/month paid annually (€240/year), personal/noncommercial | Primitive fitting listed, but reciprocal joint fitting unverified. STL-only export significantly limits CAD use. |

The QUICKSURFACE Personal headline price conflicts with a FAQ reference to a €200
upgrade credit. The annual figure above follows the headline; checkout and upgrade
terms were not tested. Its current
[edition matrix](https://www.quicksurface.com/wp-content/uploads/2026/01/QS_Personal_vs_Lite_vs_Pro_single_bitmap_UNTRIMMED.png)
was visually inspected for fitting/export availability but does not resolve
joint-fitting semantics.

GPL/LGPL entries are comparisons of external applications, not selections of
dependencies for Scansor. Proposed incorporation must respect the project's
dependency-license boundary.

### JUniForm: Capability Versus Usability

JUniForm exposes a user-defined surface, multiple named primitives, per-primitive
point assignment, parameter editing, and restriction editors. Source review is
pinned to commit `b11b26bc737516f2249374d1405e04014162e313`:

- [The adjustment gathers observations from all primitives](https://github.com/applied-geodesy/jag3d/blob/b11b26bc737516f2249374d1405e04014162e313/JAG3D/src/org/applied_geodesy/adjustment/geometry/FeatureAdjustment.java#L113-L145).
- [One system includes observation residuals and parameter restrictions](https://github.com/applied-geodesy/jag3d/blob/b11b26bc737516f2249374d1405e04014162e313/JAG3D/src/org/applied_geodesy/adjustment/geometry/FeatureAdjustment.java#L605-L688).
- [The GUI distinguishes restrictions from post-processing](https://github.com/applied-geodesy/jag3d/blob/b11b26bc737516f2249374d1405e04014162e313/JAG3D/src/org/applied_geodesy/juniform/ui/dialog/RestrictionDialog.java#L531-L543).

This verifies functionality exposed by an application, not a proposal to assemble
an optimizer. It does not show that the nozzle recipe works conveniently.
User-defined surfaces need initial estimates, and the general cylinder primitive
needs additional restrictions for circularity. A complete coaxial-cylinder/plane
configuration has not been executed.

The best verified interface examples are the developer's
[English sphere-fitting walkthrough](https://software.applied-geodesy.org/forum/?id=4512&mode=thread)
and a [German cylinder discussion](https://software.applied-geodesy.org/forum/?id=14922&mode=thread).
The former includes point data and screenshots for fixing a radius. The latter
contains parameter results and externally plotted geometry; not every image is
the JUniForm interface. In November 2025 the developer stated that JUniForm had
no wiki. No polished interface tour or multi-surface constraint walkthrough was
found in this survey.

## Other Packaged Applications and Adjacent Workflows

These remain visible as market context, not verified joint-fitting substitutes:

| Application | Role and access |
| --- | --- |
| [Creaform Scan-to-CAD / Pro](https://www.creaform3d.com/en/products/software/creaform-metrology-suite/scan-to-cad-software-module) | Mesh preparation, geometric extraction and CAD transfer; Pro adds sketching/modeling. Commercial, sales contact; associated with the earlier VXmodel workflow. |
| [SHINING 3D EXModel / Pro](https://www.shining3d.com/reverse-engineering/exmodel) | Primitive extraction, constrained sketching, surfacing, and scanner integration. Commercial, quote-based, trial available. |
| [Revopoint Revo Design / Pro](https://global.revopoint3d.com/en-uk/products/revodesign?variant=45660663087339) | Commercial editions with public store pricing. Revopoint identifies Revo Design as powered by QUICKSURFACE; not independent evidence of another fitting mechanism. |
| [Xtract3D 2](https://www.polyga.com/w3/xtract3d2//faq/) | SOLIDWORKS scan referencing, extraction and manual reconstruction. [US$899/year or US$99/month](https://shop.polyga.com/buy/) in the Polyga suite, plus SOLIDWORKS. |
| [Verisurf](https://www.verisurf.com/solution-suites/3d-scanning-reverse-engineering/) | Commercial inspection/reverse-engineering suite with primitive fitting and extraction; quote-based. |
| [Leica Cyclone 3DR](https://leica-geosystems.com/en-gb/products/laser-scanners/software/leica-cyclone/leica-cyclone-3dr) | Commercial point-cloud processing, inspection and CAD reconstruction, including larger scanned scenes. |

Rhino alone, custom Grasshopper/Galapagos definitions, and generic optimization
libraries are not counted as ready-made joint-fitting solutions. OpenVSP has a
real [Fit Model desktop workflow](https://www.nasa.gov/reference/openvsp-meshes-and-point-clouds/),
but its aircraft geometry focus makes it adjacent rather than a general
mechanical-part recommendation. RealityScan is an observation-generation source,
not a verified substitute for declared constrained fitting.

## Overlap and Candidate Differentiation

Selected-region primitive fitting, constrained orientations/dimensions, deviation
maps, CAD export, and broadly repeatable workflows already exist in user-facing
products. Open-source availability also is not unique across the wider market.

Potential distinctions requiring direct evidence are:

- An approachable interface for jointly fitting related surfaces while all their
  observations influence the shared geometry.
- Explicit fixed/free/derived parameters, bounds, and hard/soft/diagnostic-only
  relationships presented as one coherent fitting workflow.
- Explanations of poorly determined parameters even when residuals are small.
- Inspectable observation contributions and exclusions, held-out isolation, and
  enough retained information to reproduce and audit a result.

These are candidate benefits, not exclusive features or claims that Scansor has
completed them. Broad "traceability" and "diagnostics" claims need careful
comparison with mature metrology applications.

## Decisive Evaluation

The first free candidate is JUniForm, with usability treated as an explicit test.
Artec Studio and QUICKSURFACE are priorities for an approachable commercial
comparison; Mesh2Surface is useful if Rhino is already available. No purchase,
vendor contact, application installation, or hands-on benchmark occurred in this
survey.

1. Fit two noisy cylinders and an end plane, leaving radii, shared axis, and plane
   offset free under coaxial and perpendicular relationships.
2. Perturb only one region's observations. Check that the shared solution responds
   to a combined observation objective, rather than retaining a reference fit.
3. Assess how users express fixed dimensions and the nozzle's repeated-surface
   relationships, including initialization and failure recovery.
4. Introduce poor coverage, incompatible constraints, and a localized defect.
   Compare explanations, point handling, and retained rerun information.
5. Verify usable geometry export and the exact edition/license cost. Record setup
   effort separately from numerical success.

This can distinguish a missing capability from an existing capability with a
different interface, price, or evidence model. It cannot establish physical
accuracy without separate physical validation.
