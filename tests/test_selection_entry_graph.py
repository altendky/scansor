"""Selection containment selects observations without becoming fit dependencies."""

from copy import deepcopy
from typing import Any

import pytest

from experiments.feature_graph import FeatureGraph, Recipe, StaleGraph
from experiments.scan_workspace import ScanCancelled
from experiments.selection_entries import SourceBinding, source_binding
from experiments.selection_entry_runtime import VolumeCache
from experiments.selection_volume import update_selection_volume_options
from experiments.selection_volume_transactions import author_volume, convert_selection
from experiments.volume_archive import (
    VolumeArchive,
    decode_selection_volume,
    encode_selection_volume,
)
from tests.test_scan_cylinder_fit import imported_cylinder, imported_recipe
from tests.test_selection_volume_transactions import recipe, workspace


def snapshot(graph: FeatureGraph[Any]) -> dict[str, Any]:
    return dict(graph.snapshot())


def test_nested_membership_evaluates_the_existing_top_level_fit() -> None:
    scan = workspace()
    nested = convert_selection(recipe(scan), scan, "patch")
    graph = FeatureGraph(scan, nested)
    before = snapshot(graph)
    selection = before["recipe"]["nodes"][1]
    assert selection["id"] == "patch" and "ids" not in selection
    assert "depth" not in selection and len(selection["entries"]) == 1
    assert selection["entries"][0]["volumes"] == []
    assert before["recipe"]["nodes"][2]["selections"] == ["patch"]
    result: dict[str, Any] = graph.evaluate(before["token"], "plane")
    assert result["states"] == {"scan": "ready", "patch": "ready", "plane": "ready"}
    assert result["memberships"]["patch"] == list(range(len(scan.local)))
    assert result["results"]["plane"]["weighted_rms"] < 1e-8


def test_needs_input_is_settled_and_clears_geometry_without_numerical_failure() -> None:
    scan = workspace()
    nested = convert_selection(recipe(scan), scan, "patch")
    graph = FeatureGraph(scan, nested)
    before = snapshot(graph)
    _ = graph.evaluate(before["token"], "plane")
    saved = snapshot(graph)
    pending = deepcopy(saved["recipe"])
    entry = pending["nodes"][1]["entries"][0]
    entry.update(mode="generated", ids=None, source_volume=None)
    waiting: dict[str, Any] = graph.replace(
        Recipe.model_validate(pending), saved["token"]
    )
    assert waiting["states"]["patch"] == waiting["states"]["plane"] == "needs_input"
    assert waiting["errors"] == {} and not waiting["results"]
    assert waiting["diagnostics"]["patch"]["action"] == "choose_source_volume"
    assert not graph.needs_evaluation(str(waiting["token"]), ["plane"])
    explicit: dict[str, Any] = graph.evaluate(str(waiting["token"]), "plane")
    assert explicit["states"]["plane"] == "needs_input" and explicit["errors"] == {}
    assert not graph.readiness_snapshot(str(waiting["token"]), ["plane"])[
        "required_failures"
    ]


def test_inactive_membership_uses_its_saved_vertex_bound() -> None:
    scan = workspace()
    payload = convert_selection(recipe(scan), scan, "patch").model_dump(mode="json")
    inactive = SourceBinding(
        source_sha256="c" * 64, reference_sha256="d" * 64, vertices=100
    )
    payload["nodes"][1]["entries"].append(
        {
            "id": "inactive",
            "scan": inactive.model_dump(),
            "mode": "painted",
            "ids": [99],
        }
    )
    graph = FeatureGraph(scan, Recipe.model_validate(payload))
    assert snapshot(graph)["memberships"]["patch"] == list(range(len(scan.local)))


def test_new_owned_volume_and_inactive_entry_do_not_invalidate_authoring_fit() -> None:
    scan = workspace()
    graph = FeatureGraph(scan, recipe(scan))
    before = snapshot(graph)
    _ = graph.evaluate(before["token"], "plane")
    ready = snapshot(graph)
    created: dict[str, Any] = author_volume(
        graph,
        ready["token"],
        selection="patch",
        volume="mesh",
        label="Footprint",
        kind="mesh",
    )
    assert created["states"]["plane"] == "ready"
    changed = deepcopy(created["recipe"])
    changed["nodes"][1]["entries"][0]["volumes"][0]["label"] = "Renamed volume"
    changed["nodes"][1]["entries"].append(
        {
            "id": "other",
            "scan": SourceBinding(
                source_sha256="c" * 64, reference_sha256="d" * 64, vertices=50
            ).model_dump(),
            "mode": "generated",
            "source_volume": None,
        }
    )
    renamed: dict[str, Any] = graph.replace(
        Recipe.model_validate(changed), str(created["token"])
    )
    assert renamed["states"]["plane"] == "ready"
    assert renamed["results"]["plane"] == created["results"]["plane"]


