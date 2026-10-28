"""Small independent physical fixture shared by native and worker UI checks."""

from __future__ import annotations

import base64
import json
from copy import deepcopy

from experiments.feature_graph import FeatureGraph, Recipe
from experiments.selection_volume_operations import dispatch
from tests.circular_reuse_fixture import circular_fixture


def main() -> None:
    raw, workspace, recipe, circular, expected = circular_fixture()
    payload = recipe.model_dump()
    extra = deepcopy(
        next(node for node in payload["nodes"] if node["id"] == "bore_input")
    )
    extra.update(id="bore_extra_input", label="Additional bore source observations")
    for entry in extra["entries"]:
        entry["volumes"] = []
    payload["nodes"].insert(
        next(
            index for index, node in enumerate(payload["nodes"]) if node["id"] == "bore"
        ),
        extra,
    )
    graph = FeatureGraph(workspace, Recipe.model_validate(payload))
    _ = dispatch(
        graph,
        "authorSelectionVolume",
        {
            "token": graph.snapshot()["token"],
            "selection": extra["id"],
            "mode": "create",
            "volume": "bore_extra_volume",
            "label": "Additional bore footprint",
            "kind": "mesh",
            "options": {
                "inward_thickness": 0.08,
                "outward_thickness": 0.08,
                "edge_padding": 0.08,
                "normal_angle_degrees": 15,
            },
        },
    )
    recipe = Recipe.model_validate(graph.snapshot()["recipe"])
    added = next(
        node for node in recipe.model_dump()["nodes"] if node["id"] == extra["id"]
    )
    print(
        json.dumps(
            {
                "filename": "circular-holes.ply",
                "raw_base64": base64.b64encode(raw).decode(),
                "recipe": recipe.model_dump(mode="json"),
                "extra_volume": {
                    "selection": extra["id"],
                    "entry": added["entries"][0]["id"],
                    "volume": "bore_extra_volume",
                },
                "circular": circular,
                "expected_ids": expected,
            },
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
