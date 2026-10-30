"""Project recovery settings are strict, portable numerical intent."""

from typing import Any

import pytest
from pydantic import ValidationError

from experiments.feature_graph import FitRecoverySettings, Recipe
from experiments.selected_direction_fit import SelectedDirectionComponent


def test_legacy_recipe_defaults_and_settings_roundtrip() -> None:
    legacy = Recipe.model_validate({"schema_version": 2, "nodes": []})
    assert legacy.fit_recovery.geometric_equivalence_ppm == 1
    authored = Recipe.model_validate(
        {"nodes": [], "fit_recovery": {"geometric_equivalence_ppm": 0.25}}
    )
    assert Recipe.model_validate_json(authored.model_dump_json()) == authored
    assert authored.model_dump(mode="json")["fit_recovery"] == {
        "geometric_equivalence_ppm": 0.25
    }


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "1", None])
def test_invalid_equivalence_settings_are_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        _ = Recipe.model_validate(
            {"fit_recovery": {"geometric_equivalence_ppm": value}}
        )
    with pytest.raises((ValueError, TypeError)):
        _ = SelectedDirectionComponent((), {}, (), geometric_equivalence_ppm=value)


def test_unknown_settings_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _ = FitRecoverySettings.model_validate(
            {"geometric_equivalence_ppm": 1, "typo": 2}
        )