def test_generated_entry_reads_a_chosen_owned_volume() -> None:
    scan = workspace()
    graph = FeatureGraph(scan, recipe(scan))
    created: dict[str, Any] = author_volume(
        graph,
        snapshot(graph)["token"],
        selection="patch",
        volume="mesh",
        label="Footprint",
        kind="mesh",
    )
    payload = deepcopy(created["recipe"])
    owner_entry = payload["nodes"][1]["entries"][0]
    payload["nodes"].insert(
        2,
        {
            "id": "generated",
            "label": "Generated",
            "operation": "selection",
            "source": "scan",
            "entries": [
                {
                    "id": "target",
                    "scan": source_binding(scan).model_dump(),
                    "mode": "generated",
                    "source_volume": {
                        "selection": "patch",
                        "entry": owner_entry["id"],
                        "volume": "mesh",
                    },
                }
            ],
        },
    )
    payload["nodes"][-1]["selections"] = ["generated"]
    state: dict[str, Any] = graph.replace(
        Recipe.model_validate(payload), created["token"]
    )
    evaluated: dict[str, Any] = graph.evaluate(str(state["token"]), "plane")
    assert evaluated["states"]["plane"] == "ready"
    assert evaluated["memberships"]["generated"] == list(range(len(scan.local)))
    assert evaluated["derived"]["generated"]["coverage"]["fraction"] == pytest.approx(1)


def test_cache_cannot_accept_different_bytes_with_an_already_seen_claimed_digest() -> (
    None
):
    scan = workspace()
    graph = FeatureGraph(scan, recipe(scan))
    created: dict[str, Any] = author_volume(
        graph,
        snapshot(graph)["token"],
        selection="patch",
        volume="mesh",
        label="Footprint",
        kind="mesh",
    )
    raw = created["recipe"]["nodes"][1]["entries"][0]["volumes"][0]["archive"]
    accepted = VolumeArchive.model_validate(raw)
    cache = VolumeCache()
    _ = cache.get(accepted)
    tampered = VolumeArchive.model_validate({**raw, "data": "AAAA"})
    with pytest.raises(ValueError):
        cache.verify(tampered)


def generated_graph() -> FeatureGraph[Any]:
    scan = workspace()
    graph = FeatureGraph(scan, recipe(scan))
    created: dict[str, Any] = author_volume(
        graph,
        snapshot(graph)["token"],
        selection="patch",
        volume="mesh",
        label="Footprint",
        kind="mesh",
    )
    payload = deepcopy(created["recipe"])
    owner = payload["nodes"][1]["entries"][0]
    payload["nodes"].insert(
        2,
        {
            "id": "generated",
            "label": "Generated",
            "operation": "selection",
            "source": "scan",
            "entries": [
                {
                    "id": "target",
                    "scan": source_binding(scan).model_dump(),
                    "mode": "generated",
                    "source_volume": {
                        "selection": "patch",
                        "entry": owner["id"],
                        "volume": "mesh",
                    },
                }
            ],
        },
    )
    payload["nodes"][-1]["selections"] = ["generated"]
    payload["nodes"].append(
        {
            "id": "unrelated",
            "operation": "point",
            "label": "Unrelated",
            "initial_coordinates": [10, 20, 30],
        }
    )
    state: dict[str, Any] = graph.replace(
        Recipe.model_validate(payload), created["token"]
    )
    _ = graph.evaluate(str(state["token"]), all_actions=True)
    return graph


def edit_authoring_ids(graph: FeatureGraph[Any], ids: list[int]) -> dict[str, Any]:
    before = snapshot(graph)
    payload = deepcopy(before["recipe"])
    payload["nodes"][1]["entries"][0]["ids"] = ids
    return dict(graph.replace(Recipe.model_validate(payload), before["token"]))


def test_automatic_refresh_publishes_recipe_and_results_together() -> None:
    graph = generated_graph()
    before = snapshot(graph)
    ids = [index for index in range(25) if index % 5 < 3]
    changed = edit_authoring_ids(graph, ids)
    assert changed["states"]["plane"] == "needs_input"
    assert graph.needs_evaluation(changed["token"], ["plane"])
    refreshed: dict[str, Any] = dict(graph.ensure_current(changed["token"], ["plane"]))
    assert refreshed["token"] != changed["token"]
    assert refreshed["evaluation_origin_token"] == changed["token"]
    assert refreshed["states"]["plane"] == "ready"
    assert refreshed["memberships"]["generated"] == ids
    assert refreshed["states"]["unrelated"] == "ready"
    assert refreshed["results"]["unrelated"] == before["results"]["unrelated"]
    assert not graph.needs_evaluation(refreshed["token"], ["plane"])
    assert "recipe" in graph.snapshot(if_recipe_token=changed["token"])
    assert "recipe" not in graph.snapshot(if_recipe_token=refreshed["token"])
    compact = graph.snapshot(
        if_revision=refreshed["revision"], if_recipe_token=refreshed["token"]
    )
    assert compact["unchanged"] is True
    assert compact["evaluation_origin_token"] == changed["token"]
    with pytest.raises(StaleGraph):
        _ = graph.replace(Recipe.model_validate(changed["recipe"]), changed["token"])


