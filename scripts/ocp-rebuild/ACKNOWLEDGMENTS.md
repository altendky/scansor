# CAD runtime acknowledgments

This software is based in part on the work of the FreeType Team. FreeType
2.13.3 is used under the permissive FreeType License (FTL), with its warranty
disclaimer retained in `notices/cad/FreeType-FTL.txt`. The GPL alternative is
not selected. The [FreeType project](https://freetype.org/) supplies the engine;
Scansor does not claim authorship of its code. The contributed BDF, PCF, fthash
and conservative unused ft-hb notices are retained separately. The full source
archive preserves all original notices. HarfBuzz is disabled in the tested port;
no external HarfBuzz dependency is selected.

The CAD library uses the previously approved OCCT LGPL-2.1 lineage and header
exception, Apache-2.0 generated OCP bindings, MIT OCP.wasm packaging, and the
retained permissive dependency notices. Corresponding sources, modifications,
build controls and replacement instructions are included. These documents do
not select an alternative kernel or binding.

OCCT's full pinned source archive preserves the original per-file copyright
notices. The OSD_Process source used for the replacement marker credits Matra
Datavision (1998-1999) and OPEN CASCADE SAS (1999-2014), under LGPL-2.1 and the
retained OCCT exception. Its temporary change is recorded in the rebuild route.

The supplied build-control wrappers resolve their paths from their own location
and must be copied into a fresh disposable directory before execution. Their
repository copies have formatting/import, UTC spelling and equivalent conditional
and exception-suppression adjustments for Scansor's lint checks. Original probe
control hashes and distributed-control hashes remain recorded separately in
`cad-build-controls.json`. Kernel source, compiler/link options, upstream patch
and repair logic are unaffected by these wrapper changes. Historical publisher
toolchain reconstruction and byte-identical original-wheel reproduction are not
claimed.
