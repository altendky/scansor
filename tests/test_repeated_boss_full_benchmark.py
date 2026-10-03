"""Benchmark preparation preserves the full demo and painted footprints."""

from pathlib import Path
from typing import cast

import numpy as np
import pytest

from experiments.feature_graph import FeatureGraph, Recipe, Selection, Source
from experiments.nozzle_session import NozzleWorkspace
from experiments.repeated_boss_benchmark import DEFINITION
from experiments.repeated_boss_fixture import publish_fixture
from experiments.repeated_boss_full_benchmark import RECIPE, transfer_recipe


@pytest.fixture(scope="module")
def examples(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("full-boss-benchmark")
    for name, scale in (("baseline", 1), ("dense", 2)):
        _ = publish_fixture(
            root / name,
            DEFINITION,
            realization_ids=("scan-coarse",),
            tessellation_scale=scale,
        )
    return root / "baseline/scan-coarse", root / "dense/scan-coarse"


def test_baseline_recipe_is_exactly_unchanged(examples: tuple[Path, Path]) -> None:
    baseline, _ = examples
    original = Recipe.model_validate_json(RECIPE.read_bytes())
    workspace = NozzleWorkspace(baseline)

    assert transfer_recipe(original, baseline, baseline, workspace) == original


def test_transfer_preserves_graph_and_surface_membership(
    examples: tuple[Path, Path],
) -> None:
    baseline, dense = examples
    original = Recipe.model_validate_json(RECIPE.read_bytes())
    workspace = NozzleWorkspace(dense)
    transferred = transfer_recipe(original, baseline, dense, workspace)

    assert transferred == transfer_recipe(original, baseline, dense, workspace)
    _ = FeatureGraph(workspace, transferred)
    assert [node.id for node in transferred.nodes] == [
        node.id for node in original.nodes
    ]
    assert transferred.output == original.output
    base_occ = np.load(baseline / "truth/occurrence-code.npy", allow_pickle=False)
    base_role = np.load(baseline / "truth/role-code.npy", allow_pickle=False)
    dense_occ = np.load(dense / "truth/occurrence-code.npy", allow_pickle=False)
    dense_role = np.load(dense / "truth/role-code.npy", allow_pickle=False)
    for before, after in zip(original.nodes, transferred.nodes, strict=True):
        if isinstance(before, Source):
            assert isinstance(after, Source)
            assert after.source_sha256 == workspace.default.source_sha256
        elif isinstance(before, Selection):
            assert isinstance(after, Selection)
            assert after.model_copy(update={"ids": before.ids}) == before
            assert len(after.ids) > len(before.ids)
            assert after.ids == sorted(set(after.ids))
            assert np.all(workspace.data.weights[after.ids] > 0)
            expected = set(
                zip(base_occ[before.ids], base_role[before.ids], strict=True)
            )
            actual = set(zip(dense_occ[after.ids], dense_role[after.ids], strict=True))
            assert actual == expected
        else:
            assert after == before
    plate = next(node for node in transferred.nodes if node.label == "plate top")
    assert isinstance(plate, Selection)
    normals = np.load(dense / "truth/normal-part.npy", allow_pickle=False)
    np.testing.assert_array_equal(
        normals[plate.ids], np.tile([0, 0, 1], (len(plate.ids), 1))
    )


def test_transfer_rejects_an_unbound_baseline(examples: tuple[Path, Path]) -> None:
    baseline, dense = examples
    original = Recipe.model_validate_json(RECIPE.read_bytes())
    with pytest.raises(ValueError, match="baseline does not match"):
        _ = transfer_recipe(original, dense, baseline, NozzleWorkspace(baseline))


@pytest.mark.parametrize("scale", [1, 2, 4, 5, 7, 8])
def test_full_boss_workflow_completes(
    examples: tuple[Path, Path], tmp_path: Path, scale: int
) -> None:
    baseline, twofold = examples
    if scale == 1:
        dense = baseline
    elif scale == 2:
        dense = twofold
    else:
        _ = publish_fixture(
            tmp_path / "dense",
            DEFINITION,
            realization_ids=("scan-coarse",),
            tessellation_scale=scale,
        )
        dense = tmp_path / "dense/scan-coarse"
    workspace = NozzleWorkspace(dense)
    original = Recipe.model_validate_json(RECIPE.read_bytes())
    recipe = transfer_recipe(original, baseline, dense, workspace)
    graph = FeatureGraph(workspace, recipe)

    state = graph.evaluate(str(graph.snapshot()["token"]), all_actions=True)

    assert state["errors"] == {}
    assert set(cast(dict[str, str], state["states"]).values()) == {"ready"}
    assert set(cast(dict[str, str], state["states"])) == {
        node.id for node in recipe.nodes
    }
