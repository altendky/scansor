# Captured examples

Small, named input datasets for exploratory workflows live here. Keep source
geometry and capture/export metadata separate from source-bound selections and
processing results. Reusable experiment scripts live in `experiments/`; generated
outputs normally go under ignored `local-inputs/`.

These are concrete datasets, not the provisional canonical example-record schema
or evidence of physical accuracy. Dataset-specific notes describe known units,
provenance, limitations, and how to reproduce an experiment.

- [nozzle-bayonette-simplified](nozzle-bayonette-simplified/README.md): reduced
  handheld fan nozzle mesh with saved outer-wall and top-face selections for
  joint cone/plane fitting and cylinder comparisons, plus a broader retained
  interactive-selection demo recipe.
- [repeated-boss-selection](repeated-boss-selection/README.md): deterministic
  analytic plate-and-boss fixture recipe with exact, coarse, fine, and separately
  posed rescan realizations for repeated-feature and selection-transfer work,
  with a checked-in end-to-end reuse, relationship, calibration, and transform
  demo recipe.
