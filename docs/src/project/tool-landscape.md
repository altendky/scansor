# Tool Landscape

## Status and Scope

**Research finding, affordable-tool update dated 2026-10-31.** This survey
compares existing user-facing applications and packaged plugins. It is a
documentation and source review, not a hands-on product evaluation or an
exhaustive market report. Research algorithms and custom optimizer construction
are not counted as ready-made alternatives. The enterprise comparison retains
its separately dated 2026-09-13 pricing snapshot.

Two independently useful roles are in scope:

- **Fitting and idealization:** select scan observations, fit analytic geometry,
  and enforce intended relationships such as parallelism, perpendicularity,
  coaxiality, symmetry, or matching sizes. Joint fitting, sequential fitting
  against fixed references, and adjustment after fitting are distinguished.
- **Assisted CAD reconstruction:** extract sections, curves, or primitives from
  a scan, then rebuild sketches, surfaces, and solids with CAD constraints and
  editable features.

The affordable comparison targets publicly obtainable free software, qualifying
free personal editions, modest one-time purchases (roughly under US$500), and
subscriptions costing tens rather than hundreds of dollars per year. Required
host CAD and equipment count toward access cost. Temporary trials, short passes,
personal-use restrictions, public-document requirements, and usable export are
reported separately. A GUI alone does not establish ease of use.

**Documented** means a primary source states the capability; **source-reviewed**
identifies bounded implementation evidence; **inferred** identifies an assessment
from that evidence; **unverified** means the evidence does not establish the
claim. Documentation and source inspection do not establish practical usability,
physical accuracy, or successful completion of a representative scan workflow.

Scansor's [product contract](terminology-and-scope.md) remains an intended
direction. Its [browser experiment](../../../experiments/browser_viewer/README.md)
provides bounded interaction and fitting evidence, not a verified replacement for
the applications below.

## Main Finding

**Affordable direct competition exists for substantial parts of Scansor's
intended job.** ScanRuler and FreeCAD's ScanAlignment workbench already address
selected scan regions, analytic geometry, and intended relationships. ScanRuler
also documents a constrained section-sketch solver and solid reconstruction.
They are partial substitutes with different boundaries, not merely mesh-cleanup
tools. The earlier broad accessibility-gap framing is too narrow for this scope.

No approachable, inexpensive application has yet been verified here to jointly
fit multiple 3D surface regions and produce the complete intended Scansor result.
That is an unresolved comparison, not proof of a missing market capability or
exclusive Scansor advantage. Coupled fitting already exists in JUniForm and in
ScanRuler's bounded 2D sketch workflow.

JUniForm is a free desktop application with a genuine multi-primitive constrained
adjustment interface. It is a technical specialist application: users configure
mathematical parameters, initial estimates, and restriction equations. It should
not be presented as an equally approachable alternative to selecting surfaces and
choosing named coaxial or perpendicular relationships.

Artec Studio, QUICKSURFACE, and Mesh2Surface are especially relevant commercial
comparators. Their documentation establishes inter-primitive constraints or
constrained refitting. Whether their workflows optimize all original surface
observations together with reciprocal influence remains to be tested.

The conjunction of every proposed Scansor requirement is not a useful market
exclusion test. A simpler sequential or reconstruction workflow may solve the
same user problem with acceptable accuracy and less effort. Any proposed benefit
from joint fitting, diagnostics, or retained observation intent needs a measured
comparison with these existing alternatives.

## What Counts as Joint Fitting

Four workflows must be distinguished:

1. Fit one surface, then constrain a second surface to the first surface's fixed
   geometry.
2. Fit surfaces independently, then adjust the fitted primitives to satisfy
   relationships.
3. Optimize the original observation residuals for all participating surfaces
   together under the relationships, allowing shared geometry to move.
4. Extract scan sections or reference geometry, then rebuild a constrained CAD
   model. Some sketch solvers retain section observations during fitting; others
   constrain only the already extracted geometry.

