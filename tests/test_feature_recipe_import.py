"""Display-name repair preserves saved feature identity and schema enforcement."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from experiments.feature_recipe_import import import_recipe, load_recipe


def point(identifier: str, label: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "label": label,
        "operation": "point",
        "initial_coordinates": [0, 0, 0],
    }


def test_import_reserves_later_names_and_preserves_identity_without_mutating_input() -> (
    None
):
    raw: dict[str, Any] = {
        "schema_version": 2,
        "nodes": [
            point("first", "Shape"),
            point("second", " shape "),
            point("later", "shape point"),
            point("fourth", "SHAPE"),
            {
                "id": "scale",
                "label": "Scale",
                "operation": "scale",
                "distances": [
                    {
                        "first_point": "first",
                        "second_point": "second",
                        "known_distance": 1,
                        "weight": 1,
                    }
                ],
            },
        ],
        "output": "fourth",
    }
    original = deepcopy(raw)
    recipe, warnings = import_recipe(raw)
    assert [node.label for node in recipe.nodes[:4]] == [
        "Shape",
        "shape point 2",
        "shape point",
        "SHAPE point 3",
    ]
    assert [warning["id"] for warning in warnings] == ["second", "fourth"]
    restored = recipe.model_dump(mode="json")
    for old, new in zip(original["nodes"], restored["nodes"], strict=True):
        assert {key: value for key, value in old.items() if key != "label"} == {
            key: new[key] for key in old if key != "label"
        }
    assert recipe.output == original["output"]
    assert raw == original
    replayed, replay_warnings = import_recipe(restored)
    assert replayed == recipe and replay_warnings == []


def test_import_repairs_group_names_separately_and_keeps_memberships() -> None:
    raw: dict[str, Any] = {
        "nodes": [{**point("first", "Set"), "group_id": "g2"}],
        "groups": [
            {"id": "g1", "label": "Set"},
            {"id": "g2", "label": "set"},
            {"id": "g3", "label": "set group"},
        ],
        "output": "first",
    }
    recipe, warnings = import_recipe(raw)
    assert [group.label for group in recipe.groups] == [
        "Set",
        "set group 2",
        "set group",
    ]
    assert recipe.nodes[0].group_id == "g2"
    assert warnings == [
        {"id": "g2", "old_name": "set", "new_name": "set group 2", "kind": "group"}
    ]


def test_import_suffixes_fit_maximum_display_name_length() -> None:
    label = "x" * 120
    raw: dict[str, Any] = {
        "nodes": [
            point("first", label),
            point("second", label),
            point("later", "x" * 114 + " point"),
        ],
        "output": "second",
    }
    recipe, warnings = import_recipe(raw)
    assert recipe.nodes[1].label == "x" * 112 + " point 2"
    assert len(recipe.nodes[1].label) == 120
    assert len({node.label.casefold() for node in recipe.nodes}) == 3
    assert warnings[0]["new_name"] == recipe.nodes[1].label


@pytest.mark.parametrize("group", [False, True])
def test_duplicate_identity_is_rejected_before_name_repair(group: bool) -> None:
    raw: dict[str, Any] = {"nodes": [point("first", "Point")], "output": "first"}
    if group:
        raw["groups"] = [{"id": "g", "label": "Group"}, {"id": "g", "label": "Group"}]
    else:
        raw["nodes"].append(point("first", "Point"))
    original = deepcopy(raw)
    with pytest.raises(ValueError, match=r"duplicate feature.*ID"):
        _ = import_recipe(raw)
    assert raw == original


def test_import_does_not_repair_invalid_geometry_and_cli_prints_each_warning_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw: dict[str, Any] = {
        "nodes": [point("first", "Point"), point("second", "Point")],
        "output": "first",
    }
    path = tmp_path / "actions.json"
    _ = path.write_text(json.dumps(raw))
    recipe = load_recipe(path)
    assert recipe.nodes[1].label == "Point point"
    stderr = capsys.readouterr().err
    assert stderr.count("renamed duplicate feature name") == 1
    assert "'Point' to 'Point point' (ID second)" in stderr
    raw["nodes"][1]["initial_coordinates"] = [0, 0]
    with pytest.raises(ValidationError):
        _ = import_recipe(raw)
