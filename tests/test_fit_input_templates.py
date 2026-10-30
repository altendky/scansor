"""Uncreated fits use the same structural properties and role matching as saved fits."""

from typing import get_args

from experiments.feature_graph import Recipe, SurfaceFit
from experiments.feature_inputs import (
    REQUIREMENTS,
    compatibility_reason,
    fit_input_templates,
    output_catalogue,
)


def test_fit_templates_are_declared_by_the_model_and_use_real_catalogue_contracts() -> (
    None
):
    frame = "template-test-frame"
    templates = fit_input_templates(frame=frame)
    assert set(templates) == set(get_args(SurfaceFit.model_fields["kind"].annotation))
    for kind, template in templates.items():
        marker = template["feature"]
        node = SurfaceFit.model_construct(
            id=marker, label="New fit", operation="fit", kind=kind, selections=[]
        )
        recipe = Recipe.model_construct(nodes=[node], output=marker)
        assert template["input_catalogue"] == output_catalogue(recipe, frame=frame)
        for descriptor in template["input_catalogue"]:
            assert descriptor["frame"] == frame
            assert descriptor["dependencies"] == [marker]
            for property_spec in descriptor["properties"].values():
                assert property_spec["frame"] == frame
            for requirement in REQUIREMENTS:
                offered = template["input_requirements"][requirement]
                records = [*offered["choices"], *offered["unavailable"]]
                match = next(
                    (
                        record
                        for record in records
                        if record["reference"] == descriptor["reference"]
                    ),
                    None,
                )
                if match is not None:
                    assert match["compatible"] == (
                        compatibility_reason(descriptor, requirement) is None
                    )
    assert (
        templates["sphere"]["input_requirements"]["shared_center_members"]["choices"][
            0
        ]["reference"]["output"]
        == "center"
    )
    assert (
        templates["cylinder"]["input_requirements"]["relationship_direction_targets"][
            "choices"
        ][0]["reference"]["output"]
        == "axis"
    )


def test_fit_templates_do_not_alias_snapshots_or_collide_with_saved_feature_ids() -> (
    None
):
    first = fit_input_templates(frame="example")
    marker = first["sphere"]["feature"]
    changed = first["sphere"]["input_catalogue"][0]
    changed["properties"]["point"]["parameter_owner"]["quantities"]["position"] = 99
    second = fit_input_templates(frame="example", reserved_ids={marker})
    assert second["sphere"]["feature"] != marker
    assert (
        second["sphere"]["input_catalogue"][0]["properties"]["point"][
            "parameter_owner"
        ]["quantities"]["position"]
        == 3
    )
    assert (
        fit_input_templates(frame="example")["sphere"]["input_catalogue"][0][
            "properties"
        ]["point"]["parameter_owner"]["quantities"]["position"]
        == 3
    )