The third is the closest comparison to Scansor's intended joint fit. Observations
of both cylinders and an end plane should influence a common axis while the
radii and plane offset are estimated. Terms such as "parametric," "constraints,"
and "refit" do not by themselves identify which workflow is implemented. GD&T checks that
report parallelism or coaxiality are not necessarily constraints that change the
fit. Likewise, a solver's high-weight constraint residual is not an
exact-by-construction relationship, even if its interface describes it as exact.

## Affordable Fitting and Idealization

Access and evidence in this section were checked on 2026-10-31. Unless noted,
these are documentation findings; no application was installed or benchmarked.

| Application | Access | Fitting mechanism and output | Comparison boundary |
| --- | --- | --- | --- |
| [JUniForm / JAG3D](https://github.com/applied-geodesy/jag3d) | Free standalone Java application; GPLv3 | Source-reviewed multi-primitive adjustment with assigned observations and parameter restrictions; numerical estimates and diagnostics | Genuine joint-fitting comparator. Technical parameter/equation setup; practical CAD handoff and turnkey STEP-solid output unverified. |
| [FreeCAD + ScanAlignment](https://github.com/rat-designs/ScanAlignment-Workbench) | Free CAD host and LGPL-2.1-or-later workbench; public beta with manual installation | Paint mesh zones; extract planes/cylinders, perpendicular and mutually perpendicular planes, midplanes, and reference-constrained cylinder axes; align the scan | Source-reviewed sequential extraction against fixed references. Supplies datums and cylinder-radius information for reconstruction, rather than a finished parametric body. |
| [ScanRuler: 3D Measure](https://github.com/CNCKitchen/ScanRuler) | Free browser application; public AGPL source | Fit scan regions, then align exported elements parallel/perpendicular to reference planes or supply an assumed diameter; analytic STEP geometry | Post-fit idealization preserves the original measurements. This mode does not jointly refit the participating surface observations. |
| [ScanRuler: Reverse engineer](https://github.com/CNCKitchen/ScanRuler/tree/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/plugins/reverse) | Public AGPL plugin in the same free application; manual controls and optional AI-agent control | Fit lines/arcs to scan sections, retain slice observations while applying sketch relations/dimensions, then construct solids and export STEP | Source-reviewed coupled 2D sketch fitting with numerically enforced constraints. General joint fitting of 3D planes/cylinders remains unverified. |
| [Onshape + FitCylinder custom feature](https://forum.onshape.com/discussion/32041/fit-cylinder-mesh-tool-custom-feature) | [Free plan](https://www.onshape.com/en/products/free) permits noncommercial work with public documents | Author documents vertex/raycast cylinder or cone fitting, outlier rejection, axis locking to reference geometry, solid/surface output, and error readouts | Specialized sequential axis-constrained fitting; new custom feature, untested here. A public document requirement may rule out a particular scan. |

### ScanRuler: Distinguish Measurement and Reconstruction

The [measurement manual](https://github.com/CNCKitchen/ScanRuler/blob/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/docs/manual.md)
describes independent fits followed by an optional designed orientation or
diameter for exported geometry. Changing a fitted element does not jointly refit
the others. GD&T measurements and those geometry adjustments are separate tools.

The newer [reverse-engineering workflow](https://github.com/CNCKitchen/ScanRuler/blob/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/plugins/reverse/README.md)
has materially broader overlap: fitted sketch pieces retain their slice points,
and relations and dimensions participate in subsequent solves. It documents
parallel, perpendicular, tangent, concentric, and symmetric relationships, nominal
dimension suggestions, editable modeling history, and solid construction.
Describing the entire application as independent fits followed by adjustment
would miss this second workflow.

[Solver source](https://github.com/CNCKitchen/ScanRuler/blob/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/plugins/reverse/core/solver.ts#L2-L69)
and its [residual construction](https://github.com/CNCKitchen/ScanRuler/blob/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/plugins/reverse/core/solver.ts#L244-L431)
establish a common least-squares system for sketch parameters, observation
residuals, and relationships. It uses high constraint weights, an unmet-constraint
tolerance, and at most 240 slice points per piece. A final
[closure pass](https://github.com/CNCKitchen/ScanRuler/blob/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/plugins/reverse/core/solver.ts#L854-L868)
can omit slice points. This establishes bounded coupled fitting, not exact
symbolic constraint elimination or full-data joint 3D fitting. Representative
results and constraint conflicts still need testing.

The [license description](https://github.com/CNCKitchen/ScanRuler/blob/2cd89c6769a10c27ee7754b477ada521c9ef5cb7/README.md#license)
covers the public measurement, reverse-engineering, and mesh-tools code under
AGPL, with separate third-party notices and commercial licensing. It also notes
an additional hosted beta workspace outside this repository/license. Public
source availability should therefore be stated for the reviewed components,
rather than assumed for every hosted feature. Connecting an external AI agent
also changes the otherwise local processing boundary.

### ScanAlignment: Fixed References Rather Than Reciprocal Fitting

The reviewed implementation [projects an independently fitted plane normal](https://github.com/rat-designs/ScanAlignment-Workbench/blob/be76cf8ca808e6c37c90e6f8985dae6fc4b244a9/ScanAlignment.py#L209-L257)
perpendicular to an existing reference; constrained least-squares optimality is
not established by that operation. The third perpendicular plane takes its normal
from two existing reference normals. The [constrained cylinder](https://github.com/rat-designs/ScanAlignment-Workbench/blob/be76cf8ca808e6c37c90e6f8985dae6fc4b244a9/ScanAlignment.py#L327-L390)
uses a reference-plane direction and a projected algebraic circle fit. Existing
references do not move in response to the new zone's data. These are useful
sequential extractions, not a joint adjustment of every surface's observations.
Mesh alignment subsequently moves the main mesh while extracted zones/datums
stay in their original locations; the documented workflow then models from the
aligned global planes.

### Public History and Maturity

| Project | Earliest visible repository history | What the date establishes |
| --- | --- | --- |
| ScanRuler | [2026-08-13 initial commit](https://github.com/CNCKitchen/ScanRuler/commit/728645c45f86b7b3fd089223353467e6109b58c3); [public reverse plugin in 0.9.0, 2026-10-31](https://github.com/CNCKitchen/ScanRuler/commit/9825843d2b698fe16e419bc4bb4ba71f064a977e) | Initially a browser ball-bar scan evaluator. The reviewed reconstruction plugin is newer than the repository origin and the September survey. |
| ScanAlignment | [2026-07-04 initial commit](https://github.com/rat-designs/ScanAlignment-Workbench/commit/738143fc9b0aa088e22e66cdec4c777c9c0d4ec8); [first alpha 2026-07-10](https://github.com/rat-designs/ScanAlignment-Workbench/releases/tag/v2026.1-Alpha) | Public workbench history and alpha publication; not a mature-product or long-term-support claim. |

Both have months rather than years of visible project history at this snapshot.
Private development may predate these records; repository age does not date each
capability or establish reliability.

## Affordable Assisted CAD Reconstruction

These workflows are independently useful even when no joint fit is established.
The intended product is editable CAD with exact modeled relationships; that does
not mean every scan observation remains an input to the final CAD constraint
solver. Prices/access were checked on 2026-10-31 and exclude tax where applicable.

| Application | Access and usable output | Documented workflow | Limit or unresolved evidence |
| --- | --- | --- | --- |
| [FreeCAD + Detessellate](https://github.com/DesignWeaver3D/Detessellate) | Free host/workbench; LGPL-2.1; native CAD and STEP via FreeCAD | [PointPlaneSketch](https://github.com/DesignWeaver3D/Detessellate/blob/main/Documentation/PointPlaneSketch.md) fits datum planes from selected point-cloud observations and supplies sketch references; rebuild constrained profiles and features | PointPlaneSketch addresses noisy data; CoplanarSketch and other tools can target cleaner tessellations. No general joint multi-surface fitting verified. |
| [FreeCAD + MeshRemodel](https://github.com/mwganson/MeshRemodel) | Free host/workbench; LGPL-2.1; FreeCAD modeling/export | Select mesh vertices/edges, construct curves and sketches, then rebuild with geometric/dimensional constraints | Mostly manual reference construction. Noise handling and user effort need evaluation. |
| [Fusion Personal](https://www.autodesk.com/products/fusion-360/personal) | Free qualifying personal/noncommercial edition; native history and [STEP export](https://help.autodesk.com/view/fusion360/ENU/?caas=caas/sfdcarticles/sfdcarticles/Fusion-360-Free-License-Changes.html) | [Mesh sections](https://help.autodesk.com/cloudhelp/ENU/Fusion-Mesh/files/MESH-CREATE-MESH-SECTION-SKETCH.htm) → fitted sketch curves → constraints/dimensions → extrude/revolve/loft | Assisted section reconstruction; paid prismatic conversion must not be attributed to Personal. Official pages disagree on license term length, so no renewal term is assumed here. |
| [SOLIDWORKS Makers desktop](https://www.solidworks.com/solution/solidworks-makers) | US$48/year regular; US$24/year advertised promotion; personal/revenue restrictions; [STEP/IGES export and native-file limitations](https://www.solidworks.com/solution/solidworks-makers/3dexperience-solidworks-makers) | [ScanTo3D](https://help.solidworks.com/2026/english/solidworks/ScanTo3D/c_Scanto3d_overview.htm) prepares scan meshes/clouds and reconstructs surfaces/solids; ordinary CAD features supply design relationships | ScanTo3D's Professional-edition support is documented; exact current Maker entitlement is inferred from package identity and reported inclusion, so verify before purchase. Desktop and browser offerings differ. |
| [Solid Edge Community](https://resources.sw.siemens.com/en-US/download-solid-edge-community-edition/) | Free personal/noncommercial edition; native files incompatible with commercial editions | [Product-family tutorial](https://blogs.sw.siemens.com/solidedge/Reverse-Engineering-Tutorial-Part-1-Get-Your-Feet-Wet/) fits/extracts mesh-region surfaces, adds tangency afterward, and combines them into bodies | Exact current Community reverse-engineering commands and neutral export remain edition-specific verification items; general Solid Edge capability does not establish free-edition entitlement. |
| [InStep Studio 3D](https://instepstudio3d.com/) | US$149 perpetual with first-year updates; optional US$50/year maintenance for newer versions; alternative US$59/year; STEP/IGES/DXF | Analytic detection, sections/curves for downstream CAD, and NURBS resurfacing | [Its manual](https://www.solveering.com/instep/webhelp/features/features.aspx) warns that analytic detection primarily suits clear tessellated geometry and scan noise may prevent accurate recognition. Inter-surface constraints unverified. |
| [SolidCraft](https://solidcraft.io/) | Free public desktop/browser application; native history and advertised STEP output | [Command reference](https://docs.solidcraft.io/reference/commands.html#fit-curves-to-mesh-section) documents section-to-line/arc fitting followed by sketch constraints | Emerging assisted-workflow candidate; practical scan robustness and maturity unverified. |

ScanRuler's Reverse engineer workspace also belongs in this group: it documents
manual solid modeling over scan sections, optional agent control, deviation
comparison, and STEP export. A modeled STEP body is distinct from transferring a
native editable feature history to Fusion, FreeCAD, or another CAD system.

### Clean Tessellation Is Not a Noisy Scan

[MeshToFeatures](https://masoudmim.com/meshtofeatures/) is a free
LGPL-2.1-or-later FreeCAD workbench that recognizes analytic patches, regularizes
near-parallel/coaxial/equal-size geometry, and rebuilds native PartDesign features.
Its author explicitly excludes 3D-scanned shapes from the current supported
scope. It is relevant to geometry-to-feature-tree reconstruction, but is not a
verified raw-scan fitter. Likewise, InStep's analytic recognition must be tested
on noisy observations rather than inferred from success on CAD-generated STL.

Ordinary STEP exchange carries geometry rather than the originating CAD feature
history. Fusion's [import guidance](https://www.autodesk.com/products/fusion-360/blog/import-step-file-into-fusion-360/)
explains this boundary. Converting a mesh into many faceted CAD faces or a smooth
B-spline body does not by itself recover nominal dimensions or intended
relationships.

## Closest Commercial Applications

**Historical price snapshot: 2026-09-13, not refreshed in this update.** These are
capability comparators outside the affordable-access filter; quoted prices must
be rechecked if purchasing becomes relevant.

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

## Inspection Complements and Access Exclusions

The first four entries retain the 2026-09-13 snapshot; the remaining entries were
checked on 2026-10-31. Exclusion here concerns access or function, not quality.

| Application | Access | Role or exclusion |
| --- | --- | --- |
| [CloudCompare](https://www.cloudcompare.org/main.html) | Free, open source, GPL, including commercial use | Selection, inspection, segmentation, and [RANSAC primitive detection with supporting point subsets](https://www.cloudcompare.org/doc/wiki/index.php/RANSAC_Shape_Detection_%28plugin%29). No equivalent GUI joint constraint workflow verified. |
| [ZEISS INSPECT Optical 3D](https://www.zeiss.com/metrology/en/software/zeiss-inspect/zeiss-inspect-optical-3d.html) | Proprietary, ongoing limited free edition; Pro quote-based | Useful inspection, deviation, and reporting comparator. Neither joint shape fitting nor all paid capabilities are established for the free edition. |
| [Artec Studio Lite Individual](https://www.artec3d.com/prices) | Proprietary, US$480/year or US$48/month, personal/noncommercial | Excluded by recurring cost. Precise Lite constraint availability, import/export boundaries, and equivalent joint objective were not verified. |
| [QUICKSURFACE Personal](https://www.quicksurface.com/quicksurface-personal/) | Proprietary, €20/month paid annually (€240/year), personal/noncommercial | Excluded by recurring cost; STL-only export also limits CAD use. Joint fitting unverified. |
| [MeshInspector](https://meshinspector.com/pricing/) | Free Community; Makers US$300/year; Professional US$790/year | Community is a cleanup/measurement complement. Paid feature-detection/inspection capabilities cannot be assumed for Community and the subscriptions exceed the target. |
| [Able Mesh2CAD](https://www.ablesw.com/mesh2cad.html) | [US$145 personal or US$395 commercial perpetual](https://www.ablesw.com/purchase.html) | Affordable B-spline conversion; no documented parallel/perpendicular/equal-size constraint workflow established. Adjacent converter rather than equivalent idealization. |
| [Tesselform](https://www.tesselform.com/tools/features) | Proprietary; [short Suite passes](https://www.tesselform.com/pricing) US$56/week or US$150/month, nonrenewing | Relevant occasional-use comparator. Required annual/perpetual bundles exceed the target; temporary beta is not an ongoing free edition. |
| [BlacksmithAI](https://blacksmithai.org/pricing) | STL/STEP input advertised at US$49.99/month; request-beta-access entry | Excluded by recurring cost and unverified unrestricted public availability. The US$24.99 plan is photo-only; the one-time manufacturing offer is not a software license. |
| [ParameshAI](https://parameshai.com/) | Advertises US$2/part but still labels conversion private beta/waitlist | Availability and usable reconstruction remain unverified. Free mesh cleaning is a complement; advertised STEP-with-feature-tree claims are not established interchange behavior. |
| [REVERSCAD](https://github.com/Shugar86/reverscad) | Public MIT developer project; local Python/Node setup | Advertises section/sketch/solid reconstruction. [Its audit](https://github.com/Shugar86/reverscad/blob/main/docs/AUDIT.md) records pipeline/constraint integration gaps; conflicting completion claims need resolution before treating it as an established application. |

In the September snapshot, the QUICKSURFACE Personal headline price conflicted
with a FAQ reference to a €200 upgrade credit. The annual figure above follows
the headline; checkout and upgrade terms were not tested. The
[edition matrix](https://www.quicksurface.com/wp-content/uploads/2026/01/QS_Personal_vs_Lite_vs_Pro_single_bitmap_UNTRIMMED.png)
reviewed in September was visually inspected for fitting/export availability
but does not resolve joint-fitting semantics.

Tesselform's October pricing requires **Scan Processing plus Fit & Surface** for
fitting/export: US$400/year or US$1,200 perpetual at the introductory price,
US$800/year or US$2,400 perpetual at the advertised regular price. Adding the
Design workflow is covered by the Suite at US$600/year or US$1,800 perpetual
introductory pricing. These totals come from the public pricing configuration,
not currency conversion or completed checkout. The advertised beta ends at
2026-11-01 00:00 UTC; it must not be described as a continuing free tier.
Its analytic fitting, fixed diameters, orientation snapping, and nominal-size
regularization warrant evaluation, but joint multi-surface fitting is unverified.

GPL/LGPL/AGPL entries are comparisons of external applications, not selections of
dependencies for Scansor. Proposed incorporation must respect the project's
dependency-license boundary.

## JUniForm: Capability Versus Usability

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

These retain the 2026-09-13 snapshot as market context, not verified joint-fitting
substitutes:

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
products, including affordable public applications. ScanRuler and ScanAlignment
are direct competitors for parts of Scansor's intended job. ScanRuler's section
solver also prevents treating all affordable workflows as independent fitting
followed by arbitrary manual adjustment. Open-source availability is not unique.

Potential distinctions requiring direct evidence are:

- An approachable interface for jointly fitting related surfaces while all their
  observations influence the shared geometry.
- Explicit fixed/free/derived parameters, bounds, and hard/soft/diagnostic-only
  relationships presented as one coherent fitting workflow.
- Explanations of poorly determined parameters even when residuals are small.
- Inspectable observation contributions and exclusions, held-out isolation, and
  enough retained information to reproduce and audit a result.

These are candidate benefits, not exclusive features or claims that Scansor has
completed them. Joint fitting must demonstrate a useful improvement in results,
time, or effort over the simpler alternatives. Broad "traceability" and
"diagnostics" claims also need comparison with existing metrology applications.

## Decisive Evaluation

Evaluate the two roles separately:

- For fitting/idealization, prioritize ScanRuler and ScanAlignment, with JUniForm
  as a joint-adjustment comparator. Test ScanRuler's measurement and constrained
  sketch modes separately.
- For assisted reconstruction, compare ScanRuler's solid workflow and
  Detessellate/FreeCAD with Fusion's section workflow. Consider Makers after
  verifying its exact ScanTo3D entitlement, and InStep as a conversion bridge.
- Retain Artec Studio, QUICKSURFACE, and Mesh2Surface as commercial capability
  benchmarks, without confusing their prices or trials with affordable access.

No purchase, vendor contact, application installation, or hands-on benchmark
occurred in this survey; inspected upstream implementation/tests were not run.

1. For constrained sections, fit several noisy line/arc regions, impose
   parallel/perpendicular/equal-radius relationships, perturb just one region,
   and inspect reciprocal movement and observation residuals.
2. Separately fit two noisy cylinders and an end plane, leaving radii, shared
   axis, and plane offset free under coaxial and perpendicular relationships.
   Determine whether all participating observations influence shared geometry,
   whether references stay fixed, or whether geometry is adjusted afterward.
3. Rebuild the same part using assisted CAD reconstruction. Compare time, manual
   decisions, achieved relationships, and deviation against the scan rather than
   judging only whether a joint optimizer exists.
4. Exercise fixed dimensions, repeated features, poor coverage, incompatible
   constraints, localized defects, initialization, and failure recovery. Compare
   explanations and retained observation/exclusion information.
5. Save/reopen the native project and export/reimport usable geometry. Distinguish
   native feature history from STEP solids, and verify the exact edition/license
   cost. Record setup effort separately from numerical success.

This can distinguish a missing capability from an existing capability with a
different interface, price, or evidence model. It cannot establish physical
accuracy without separate physical validation.