def test_failed_refresh_waits_until_authoring_changes() -> None:
    graph = generated_graph()
    changed = edit_authoring_ids(graph, [])
    failed: dict[str, Any] = dict(graph.ensure_current(changed["token"], ["plane"]))
    assert failed["token"] == changed["token"]
    assert failed["states"]["plane"] == "needs_input"
    assert failed["errors"] == {}
    assert "could not refresh" in failed["diagnostics"]["generated"]["message"]
    assert not graph.needs_evaluation(failed["token"], ["plane"])
    payload = deepcopy(failed["recipe"])
    payload["nodes"][1]["label"] = "Renamed"
    renamed: dict[str, Any] = graph.replace(
        Recipe.model_validate(payload), failed["token"]
    )
    assert not graph.needs_evaluation(str(renamed["token"]), ["plane"])
    repaired = edit_authoring_ids(graph, [0, 1, 5, 6])
    assert graph.needs_evaluation(repaired["token"], ["plane"])


def test_cancellation_after_private_evaluation_does_not_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = generated_graph()
    changed = edit_authoring_ids(graph, [0, 1, 5, 6])
    cancelled = False
    original = FeatureGraph._evaluate  # pyright: ignore[reportPrivateUsage]

    def evaluate(candidate: FeatureGraph[Any], *args: Any, **kwargs: Any) -> Any:
        nonlocal cancelled
        result = original(candidate, *args, **kwargs)
        if candidate is not graph:
            cancelled = True
        return result

    monkeypatch.setattr(FeatureGraph, "_evaluate", evaluate)
    with pytest.raises(ScanCancelled):
        _ = graph.evaluate(changed["token"], "plane", cancelled=lambda: cancelled)
    assert snapshot(graph) == changed


def test_user_edit_during_private_evaluation_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = generated_graph()
    changed = edit_authoring_ids(graph, [0, 1, 5, 6])
    original = FeatureGraph._evaluate  # pyright: ignore[reportPrivateUsage]
    edited = False

    def evaluate(candidate: FeatureGraph[Any], *args: Any, **kwargs: Any) -> Any:
        nonlocal edited
        result = original(candidate, *args, **kwargs)
        if candidate is not graph and not edited:
            edited = True
            payload = deepcopy(changed["recipe"])
            payload["nodes"][0]["label"] = "User edit"
            _ = graph.replace(Recipe.model_validate(payload), changed["token"])
        return result

    monkeypatch.setattr(FeatureGraph, "_evaluate", evaluate)
    with pytest.raises(StaleGraph):
        _ = graph.evaluate(changed["token"], "plane")
    final = snapshot(graph)
    assert final["recipe"]["nodes"][0]["label"] == "User edit"
    assert final["states"]["plane"] == "needs_input"
    assert "evaluation_origin_token" not in final


def test_unloaded_authoring_scan_cannot_be_refreshed_from_the_target() -> None:
    original = generated_graph()
    saved = snapshot(original)
    payload = deepcopy(saved["recipe"])
    target = workspace(6)
    source = recipe(target).model_dump()["nodes"][0]
    payload["nodes"][0] = source
    for index in (1, 2):
        owner = payload["nodes"][index]
        reference = payload["nodes"][2]["entries"][0]["source_volume"]
        owner["entries"].append(
            {
                "id": "rescan",
                "scan": source_binding(target).model_dump(),
                "mode": "generated",
                "source_volume": reference,
            }
        )
    graph = FeatureGraph(target, Recipe.model_validate(payload))
    accepted: dict[str, Any] = dict(graph.evaluate(snapshot(graph)["token"], "plane"))
    assert accepted["states"]["plane"] == "ready"
    archive = accepted["recipe"]["nodes"][1]["entries"][0]["volumes"][0]["archive"]
    changed = edit_authoring_ids(graph, [0, 1, 5, 6])
    assert changed["states"]["plane"] == "needs_input"
    assert changed["diagnostics"]["generated"]["source_loaded"] is False
    assert not graph.needs_evaluation(changed["token"], ["plane"])
    evaluated: dict[str, Any] = graph.evaluate(changed["token"], "plane")
    assert evaluated["states"]["plane"] == "needs_input"
    assert (
        evaluated["recipe"]["nodes"][1]["entries"][0]["volumes"][0]["archive"]
        == archive
    )


