#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.12.12"
# dependencies = []
# ///
"""Focused tests for generated-to-Track-5 CAD reconciliation."""

from __future__ import annotations

import copy
import importlib
import os
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, ClassVar, cast, final, override
from unittest import mock

if TYPE_CHECKING:
    from experiments import generated_track5_cad_reconciliation_v1 as reconciliation
else:
    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    reconciliation = importlib.import_module(
        "experiments.generated_track5_cad_reconciliation_v1"
    )


@final
class ReconciliationTest(unittest.TestCase):
    root: ClassVar[Path]
    track5: ClassVar[ModuleType]
    estimate: ClassVar[dict[str, Any]]
    prediction_bytes: ClassVar[bytes]
    run_01: ClassVar[Any]
    run_02: ClassVar[Any]

    @classmethod
    @override
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parent.parent
        cls.track5 = reconciliation.load_track5(
            (cls.root / "experiments/track5_onshape_cad_repro.py").read_bytes(),
            cls.root / "experiments/track5_onshape_cad_repro.py",
        )
        cls.estimate = dict(reconciliation.GENERATED_TRUTH)
        cls.prediction_bytes = reconciliation.frozen_prediction_bytes(cls.estimate)
        cls.run_01 = reconciliation.parse_json(
            (
                cls.root
                / "experiments/track5-cad-evidence-run-01-backfill/normalized/normalized.json"
            ).read_bytes(),
            "run 01 normalized",
        )
        cls.run_02 = reconciliation.parse_json(
            (
                cls.root
                / "experiments/track5-cad-evidence-run-02/normalized/normalized.json"
            ).read_bytes(),
            "run 02 normalized",
        )

    def test_fitted_geometry_is_derived_from_seven_estimates(self) -> None:
        prediction = reconciliation.disposable_prediction(self.prediction_bytes)
        variants = {item["variant"]: item for item in prediction["variants"]}
        axisymmetric = variants["axisymmetric"]
        asymmetric = variants["asymmetric_datum_flat"]
        self.assertEqual(axisymmetric["face_count"], 7)
        self.assertEqual(asymmetric["face_count"], 8)
        self.assertEqual(
            axisymmetric["frame"],
            {"axis": "+Z", "handedness": "right", "length_unit": "m"},
        )
        self.assertEqual(
            [item["z_bounds_m"] for item in asymmetric["cylinders"]],
            [[0.0, 0.02], [0.02, 0.05], [0.05, 0.08]],
        )
        self.assertEqual(asymmetric["cylinders"][1]["trim"]["x_max_m"], 0.016)
        datum = asymmetric["planes"][-1]
        self.assertEqual(datum["source_normal"], [-1.0, 0.0, 0.0])
        self.assertFalse(datum["orientation"])
        self.assertEqual(datum["outward_normal"], [1.0, 0.0, 0.0])
        self.assertAlmostEqual(datum["bounds_m"]["y"][1], 0.008246211251235319)

    def test_cad_value_mutation_cannot_change_fitted_prediction_bytes(self) -> None:
        before = self.prediction_bytes
        mutated = copy.deepcopy(self.run_01)
        mutated["variants"][0]["cylinders"][0]["radius_m"] += 1e-10
        summary, _ = reconciliation.compare_prediction(
            self.prediction_bytes, mutated, self.track5, "mutated-cad"
        )
        self.assertEqual(summary["outcome"], "tolerated numerical")
        self.assertEqual(self.prediction_bytes, before)

    def test_mutate_and_restore_disposable_prediction_cannot_cross_comparisons(
        self,
    ) -> None:
        first = reconciliation.disposable_prediction(self.prediction_bytes)
        original_count = first["variants"][0]["face_count"]
        first["variants"][0]["face_count"] = 99
        second = reconciliation.disposable_prediction(self.prediction_bytes)
        self.assertIsNot(first, second)
        self.assertEqual(second["variants"][0]["face_count"], original_count)
        first["variants"][0]["face_count"] = original_count
        third = reconciliation.disposable_prediction(self.prediction_bytes)
        self.assertIsNot(first, third)
        self.assertIsNot(second, third)
        self.assertEqual(reconciliation.compact_json(second), self.prediction_bytes)
        self.assertEqual(reconciliation.compact_json(third), self.prediction_bytes)
        run_01, _ = reconciliation.compare_prediction(
            self.prediction_bytes, self.run_01, self.track5, "after-restored-copy"
        )
        run_02, _ = reconciliation.compare_prediction(
            self.prediction_bytes, self.run_02, self.track5, "independent-second-copy"
        )
        self.assertLessEqual(
            run_01["maximum_linear_error_m"], reconciliation.LINEAR_TOLERANCE_M
        )
        self.assertLessEqual(
            run_02["maximum_linear_error_m"], reconciliation.LINEAR_TOLERANCE_M
        )

    def test_both_runs_are_required_and_never_averaged(self) -> None:
        for normalized, delta in ((self.run_01, 2e-9), (self.run_02, -2e-9)):
            mutated = copy.deepcopy(normalized)
            mutated["variants"][0]["cylinders"][0]["radius_m"] += delta
            with self.assertRaisesRegex(
                reconciliation.ReconciliationError, "failed geometry"
            ):
                _ = reconciliation.compare_prediction(
                    self.prediction_bytes, mutated, self.track5, "independent-run"
                )

    def test_linear_tolerance_boundary(self) -> None:
        comparison_summary = cast(
            Callable[[list[dict[str, Any]], str], dict[str, Any]],
            vars(reconciliation)["_comparison_summary"],
        )
        finding = [{"classification": "tolerated numerical", "difference_m": 1e-9}]
        summary = comparison_summary(finding, "boundary")
        self.assertEqual(summary["maximum_linear_error_m"], 1e-9)
        with self.assertRaises(reconciliation.ReconciliationError):
            _ = comparison_summary(
                [{"classification": "failure", "difference_m": 1.0000001e-9}],
                "outside",
            )

    def test_unoriented_cylinder_axis_accepts_source_negative_z(self) -> None:
        summary, semantics = reconciliation.compare_prediction(
            self.prediction_bytes, self.run_01, self.track5, "axis-semantics"
        )
        self.assertIn(summary["outcome"], {"exact", "tolerated numerical"})
        self.assertTrue(any("unoriented" in item for item in semantics))

    def test_semantic_frame_and_oriented_normal_changes_fail(self) -> None:
        wrong_frame = copy.deepcopy(self.run_01)
        wrong_frame["variants"][0]["frame"]["handedness"] = "left"
        with self.assertRaisesRegex(
            reconciliation.ReconciliationError, "frame mismatch"
        ):
            _ = reconciliation.compare_prediction(
                self.prediction_bytes, wrong_frame, self.track5, "wrong-frame"
            )
        wrong_orientation = copy.deepcopy(self.run_01)
        wrong_orientation["variants"][0]["planes"][0]["orientation"] = False
        with self.assertRaisesRegex(
            reconciliation.ReconciliationError, "normal or orientation mismatch"
        ):
            _ = reconciliation.compare_prediction(
                self.prediction_bytes,
                wrong_orientation,
                self.track5,
                "wrong-orientation",
            )

    def test_source_normal_orientation_and_outward_normal_are_distinct(self) -> None:
        mutated = copy.deepcopy(self.run_01)
        datum = next(
            item for item in mutated["variants"][0]["planes"] if item["kind"] == "datum"
        )
        datum["source_normal"] = [1.0, 0.0, 0.0]
        with self.assertRaisesRegex(
            reconciliation.ReconciliationError, "normal or orientation mismatch"
        ):
            _ = reconciliation.compare_prediction(
                self.prediction_bytes, mutated, self.track5, "datum-distinction"
            )

    def test_paired_axial_source_normal_and_orientation_inversion_fails(self) -> None:
        mutated = copy.deepcopy(self.run_01)
        plane = mutated["variants"][0]["planes"][0]
        plane["source_normal"] = [-value for value in plane["source_normal"]]
        plane["orientation"] = not plane["orientation"]
        with self.assertRaisesRegex(
            reconciliation.ReconciliationError, "source normal or orientation"
        ):
            _ = reconciliation.compare_prediction(
                self.prediction_bytes, mutated, self.track5, "paired-inversion"
            )

    def test_source_and_sidecar_corruption_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.py"
            _ = path.write_bytes(b"pass\n")
            checksum = reconciliation.sha256(path.read_bytes())
            _ = path.with_suffix(".sha256").write_text(
                f"{checksum}  {path.name}\n", encoding="ascii"
            )
            _ = reconciliation.verify_sidecar(
                path, checksum, "source", replace_suffix=True
            )
            _ = path.write_bytes(b"pass # corrupt\n")
            with self.assertRaisesRegex(reconciliation.ReconciliationError, "mismatch"):
                _ = reconciliation.verify_sidecar(
                    path, checksum, "source", replace_suffix=True
                )

    def test_manifest_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ = (root / "manifest.json").write_bytes(b"{}\n")
            _ = (root / "manifest.sha256").write_text(
                f"{'0' * 64}  manifest.json\n", encoding="ascii"
            )
            with self.assertRaisesRegex(reconciliation.ReconciliationError, "mismatch"):
                reconciliation.verify_manifest_hash(
                    root, "manifest.json", "0" * 64, "manifest"
                )

    def test_source_contains_no_optimizer_or_solver_import(self) -> None:
        source = Path(reconciliation.__file__).read_bytes()
        result = reconciliation.source_has_no_optimizer_or_solver_import(source)
        self.assertEqual(result["optimizer_calls"], [])
        self.assertEqual(result["prohibited_imports"], [])
        for prohibited in (
            b"import scipy",
            b"from scipy",
            b"import generated_solver_evaluator_v1",
            b"from generated_solver_evaluator_v1",
        ):
            self.assertNotIn(prohibited, source)

    def test_static_optimizer_and_solver_import_detection(self) -> None:
        for source in (
            b"from scipy.optimize import least_squares\nleast_squares()\n",
            b"import generated_solver_evaluator_v1\n",
        ):
            with self.assertRaisesRegex(
                reconciliation.ReconciliationError, "optimizer"
            ):
                _ = reconciliation.source_has_no_optimizer_or_solver_import(source)

    def test_track5_executes_verified_bytes_not_path_contents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dependency.py"
            _ = path.write_text("marker = 'path'\n", encoding="ascii")
            module = reconciliation.load_track5(b"marker = 'verified'\n", path)
            self.assertEqual(module.marker, "verified")

    def test_retained_call_order_precedes_actual_cad_open(self) -> None:
        evidence = reconciliation.parse_json(
            reconciliation.read_regular(
                self.root
                / "experiments/generated-track5-cad-reconciliation-v1-evidence.json",
                "retained reconciliation evidence",
            ),
            "retained reconciliation evidence",
        )
        order = evidence["integrity_order"]
        events = order["events"]
        self.assertLess(
            events.index("solver-semantic-recomputation-completed"),
            events.index("cad-evidence-paths-first-opened"),
        )
        self.assertLess(
            events.index("fitted-prediction-frozen-and-hashed"),
            events.index("cad-evidence-paths-first-opened"),
        )
        self.assertTrue(order["solver_verified_before_any_cad_path_opened"])

    def test_generation_api_returns_bytes_without_filesystem_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            before = list(temporary.iterdir())
            with mock.patch.object(
                reconciliation, "build_report", return_value={"result": "pass"}
            ):
                generated = reconciliation.generate_evidence_bytes(self.root)
            self.assertEqual(generated, b'{\n  "result": "pass"\n}\n')
            self.assertEqual(list(temporary.iterdir()), before)

    def test_generate_cli_has_no_output_path_option(self) -> None:
        with (
            mock.patch.object(
                sys,
                "argv",
                [
                    "reconciliation",
                    "generate",
                    "--root",
                    str(self.root),
                    "--output",
                    str(self.root / "forbidden-evidence.json"),
                ],
            ),
            mock.patch.object(reconciliation, "build_report") as build,
            self.assertRaises(SystemExit),
        ):
            _ = reconciliation.main()
        build.assert_not_called()

    def test_repository_local_temporary_workspace_is_rejected(self) -> None:
        with (
            mock.patch.dict(os.environ, {"TMPDIR": str(self.root)}),
            self.assertRaisesRegex(
                reconciliation.ReconciliationError, "temporary workspace"
            ),
        ):
            reconciliation.run_solver_verifier(
                self.root / "experiments/generated_solver_evaluator_v1.py",
                self.root / "experiments/generated-solver-evaluator-v1-evidence.json",
            )

    def test_read_regular_is_anchored_across_parent_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            parent = temporary / "parent"
            parent.mkdir()
            _ = (parent / "value.txt").write_bytes(b"anchored")
            substitute = temporary / "substitute"
            substitute.mkdir()
            _ = (substitute / "value.txt").write_bytes(b"redirected")
            moved = temporary / "moved"
            original_open = os.open
            replaced = False

            def racing_open(
                path: str | Path,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal replaced
                if path == "value.txt" and dir_fd is not None and not replaced:
                    _ = parent.rename(moved)
                    parent.symlink_to(substitute, target_is_directory=True)
                    replaced = True
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch.object(os, "open", side_effect=racing_open):
                self.assertEqual(
                    reconciliation.read_regular(parent / "value.txt", "raced read"),
                    b"anchored",
                )

    def test_tree_fingerprint_rejects_root_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = temporary / "tree"
            root.mkdir()
            _ = (root / "value.txt").write_bytes(b"anchored")
            substitute = temporary / "substitute"
            substitute.mkdir()
            _ = (substitute / "value.txt").write_bytes(b"redirected")
            moved = temporary / "moved"
            original_scandir = os.scandir
            replaced = False

            def racing_scandir(path: int | str | Path) -> object:
                nonlocal replaced
                if isinstance(path, int) and not replaced:
                    _ = root.rename(moved)
                    root.symlink_to(substitute, target_is_directory=True)
                    replaced = True
                return original_scandir(path)

            with (
                mock.patch.object(os, "scandir", side_effect=racing_scandir),
                self.assertRaisesRegex(
                    reconciliation.ReconciliationError, "root was replaced"
                ),
            ):
                _ = reconciliation.tree_fingerprint(root)

    def test_tree_fingerprint_rejects_nested_directory_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            root = temporary / "tree"
            nested = root / "nested"
            nested.mkdir(parents=True)
            _ = (nested / "value.txt").write_bytes(b"anchored")
            moved = root / "opened-nested"

            def replace_nested():
                _ = nested.rename(moved)
                nested.mkdir()
                _ = (nested / "value.txt").write_bytes(b"redirected")

            with self.assertRaisesRegex(
                reconciliation.ReconciliationError, "component was replaced"
            ):
                _ = reconciliation.tree_fingerprint(root, replace_nested)

    def test_tree_fingerprint_rejects_leaf_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tree"
            root.mkdir()
            leaf = root / "value.txt"
            _ = leaf.write_bytes(b"anchored")

            def replace_leaf():
                _ = leaf.rename(root / "opened-value.txt")
                _ = leaf.write_bytes(b"redirected")

            with self.assertRaisesRegex(
                reconciliation.ReconciliationError, "component was replaced"
            ):
                _ = reconciliation.tree_fingerprint(root, replace_leaf)

    def test_track5_verification_requires_both_runs_and_writes_nothing(self) -> None:
        roots = {
            "run-01": self.root / "experiments/track5-cad-evidence-run-01-backfill",
            "run-02": self.root / "experiments/track5-cad-evidence-run-02",
            "suite": self.root / "experiments/track5-cad-evidence-suite",
        }
        before = {
            name: reconciliation.tree_fingerprint(path) for name, path in roots.items()
        }
        events: list[str] = []
        result = reconciliation.verify_track5(self.root, self.prediction_bytes, events)
        after = {
            name: reconciliation.tree_fingerprint(path) for name, path in roots.items()
        }
        self.assertEqual(before, after)
        self.assertEqual(
            [item["label"] for item in result["fitted_to_runs"]],
            ["fitted-to-run-01-backfill", "fitted-to-run-02"],
        )
        self.assertTrue(result["read_only_tree_fingerprints"]["unchanged"])
        self.assertEqual(events[0], "track5-tool-source-and-sidecar-verified")
        self.assertEqual(events[1], "cad-evidence-paths-first-opened")

    def test_phase1_integrity_corruption_stops_before_verifier(self) -> None:
        verifier = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            experiments = root / "experiments"
            experiments.mkdir()
            generator = experiments / "generate_stepped_rotational_v1.py"
            _ = generator.write_bytes(b"corrupt\n")
            _ = generator.with_suffix(".sha256").write_text(
                f"{reconciliation.GENERATOR_SHA256}  {generator.name}\n",
                encoding="ascii",
            )
            with self.assertRaisesRegex(
                reconciliation.ReconciliationError, "generator"
            ):
                _ = reconciliation.verify_phase1(root, [], verifier)
        verifier.assert_not_called()

    def test_generated_truth_comparison_records_maximum_error(self) -> None:
        estimate = dict(self.estimate)
        estimate["radius.band-1_m"] += 5e-10
        report = reconciliation.generated_truth_comparison(
            reconciliation.frozen_prediction_bytes(estimate)
        )
        self.assertAlmostEqual(report["maximum_linear_error_m"], 5e-10)
        self.assertEqual(report["outcome"], "tolerated numerical")


if __name__ == "__main__":
    _ = unittest.main()
