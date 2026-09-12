# Exploratory cone and perpendicular plane fit

The saved nozzle's current example workflow fits a right circular cone side with
a perpendicular top plane. A frustum is the finite supported portion of that
cone; its lateral surface is fitted here, without an apex or cap-distance model.
This experiment does not extend the synthetic-only product admission contract
or implement a general constraint system.

## Geometry and objective

Parameters in the saved local frame are `[cx, cy, a, b, R, h, k]`. The unit axis
is `normalize((a,b,1))`, with reference point `c=(cx,cy,0)`. Radius at axial
coordinate `z` is `R+k*z`; `R` is the radius at `c`. Signed taper `k` can be
positive or negative, and `atan(k)` is the signed cone half-angle. Zero taper is
exactly a cylinder, with no distant or undefined apex in the parameterization.
The axis parameterization does not cover axes parallel to local `z=0`.

For a lateral observation `p`, define `z=(p-c)·axis` and its radial distance `rho`.
The signed orthogonal side residual is `(rho-R-k*z)/sqrt(1+k*k)`. Its nearest
side point has axial coordinate `(z+k*(rho-R))/(1+k*k)`. Radial residuals are
recorded separately, so their meaning is not confused with orthogonal distance.

The plane equation is `p·axis=h` in the local frame. Its normal is the cone axis
by construction; position and orientation are jointly fitted. Its offset is not
fixed to a support endpoint. The optimizer minimizes the sum of squared signed
orthogonal distances, with the same globally normalized incident-area weights as
the cylinder/plane baseline. There is one additional fit parameter, taper.

## Finite support and validity

The example declares axial support `[-2,5]` in source units, measured from `c`
along the fitted unit axis. These conservative bounds enclose the selected band
and the top-plane visualization, and are not estimates of physical edges.
Reference radius and both endpoint radii must be strictly positive. Every lateral
observation's orthogonal projection must lie inside this interval. Invalid
initial parameters fail; invalid trial steps are rejected during backtracking.
There is no clipping to an edge, cap residual, sample removal, or apex crossing.
The example runner also checks that the plane/axis intersection lies in this
interval before extending the cone guide to the plane.

Selections use their original fixed reference axes and IDs, not the changing cone
axis. Geometric selection replay must reproduce both ID lists exactly. The
perpendicular plane has no fitted inner/outer boundary: its annular display bounds
are visualization choices, as in the preceding cylinder experiment.

## Reproduction and evidence

```sh
PYTHONPATH=src:. python -m experiments.run_nozzle_cone_plane \
  --output local-inputs/nozzle-cone-plane-fit
```

Use a new output directory. The model is retained in
`examples/nozzle-bayonette-simplified/models/cone-plane.json`; source and selections
are shared with the cylinder/plane comparison. Results include implementation,
model and selection hashes, all residuals/IDs/areas, three starting guesses,
independent world-space and nearest-point distance checks, and common fixed axial
bins for inspecting residual trends. All outputs remain outside tracked sources.

The CloudCompare view uses cyan for the cone guide and magenta for the plane.
Each surface's residual colors use their own symmetric scale; exact ranges are
written to `VIEW.txt`. No residuals are clipped. Guide tubes are display geometry,
not error bounds, and their radius changes with height to follow the fitted cone.

Tests recover generated positive, negative, near-zero and zero taper with varied
poses, nonuniform observation weights, short axial coverage and incomplete angular
coverage. Analytic derivatives are checked by finite differences, the zero-taper
residual/Jacobian matches the cylinder, invalid support/radii are rejected, and a
single axial ring is rejected as insufficient to determine radius and taper.
Saved-example tests verify the common-selection comparison, shared normal,
complete residual accounting, and tapered display guide geometry.

All reported dimensions are in unconfirmed source units. Better training residuals
with an extra parameter do not establish physical taper or accuracy, and no
held-out assessment is claimed. The top-plane diameter extrapolates beyond the
selected lateral band and must be interpreted accordingly.