def test_valid_archive_refresh_can_publish_an_ordinary_fit_failure() -> None:
    graph = generated_graph()
    changed = edit_authoring_ids(graph, [0, 1])
    with pytest.raises(ValueError):
        _ = graph.evaluate(changed["token"], "plane")
    failed = snapshot(graph)
    assert failed["token"] != changed["token"]
    assert failed["evaluation_origin_token"] == changed["token"]
    assert failed["states"]["generated"] == "ready"
    assert failed["states"]["plane"] == "failed"
    assert failed["memberships"]["generated"] == [0, 1]
    assert failed["errors"]["plane"]


def test_recovery_preserves_setup_and_refresh_failure_evidence() -> None:
    graph = generated_graph()
    changed = edit_authoring_ids(graph, [])
    _ = graph.ensure_current(changed["token"], ["plane"])
    checkpoint = graph.failure_checkpoint()
    recovered = FeatureGraph(graph.workspace, checkpoint.recipe)
    recovered.restore_failures(checkpoint)
    state = snapshot(recovered)
    assert state["states"]["plane"] == "needs_input"
    assert (
        state["diagnostics"]["generated"] == snapshot(graph)["diagnostics"]["generated"]
    )
    assert state["results"] == {} and state["errors"] == {}
    settled = recovered.ensure_current(state["token"], ["plane"])
    assert settled["token"] == state["token"]
    assert not recovered.needs_evaluation(state["token"], ["plane"])


@pytest.mark.parametrize("convert_active", [False, True])
def test_unrelated_unavailable_selection_does_not_block_cylinder(
    convert_active: bool,
) -> None:
    scan = imported_cylinder()
    initial = imported_recipe(scan)
    if convert_active:
        initial = convert_selection(initial, scan, "patch")
    payload = initial.model_dump()
    payload["nodes"].append(
        {
            "id": "unavailable",
            "label": "Archived patch",
            "operation": "selection",
            "source": "scan",
            "entries": [
                {
                    "id": "foreign",
                    "scan": source_binding(workspace()).model_dump(),
                    "mode": "painted",
                    "ids": [0, 1, 2],
                }
            ],
        }
    )
    graph = FeatureGraph(scan, Recipe.model_validate(payload))
    evaluated: dict[str, Any] = graph.evaluate(snapshot(graph)["token"], "cylinder")
    assert evaluated["states"]["cylinder"] == "ready"
    assert evaluated["states"]["unavailable"] == "needs_input"
    assert evaluated["errors"] == {}


@pytest.mark.parametrize("contradiction", ["options", "ownership", "surface"])
def test_fresh_archive_cannot_contradict_declared_intent_or_provenance(
    contradiction: str,
) -> None:
    graph = generated_graph()
    before = snapshot(graph)
    payload = deepcopy(before["recipe"])
    volume = payload["nodes"][1]["entries"][0]["volumes"][0]
    geometry = decode_selection_volume(VolumeArchive.model_validate(volume["archive"]))
    if contradiction == "options":
        geometry = update_selection_volume_options(geometry, {"edge_padding": 100})
    elif contradiction == "ownership":
        geometry["provenance"]["volume"] = "different"
    else:
        geometry["provenance"]["surface"] = {"feature": "plane", "surface": None}
    volume["archive"] = encode_selection_volume(geometry).model_dump(mode="json")
    with pytest.raises(ValueError, match=r"contradicts|ownership provenance"):
        _ = graph.replace(Recipe.model_validate(payload), before["token"])
    assert snapshot(graph) == before


def test_pending_options_change_retains_geometry_until_atomic_refresh() -> None:
    graph = generated_graph()
    before = snapshot(graph)
    payload = deepcopy(before["recipe"])
    volume = payload["nodes"][1]["entries"][0]["volumes"][0]
    archive = deepcopy(volume["archive"])
    volume["options"]["edge_padding"] = 0.25
    waiting: dict[str, Any] = graph.replace(
        Recipe.model_validate(payload), before["token"]
    )
    assert waiting["states"]["plane"] == "needs_input"
    assert (
        waiting["recipe"]["nodes"][1]["entries"][0]["volumes"][0]["archive"] == archive
    )
    refreshed: dict[str, Any] = graph.ensure_current(waiting["token"], ["plane"])
    assert refreshed["states"]["plane"] == "ready"
    assert (
        refreshed["recipe"]["nodes"][1]["entries"][0]["volumes"][0]["archive"]
        != archive
    )
