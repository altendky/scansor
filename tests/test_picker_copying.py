"""Picker construction copies returned data without traversing rejected data."""

from copy import deepcopy
from typing import Any

import pytest

import experiments.feature_inputs as feature_inputs
from tests.test_feature_inputs import ready_catalogue
from tests.test_feature_inputs import recipe as small_recipe


class CopyProbe:
    def __init__(self, visits: list[int]) -> None:
        self.visits: list[int] = visits

    def __deepcopy__(self, _memo: dict[int, Any]) -> list[int]:
        self.visits.append(1)
        return [1, 2, 3]


def test_picker_copies_only_descriptors_returned_for_each_requirement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recipe = small_recipe()
    catalogue = ready_catalogue()
    originals = {id(item) for item in catalogue}
    copied: list[int] = []
    ordinary_copy = deepcopy

    def observed(value: Any) -> Any:
        if id(value) in originals:
            copied.append(id(value))
        return ordinary_copy(value)

    monkeypatch.setattr(feature_inputs, "deepcopy", observed)
    total_returned = 0
    for requirement in feature_inputs.REQUIREMENTS:
        copied.clear()
        result = feature_inputs.input_choices(
            recipe, requirement=requirement, catalogue=catalogue
        )
        returned = len(result["choices"]) + len(result["unavailable"])
        assert len(copied) == returned
        assert len(set(copied)) == returned
        total_returned += returned
    assert 0 < total_returned < len(catalogue) * len(feature_inputs.REQUIREMENTS)


@pytest.mark.parametrize("requirement", ["point", "direction"])
@pytest.mark.parametrize("retain", [False, True])
def test_irrelevant_capabilities_and_roles_are_skipped_unless_retained(
    requirement: str, retain: bool
) -> None:
    descriptor = next(
        item
        for item in ready_catalogue()
        if item["reference"] == {"feature": "axis", "output": "axis", "context": "axis"}
    )
    visits: list[int] = []
    descriptor["preview"] = {"probe": CopyProbe(visits)}
    descriptor["uses"] = ["binding"]
    result = feature_inputs.input_choices(
        small_recipe(),
        requirement=requirement,
        retained=[descriptor["reference"]] if retain else [],
        catalogue=[descriptor],
    )
    assert result["choices"] == []
    assert len(result["unavailable"]) == int(retain)
    assert len(visits) == int(retain)
    if retain:
        selected = result["unavailable"][0]
        assert selected["reference"] == descriptor["reference"]
        assert not selected["compatible"]
        assert (
            "capability" in selected["reason"]
            if requirement == "point"
            else "role" in selected["reason"]
        )


def test_same_capability_ownership_and_context_failures_remain_explained() -> None:
    catalogue = ready_catalogue()
    constrained = feature_inputs.input_choices(
        small_recipe(), requirement="constrainable_plane", catalogue=catalogue
    )
    datum = next(
        item
        for item in constrained["unavailable"]
        if item["reference"]["feature"] == "datum"
    )
    assert "parameter owner" in datum["reason"]
    axis = next(item for item in catalogue if item["reference"]["output"] == "axis")
    blocked = feature_inputs.input_choices(
        small_recipe(), requirement="direction", consumer_id="axis", catalogue=[axis]
    )
    assert blocked["unavailable"][0]["reference"] == axis["reference"]
    assert "dependency cycle" in blocked["unavailable"][0]["reason"]


@pytest.mark.parametrize("state", ["unevaluated", "stale", "failed"])
def test_corrective_bindings_and_fixed_reads_keep_detached_metadata(state: str) -> None:
    catalogue = ready_catalogue()
    axis = next(item for item in catalogue if item["reference"]["output"] == "axis")
    axis.update(availability=state, reason="Provider is not current")
    before = deepcopy(catalogue)
    fixed = feature_inputs.input_choices(
        small_recipe(),
        requirement="relationship_direction_reference",
        use="geometry",
        catalogue=catalogue,
    )
    binding = feature_inputs.input_choices(
        small_recipe(),
        requirement="relationship_direction_reference",
        use="binding",
        catalogue=catalogue,
    )
    fixed_axis = next(
        item for item in fixed["unavailable"] if item["reference"] == axis["reference"]
    )
    binding_axis = next(
        item for item in binding["choices"] if item["reference"] == axis["reference"]
    )
    assert fixed_axis["availability"] == state
    assert binding_axis["availability"] == "ready"
    assert binding_axis["reason"] is None
    binding_axis["reference"]["context"] = "changed"
    binding_axis["preview"]["axis_display"][0] = 99
    binding_axis["properties"]["direction"]["uses"].append("changed")
    assert catalogue == before
    assert fixed_axis["preview"]["axis_display"][0] != 99
    assert "changed" not in fixed_axis["properties"]["direction"]["uses"]
    fresh = feature_inputs.input_choices(
        small_recipe(),
        requirement="relationship_direction_reference",
        use="binding",
        catalogue=catalogue,
    )
    assert (
        next(
            item for item in fresh["choices"] if item["reference"] == axis["reference"]
        )["preview"]["axis_display"][0]
        != 99
    )


def test_explicit_property_use_failure_is_not_filtered_as_capability_noise() -> None:
    axis = next(
        item for item in ready_catalogue() if item["reference"]["output"] == "axis"
    )
    axis["properties"]["direction"]["uses"] = ["geometry"]
    before = deepcopy(axis)
    result = feature_inputs.input_choices(
        small_recipe(),
        requirement="relationship_direction_reference",
        use="binding",
        catalogue=[axis],
    )
    assert result["choices"] == []
    assert result["unavailable"][0]["reference"] == axis["reference"]
    assert (
        result["unavailable"][0]["reason"]
        == "This output does not provide the requested input use"
    )
    assert not result["unavailable"][0]["compatible"]
    assert axis == before


def test_extra_outputs_do_not_copy_discarded_preview_or_property_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_visits: list[int] = []
    property_visits: list[int] = []
    ordinary_resolve = feature_inputs.resolve_output
    ordinary_properties = feature_inputs._property_descriptors  # pyright: ignore[reportPrivateUsage]

    def resolve(
        reference: feature_inputs.OutputReference, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        value = ordinary_resolve(reference, *args, **kwargs)
        if reference.feature == "sphere" and reference.output == "center":
            value["probe"] = CopyProbe(preview_visits)
        return value

    def properties(
        spec: dict[str, Any], reference: feature_inputs.OutputReference, frame: str
    ) -> dict[str, Any]:
        value = ordinary_properties(spec, reference, frame)
        if reference.feature == "sphere" and reference.output == "center":
            value["probe"] = {"probe": CopyProbe(property_visits)}
        return value

    monkeypatch.setattr(feature_inputs, "resolve_output", resolve)
    monkeypatch.setattr(feature_inputs, "_property_descriptors", properties)
    recipe = small_recipe()
    values = {"sphere": {"kind": "sphere", "parameters": [4, 5, 6, 2], "ids": [0, 1]}}
    before = deepcopy(values)
    catalogue = feature_inputs.output_catalogue(
        recipe, dict.fromkeys((node.id for node in recipe.nodes), "ready"), values
    )
    sphere = [item for item in catalogue if item["reference"]["feature"] == "sphere"]
    assert {item["reference"]["output"] for item in sphere} == {
        "center",
        "surface",
        "radius",
        "observations",
    }
    assert preview_visits == property_visits == []
    assert all(
        "probe" not in (item["preview"] or {}) and "probe" not in item["properties"]
        for item in sphere
        if item["reference"]["output"] != "center"
    )
    assert values == before
