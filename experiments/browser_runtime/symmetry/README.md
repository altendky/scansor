# Frozen imported symmetry cases

These six synthetic scans exercise imported named rotational and mirror solves
for planes, cylinders and cones. PLY bytes retain the independent fixture's
selected rows, unselected triangle satellites and nonuniform incident-area
weights. They are generated from `tests/symmetry_fixture.py`; saved recipes are
authored by `symmetry_recipe` in `tests/test_scan_symmetry_graph.py`.

Each JSON record contains the saved recipe, selection IDs, independent geometric
truth, original weights, source hash and generation options. Truth is checked
only after fitting. It never supplies numerical starts. The runtime imports no
test code and reads the same frozen bytes in native Python and the worker.

The plane rotation uses three individually collinear patches. The fixed mirror
case uses authored ordered point references. The cone cases retain separate
finite source-Z support intervals for each member. Other member support is
derived automatically. Every case replays its saved recipe over renamed
identical source bytes. These are numerical integration fixtures, without a
physical scan accuracy claim.

The existing float64 `fixture.npz` generator remains unchanged. Runtime source
manifests bind these additional binary and JSON assets. Reproducibility tests
regenerate the source bytes and recipes independently of the numerical solver.
