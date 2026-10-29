"""Shared numerical evidence for seed surfaces, previews and positional Fits."""

# Reuse the exact bounded source fixture already packaged for entry parity.
# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any, cast

from experiments.browser_runtime.selection_entries_probe import (
    _scan,
    _source,
)
from experiments.feature_graph import (
    FeatureGraph,
    Recipe,
    Selection,
    SurfaceFit,
    SurfaceSelection,
)
from experiments.seed_surface_authoring import (
    SurfaceSelectionPreviewRequest,
    prepare_surface_selection_preview,
)


def seed_surface_probe() -> list[dict[str, Any]]:
    scan = _scan(5)
    reports: list[dict[str, Any]] = []
    for setback in (0.0, 0.25):
        barriers = ["barrier"] if setback == 0 else []
        recipe = Recipe(
            schema_version=2,
            nodes=[
                _source(scan),
                Selection(
                    id="seeds",
                    label="Seed patch",
                    operation="selection",
                    source="scan",
                    ids=[6],
                ),
                Selection(
                    id="barrier",
                    label="Barrier strip",
                    operation="selection",
                    source="scan",
                    ids=[2, 7, 12, 17, 22],
                ),
                SurfaceSelection(
                    id="surface",
                    label="Seed surface",
                    operation="surface_selection",
                    seed_selection="seeds",
                    barriers=barriers,
                    setback=setback,
                ),
                SurfaceFit(
                    id="plane",
                    label="Independent positional plane",
                    operation="fit",
                    selections=["surface"],
                    kind="plane",
                ),
            ],
            output="plane",
        )
        graph = FeatureGraph(scan, recipe)
        token = str(graph.snapshot()["token"])
        _ = graph.ensure_current(token, ["seeds", "barrier"])
        before = graph.snapshot()
        preview = prepare_surface_selection_preview(
            graph,
            SurfaceSelectionPreviewRequest(
                token=token,
                seed_selection="seeds",
                barriers=barriers,
                setback=setback,
            ),
        )(None)
        assert graph.snapshot() == before
        ready = cast(dict[str, Any], graph.ensure_current(token, ["plane"]))
        assert not ready["errors"] and set(ready["states"].values()) == {"ready"}
        saved = graph.export_recipe()
        assert "ids" not in saved["nodes"][3]
        replay = FeatureGraph(scan, Recipe.model_validate(saved))
        restored = cast(
            dict[str, Any],
            replay.ensure_current(str(replay.snapshot()["token"]), ["plane"]),
        )
        assert restored["memberships"]["surface"] == ready["memberships"]["surface"]
        fitted = ready["results"]["plane"]
        reports.append(
            {
                "setback": setback,
                "preview": preview["preview"],
                "ids": ready["memberships"]["surface"],
                "reopened_ids": restored["memberships"]["surface"],
                "recipe": saved,
                "fit": {
                    **{key: fitted[key] for key in ("kind", "ids", "weighted_rms")},
                    "plane_equation": fitted.get(
                        "plane_equation", fitted["parameters"][:4]
                    ),
                },
            }
        )
    return reports
