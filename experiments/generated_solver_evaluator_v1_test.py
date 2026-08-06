#!/usr/bin/env -S uv run
# /// script
# requires-python = "==3.12.12"
# dependencies = [
#     "numpy==2.3.1",
#     "scipy==1.16.1",
# ]
# ///
"""Focused tests for the experiment-local generated solver/evaluator gate."""

from __future__ import annotations

import copy
import py_compile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import generated_solver_evaluator_v1 as gate
import numpy as np


class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = gate.load_contract()

    def expected_callback_candidates(
        self, kind: str, coordinates: np.ndarray
    ) -> list[tuple[str, np.ndarray]]:
        raw_direction = (
            np.array([0.23, -0.31, 0.37, -0.41, 0.43, -0.47, 0.53])
            if kind == "shape"
            else np.array([0.31, -0.27, 0.19, -0.41, 0.53, -0.59])
        )
        direction = raw_direction / np.linalg.norm(raw_direction)
        offsets = (
            ("-2h", -2.0),
            ("-h", -1.0),
            ("-h/2", -0.5),
            ("+h/2", 0.5),
            ("+h", 1.0),
            ("+2h", 2.0),
        )
        expected = [("center", coordinates.copy())]
        for column in range(len(coordinates)):
            for magnitude, scale in offsets:
                candidate = coordinates.copy()
                candidate[column] += scale * gate.DERIVATIVE_STEP
                expected.append((f"coordinate[{column}]:{magnitude}", candidate))
        for magnitude, scale in offsets:
            expected.append(
                (
                    f"directional:{magnitude}",
                    coordinates + scale * gate.DERIVATIVE_STEP * direction,
                )
            )
        return expected

    def test_verified_generator_bytes_ignore_path_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generator.py"
            path.write_text("marker = 'substituted'\n", encoding="ascii")
            module = gate.execute_verified_module(
                b"marker = 'verified'\n", path, "test_verified_generator_substitution"
            )
            self.assertEqual(module.marker, "verified")

    def test_verified_generator_bytes_ignore_stale_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generator.py"
            stale_source = "marker = 'stale---'\n"
            verified_source = b"marker = 'verified'\n"
            path.write_text(stale_source, encoding="ascii")
            py_compile.compile(str(path), doraise=True)
            path.write_bytes(verified_source)
            module = gate.execute_verified_module(
                verified_source, path, "test_verified_generator_stale_bytecode"
            )
            self.assertEqual(module.marker, "verified")

    def expected_numerical_evaluations(
        self, kind: str, coordinates: np.ndarray
    ) -> list[np.ndarray]:
        raw_direction = (
            np.array([0.23, -0.31, 0.37, -0.41, 0.43, -0.47, 0.53])
            if kind == "shape"
            else np.array([0.31, -0.27, 0.19, -0.41, 0.53, -0.59])
        )
        direction = raw_direction / np.linalg.norm(raw_direction)
        expected = []
        for step in (gate.DERIVATIVE_STEP, gate.DERIVATIVE_STEP / 2.0):
            for column in range(len(coordinates)):
                delta = np.zeros_like(coordinates)
                delta[column] = step
                expected.extend(
                    (
                        coordinates + 2.0 * delta,
                        coordinates + delta,
                        coordinates - delta,
                        coordinates - 2.0 * delta,
                    )
                )
        for step in (gate.DERIVATIVE_STEP / 2.0, gate.DERIVATIVE_STEP):
            expected.extend(
                (
                    coordinates + 2.0 * step * direction,
                    coordinates + step * direction,
                    coordinates - step * direction,
                    coordinates - 2.0 * step * direction,
                )
            )
        return expected

    def test_explicit_factor_projection_and_held_out_exclusion(self) -> None:
        held_out = {
            item.observation_id
            for item in self.data.observations.values()
            if item.role == "held_out"
        }
        self.assertEqual(len(self.data.factors), 454)
        self.assertTrue(
            held_out.isdisjoint(
                factor.observation_id for factor in self.data.factors.values()
            )
        )
        for scenario in self.data.scenarios.values():
            projected = gate.active_factors(self.data, scenario.scenario_id)
            self.assertEqual(
                tuple(item.factor_id for item in projected), scenario.active_factor_ids
            )

    def test_raw_residual_signs_and_support_diagnostics(self) -> None:
        cylinder = next(
            factor
            for factor in self.data.factors.values()
            if factor.element_id == "cylinder.band-1"
        )
        point, _ = gate.truth_local(
            self.data, self.data.observations[cylinder.observation_id]
        )
        gradient = gate.support_gradient(point, cylinder.element_id)
        self.assertAlmostEqual(
            gate.raw_residual(
                point + 1e-6 * gradient, cylinder.element_id, gate.SHAPE_TRUTH
            ),
            1e-6,
            places=14,
        )
        outside = point.copy()
        outside[2] = 1.0
        inside, diagnostic = gate.generated_guard_domain(
            outside, cylinder, gate.SHAPE_TRUTH
        )
        self.assertFalse(inside)
        self.assertIn("outside", diagnostic)

        for element, coordinate, direction in (
            ("plane.station-20", 2, -1.0),
            ("plane.datum-flat", 0, 1.0),
        ):
            factor = next(
                item
                for item in self.data.factors.values()
                if item.element_id == element
            )
            point, _ = gate.truth_local(
                self.data, self.data.observations[factor.observation_id]
            )
            perturbed = point.copy()
            perturbed[coordinate] += direction * 1e-6
            self.assertAlmostEqual(
                gate.raw_residual(perturbed, element, gate.SHAPE_TRUTH),
                1e-6,
                places=14,
            )

    def test_cylinder_domain_uses_support_projection(self) -> None:
        factor = next(
            item
            for item in self.data.factors.values()
            if item.variant == "asymmetric_datum_flat"
            and item.element_id == "cylinder.band-2"
        )
        point = np.array([0.014966, 0.0100, 0.035])
        candidate = gate.SHAPE_TRUTH.copy()
        candidate[1] = 0.020
        candidate[6] = 0.015
        inside, diagnostic = gate.physical_support_domain(point, factor, candidate)
        self.assertFalse(inside)
        self.assertIn("outside", diagnostic)

        axis_point = np.array([0.0, 0.0, 0.035])
        for evaluator in (gate.generated_guard_domain, gate.physical_support_domain):
            inside, diagnostic = evaluator(axis_point, factor, gate.SHAPE_TRUTH)
            self.assertFalse(inside)
            self.assertIn("degenerate cylinder projection", diagnostic)

    def test_invalid_geometry_has_specific_diagnostic(self) -> None:
        invalid = gate.SHAPE_TRUTH.copy()
        invalid[1] = -0.018
        valid, diagnostic = gate.geometry_valid(invalid)
        self.assertFalse(valid)
        self.assertEqual(diagnostic, "radii must be positive")

    def test_evidence_runtime_is_exactly_pinned(self) -> None:
        self.assertEqual(gate.verify_runtime((3, 12, 12)), "3.12.12")
        with self.assertRaisesRegex(gate.GateError, "requires exact runtime"):
            gate.verify_runtime((3, 12, 11))
        with self.assertRaisesRegex(gate.GateError, "requires exact runtime"):
            gate.verify_runtime((3, 12, 12), "pypy")
        with self.assertRaisesRegex(gate.GateError, "requires exact runtime"):
            gate.verify_runtime((3, 12, 12), "cpython", "2.3.2", "1.16.1")
        with self.assertRaisesRegex(gate.GateError, "requires exact runtime"):
            gate.verify_runtime((3, 12, 12), "cpython", "2.3.1", "1.16.2")

    def test_rank_spectra_and_derivative_check(self) -> None:
        ranks = [
            gate.spectrum(gate.pose_jacobian(self.data, scenario))["raw_rank"]
            for scenario in (
                "axisymmetric-free-roll",
                "asymmetric-full-pose",
                "flat-factor-ablation",
            )
        ]
        self.assertEqual(ranks, [5, 6, 5])
        derivatives = gate.derivative_checks(self.data)
        self.assertEqual(set(derivatives["probes"]), {"pose", "shape"})
        for probes in derivatives["probes"].values():
            expected_points = (
                [
                    "nominal",
                    "perturbed",
                    "small-angle",
                    "near-parameter-bound-raw",
                ]
                if len(probes) == 4
                else ["nominal", "perturbed", "near-parameter-bound-raw"]
            )
            self.assertEqual([probe["point"] for probe in probes], expected_points)
            for probe in probes:
                self.assertLessEqual(probe["max_abs_error"], gate.DERIVATIVE_ABS_TOL)
                self.assertLessEqual(
                    probe["max_dimensionless_abs_error"],
                    gate.DERIVATIVE_DIMENSIONLESS_ABS_TOL,
                )
                self.assertTrue(all(probe["factor_kind_counts"].values()))
                self.assertEqual(len(probe["factor_element_counts"]), 8)
                self.assertEqual(
                    probe["raw_factor_count"],
                    probe["all_active_factor_count"],
                )
                self.assertEqual(probe["raw_factor_count"], 230)

        expected_counts = {
            "cylinder.band-1": 56,
            "cylinder.band-2": 48,
            "cylinder.band-3": 56,
            "plane.datum-flat": 16,
            "plane.station-0": 14,
            "plane.station-20": 14,
            "plane.station-50": 12,
            "plane.station-80": 14,
        }
        expected_executions = []
        for kind, probes in derivatives["probes"].items():
            for probe in probes:
                evaluations = self.expected_numerical_evaluations(
                    kind, np.asarray(probe["coordinates"])
                )
                expected_executions.append(
                    {
                        "kind": kind,
                        "point": probe["point"],
                        "raw_residual_coordinate_sequence_sha256": gate.sha256(
                            gate.canonical_json(
                                [evaluation.tolist() for evaluation in evaluations]
                            )
                        ),
                        "raw_residual_evaluation_count": len(evaluations),
                        "raw_residual_unique_coordinate_count": len(
                            {evaluation.tobytes() for evaluation in evaluations}
                        ),
                        "validated_steps": [
                            gate.DERIVATIVE_STEP,
                            gate.DERIVATIVE_STEP / 2.0,
                        ],
                    }
                )
        self.assertEqual(
            derivatives["numerical_stencil_executions"], expected_executions
        )
        self.assertEqual(
            {
                (item["kind"], item["raw_residual_evaluation_count"])
                for item in expected_executions
            },
            {("shape", 64), ("pose", 56)},
        )
        self.assertEqual(
            {
                (item["kind"], item["raw_residual_unique_coordinate_count"])
                for item in expected_executions
            },
            {("shape", 48), ("pose", 42)},
        )
        self.assertEqual(len(derivatives["callback_domain_probes"]), 4)
        for probe in derivatives["callback_domain_probes"]:
            kind = "shape" if probe["point"].startswith("shape") else "pose"
            expected_candidates = self.expected_callback_candidates(
                kind, np.asarray(probe["coordinates"])
            )
            expected_ids = self.data.scenarios["asymmetric-full-pose"].active_factor_ids
            expected_hash = gate.sha256(gate.canonical_json(list(expected_ids)))
            expected_coordinates = [
                candidate.tolist() for _, candidate in expected_candidates
            ]
            expected_paths = [f"{kind}.residual", f"{kind}.analytic_jacobian"]
            expected_trace = [
                {
                    "coordinates": candidate.tolist(),
                    "factor_ids": list(expected_ids),
                    "path": path,
                    "row_count": 230,
                }
                for _, candidate in expected_candidates
                for path in expected_paths
            ]
            self.assertEqual(probe["active_factor_count"], 230)
            self.assertEqual(probe["active_factor_ids_sha256"], expected_hash)
            self.assertEqual(
                probe["stencil_candidate_count"],
                49 if probe["point"].startswith("shape") else 43,
            )
            self.assertEqual(
                probe["unique_stencil_candidate_count"],
                probe["stencil_candidate_count"],
            )
            self.assertEqual(
                probe["callback_evaluation_count"],
                2 * probe["stencil_candidate_count"],
            )
            self.assertEqual(
                probe["candidate_labels_sha256"],
                gate.sha256(
                    gate.canonical_json([label for label, _ in expected_candidates])
                ),
            )
            actual_coordinates = [
                np.asarray(candidate) for candidate in expected_coordinates
            ]
            self.assertEqual(
                len({candidate.tobytes() for candidate in actual_coordinates}),
                len(actual_coordinates),
            )
            self.assertEqual(
                probe["candidate_coordinates_sha256"],
                gate.sha256(gate.canonical_json(expected_coordinates)),
            )
            self.assertEqual(probe["callback_paths_per_candidate"], expected_paths)
            self.assertEqual(
                probe["callback_trace_sha256"],
                gate.sha256(gate.canonical_json(expected_trace)),
            )
            self.assertEqual(probe["factor_element_counts"], expected_counts)
            self.assertTrue(probe["factor_element_counts_exact_every_candidate"])
            self.assertTrue(probe["ordered_factor_ids_exact_active_every_evaluation"])
            self.assertEqual(
                probe["ordered_factor_ids_sha256_every_evaluation"], expected_hash
            )
            self.assertEqual(probe["row_count_every_evaluation"], 230)

        points = gate.local_points(self.data)
        factors = gate.active_factors(self.data, "asymmetric-full-pose")
        nonzero = gate.raw_pose_jacobian(factors, points, gate.POSE_INITIAL)
        identity = gate.raw_pose_jacobian(factors, points, np.zeros(6))
        self.assertGreater(np.max(np.abs(nonzero - identity)), 1e-4)

    def test_support_count_is_informational_and_never_filters_raw_rows(self) -> None:
        with mock.patch.object(gate, "supported_derivative_factors", return_value=()):
            probe = gate.derivative_probe(
                self.data,
                "asymmetric-full-pose",
                "shape",
                "support-count-regression",
                gate.SHAPE_TRUTH,
            )
        self.assertEqual(probe["callback_supported_factor_count"], 0)
        self.assertEqual(probe["all_active_factor_count"], 230)
        self.assertEqual(probe["raw_factor_count"], 230)
        self.assertEqual(sum(probe["factor_element_counts"].values()), 230)

    def test_nonfinite_coarse_directional_result_fails_closed(self) -> None:
        finite = np.zeros(230)
        nonfinite = np.full(230, np.nan)
        with (
            mock.patch.object(
                gate,
                "five_point_directional",
                side_effect=(finite, nonfinite),
            ),
            self.assertRaisesRegex(
                gate.GateError, "non-finite directional derivative at h"
            ),
        ):
            gate.derivative_probe(
                self.data,
                "asymmetric-full-pose",
                "shape",
                "nonfinite-coarse-regression",
                gate.SHAPE_TRUTH,
            )

    def test_callback_trace_mismatch_and_rejection_fail_proof(self) -> None:
        factors = gate.active_factors(self.data, "asymmetric-full-pose")
        points = gate.local_points(self.data)
        candidate = [("center", gate.SHAPE_TRUTH.copy())]

        reordered = gate.FitCallback(self.data, factors, points)
        reordered.shape(gate.SHAPE_TRUTH)
        reordered.shape_jacobian(gate.SHAPE_TRUTH)
        self.assertEqual(
            [invocation.path for invocation in reordered.invocations],
            ["shape.residual", "shape.analytic_jacobian"],
        )
        for invocation in reordered.invocations:
            np.testing.assert_array_equal(invocation.coordinates, gate.SHAPE_TRUTH)
            self.assertEqual(
                tuple(invocation.factor_ids), tuple(f.factor_id for f in factors)
            )
            self.assertEqual(invocation.row_count, 230)
        reordered.invocations[0].factor_ids.reverse()
        with self.assertRaisesRegex(gate.GateError, "ordered factor mismatch"):
            gate.callback_invocation_evidence(reordered, candidate, factors, "shape")
        with self.assertRaisesRegex(gate.GateError, "ordered factors differ"):
            gate.callback_audit(self.data, reordered)

        rejected_points = {key: value.copy() for key, value in points.items()}
        rejected_points[factors[0].observation_id][2] = 1.0
        rejected = gate.FitCallback(self.data, factors, rejected_points)
        with self.assertRaisesRegex(gate.GateError, factors[0].factor_id):
            rejected.shape(gate.SHAPE_TRUTH)
        with self.assertRaisesRegex(gate.GateError, "trace count mismatch"):
            gate.callback_invocation_evidence(rejected, candidate, factors, "shape")

    def test_pose_roll_gauge_and_flat_ablation(self) -> None:
        axisymmetric, _ = gate.solve_pose(self.data, "axisymmetric-free-roll")
        asymmetric, _ = gate.solve_pose(self.data, "asymmetric-full-pose")
        ablation, _ = gate.solve_pose(self.data, "flat-factor-ablation")
        self.assertLessEqual(
            axisymmetric["max_abs_nonroll_error"], gate.RECOVERY_TOLERANCE
        )
        self.assertGreater(abs(axisymmetric["roll_rad"]), 1e-3)
        self.assertLessEqual(
            asymmetric["max_abs_nonroll_error"], gate.RECOVERY_TOLERANCE
        )
        self.assertLessEqual(abs(asymmetric["roll_rad"]), gate.RECOVERY_TOLERANCE)
        self.assertGreater(abs(ablation["roll_rad"]), 1e-3)

    def test_held_out_oracle_acceptance_fails_closed(self) -> None:
        fixed, _ = gate.solve_shape(self.data, "noiseless-fixed-pose")
        fixed_shape = np.array([fixed["estimate"][name] for name in gate.SHAPE_NAMES])
        held_out = gate.held_out_oracle(self.data, fixed_shape)
        self.assertTrue(gate.held_out_oracle_acceptable(held_out))

        for field, value in (
            ("support_failures", [{"observation_id": "regression", "reason": "x"}]),
            ("max_abs_residual_m", 2.0 * gate.ORACLE_TOLERANCE),
            ("max_abs_residual_m", float("nan")),
            ("max_abs_residual_m", float("inf")),
        ):
            with self.subTest(field=field, value=value):
                rejected = copy.deepcopy(held_out)
                rejected[field] = value
                self.assertFalse(gate.held_out_oracle_acceptable(rejected))

    def test_free_roll_gauge_solution_acceptance_fails_closed(self) -> None:
        ranks = {
            scenario: gate.spectrum(gate.pose_jacobian(self.data, scenario))
            for scenario in (
                "axisymmetric-free-roll",
                "asymmetric-full-pose",
                "flat-factor-ablation",
            )
        }
        gauges = gate.gauge_checks(self.data, ranks)
        for scenario in ("axisymmetric-free-roll", "flat-factor-ablation"):
            nominal = gauges[scenario]
            self.assertTrue(gate.free_roll_gauge_acceptable(nominal))
            for field, value in (
                ("success", False),
                ("max_abs_nonroll_error", 2.0 * gate.RECOVERY_TOLERANCE),
                ("max_abs_residual_m", 2.0 * gate.ORACLE_TOLERANCE),
                ("max_abs_nonroll_error", float("nan")),
                ("max_abs_residual_m", float("inf")),
                ("roll_rad", float("nan")),
            ):
                for solution in ("solution_minus", "solution_plus"):
                    with self.subTest(
                        scenario=scenario, solution=solution, field=field, value=value
                    ):
                        rejected = copy.deepcopy(nominal)
                        rejected[solution][field] = value
                        self.assertFalse(gate.free_roll_gauge_acceptable(rejected))

    def test_asymmetric_datum_solution_acceptance_fails_closed(self) -> None:
        ranks = {
            scenario: gate.spectrum(gate.pose_jacobian(self.data, scenario))
            for scenario in (
                "axisymmetric-free-roll",
                "asymmetric-full-pose",
                "flat-factor-ablation",
            )
        }
        rank = ranks["asymmetric-full-pose"]
        nominal = gate.gauge_checks(self.data, ranks)["asymmetric-full-pose"]
        self.assertTrue(gate.asymmetric_datum_gauge_acceptable(nominal, rank))
        for field, value in (
            ("minus_max_abs_residual_m", float("nan")),
            ("plus_max_abs_residual_m", float("inf")),
        ):
            with self.subTest(field=field, value=value):
                rejected = copy.deepcopy(nominal)
                rejected[field] = value
                self.assertFalse(gate.asymmetric_datum_gauge_acceptable(rejected, rank))
        for field, value in (
            ("success", False),
            ("max_abs_nonroll_error", 2.0 * gate.RECOVERY_TOLERANCE),
            ("max_abs_residual_m", 2.0 * gate.ORACLE_TOLERANCE),
            ("roll_rad", 2.0 * gate.RECOVERY_TOLERANCE),
            ("max_abs_nonroll_error", float("nan")),
            ("max_abs_residual_m", float("inf")),
            ("roll_rad", float("nan")),
        ):
            for solution in ("solution_minus", "solution_plus"):
                with self.subTest(solution=solution, field=field, value=value):
                    rejected = copy.deepcopy(nominal)
                    rejected[solution][field] = value
                    self.assertFalse(
                        gate.asymmetric_datum_gauge_acceptable(rejected, rank)
                    )

    def test_gate_and_adverse_termination_disposition(self) -> None:
        report = gate.run_gate()
        adverse = report["scenarios"]["coverage-inadequate"]
        self.assertTrue(all(report["checks"].values()))
        self.assertTrue(adverse["termination"]["success"])
        self.assertEqual(adverse["disposition"], "failed")
        self.assertTrue(adverse["diagnostics"]["missing_required_mappings"])
        self.assertTrue(adverse["diagnostics"]["missing_coverage_cells"])
        self.assertTrue(report["checks"]["analytic_solver_jacobians_used"])
        self.assertTrue(
            report["checks"]["raw_derivative_probes_use_all_active_factors"]
        )
        self.assertTrue(
            report["checks"]["callback_domain_stencils_use_all_active_factors"]
        )
        self.assertTrue(report["checks"]["roll_gauge_equivalence_and_null_alignment"])
        boundaries = report["declared_evidence_boundaries"]
        self.assertIn(
            "not established by executable checks", boundaries["classification"]
        )
        self.assertFalse(boundaries["cad_evidence_consumed"])
        self.assertFalse(boundaries["physical_reference_consumed"])

    def test_adverse_realizations_and_mapping_rejection_are_measured(self) -> None:
        outlier_scenario = self.data.scenarios["fixed-outliers"]
        offsets = outlier_scenario.declaration["outlier_offsets_m"]
        points = gate.local_points(self.data, offsets)
        application = gate.offset_application_evidence(self.data, offsets, points)
        self.assertEqual(application["changed_observation_count"], 3)
        self.assertEqual(application["unchanged_observation_count"], 618)

        baseline = gate.local_points(self.data)
        mismatch_points, mismatch = gate.mismatch_points_and_bounds(self.data)
        middle_ids = {
            factor.observation_id
            for factor in gate.active_factors(self.data, "out-of-contract-mismatch")
            if factor.element_id == "cylinder.band-2"
        }
        self.assertEqual(len(middle_ids), 104)
        self.assertEqual(mismatch["declared_training_middle_factor_count"], 104)
        for observation_id, point in mismatch_points.items():
            if observation_id not in middle_ids:
                np.testing.assert_array_equal(point, baseline[observation_id])
                continue
            theta = np.arctan2(baseline[observation_id][1], baseline[observation_id][0])
            self.assertAlmostEqual(point[0], 0.018 * np.cos(theta), places=15)
            self.assertAlmostEqual(point[1], 0.017 * np.sin(theta), places=15)

        expected_ordered_ids = [
            factor.observation_id
            for factor in gate.active_factors(self.data, "out-of-contract-mismatch")
            if factor.element_id == "cylinder.band-2"
        ]
        ordered_ids = mismatch["ordered_middle_observation_ids"]
        self.assertEqual(ordered_ids, expected_ordered_ids)
        expected_angles = [
            float(np.arctan2(baseline[item][1], baseline[item][0]))
            for item in ordered_ids
        ]
        expected_radii = np.asarray(
            [
                np.hypot(0.018 * np.cos(angle), 0.017 * np.sin(angle))
                for angle in expected_angles
            ]
        )
        expected_radius = float(np.mean(expected_radii))
        expected_residuals = expected_radii - expected_radius
        self.assertEqual(mismatch["analytic_middle_angles_rad"], expected_angles)
        self.assertAlmostEqual(
            mismatch["analytic_circular_least_squares_radius_m"],
            expected_radius,
            places=15,
        )
        np.testing.assert_allclose(
            mismatch["analytic_middle_residuals_m"],
            expected_residuals,
            rtol=0.0,
            atol=1e-15,
        )
        self.assertAlmostEqual(
            mismatch["analytic_middle_residual_span_m"],
            float(np.ptp(expected_residuals)),
            places=15,
        )
        self.assertAlmostEqual(
            mismatch["analytic_middle_residual_rms_m"],
            float(np.sqrt(np.mean(expected_residuals**2))),
            places=15,
        )
        expected_radii_list = expected_radii.tolist()
        expected_residual_list = expected_residuals.tolist()
        expected_samples = [
            {
                "angle_rad": angle,
                "observation_id": observation_id,
                "sampled_radius_m": radius,
            }
            for observation_id, angle, radius in zip(
                ordered_ids, expected_angles, expected_radii_list, strict=True
            )
        ]
        self.assertEqual(
            mismatch["declared_training_middle_ids_sha256"],
            gate.sha256(gate.canonical_json(ordered_ids)),
        )
        self.assertEqual(
            mismatch["analytic_middle_angles_rad_sha256"],
            gate.sha256(gate.canonical_json(expected_angles)),
        )
        self.assertEqual(
            mismatch["analytic_sampled_radii_sha256"],
            gate.sha256(gate.canonical_json(expected_radii_list)),
        )
        self.assertEqual(
            mismatch["analytic_middle_residuals_sha256"],
            gate.sha256(gate.canonical_json(expected_residual_list)),
        )
        self.assertEqual(
            mismatch["analytic_middle_sample_binding_sha256"],
            gate.sha256(gate.canonical_json(expected_samples)),
        )

        with mock.patch.object(
            gate,
            "physical_support_domain",
            wraps=gate.physical_support_domain,
        ) as support:
            rejected = gate.rejected_corrupted_mapping(self.data)
        self.assertEqual(support.call_count, 1)
        self.assertEqual(rejected["diagnostics"]["retarget_attempt_count"], 0)

    def test_acceptance_policies_derive_from_measured_evidence(self) -> None:
        self.assertEqual(
            gate.coverage_acceptance_policy(
                {"missing_required_mappings": [], "missing_coverage_cells": {}}
            ),
            gate.DISPOSITION_PASSED,
        )
        self.assertEqual(
            gate.coverage_acceptance_policy(
                {
                    "missing_required_mappings": [],
                    "missing_coverage_cells": {"mapping": ["cell"]},
                }
            ),
            gate.DISPOSITION_REVIEW_REQUIRED,
        )
        self.assertEqual(
            gate.coverage_acceptance_policy(
                {
                    "missing_required_mappings": ["mapping"],
                    "missing_coverage_cells": {},
                }
            ),
            gate.DISPOSITION_FAILED,
        )
        self.assertEqual(
            gate.fixed_outlier_acceptance_policy(), gate.DISPOSITION_UNCLASSIFIED
        )

        report = gate.run_gate()
        scenarios = report["scenarios"]
        for scenario_id, policy in (
            ("corrupted-mapping", gate.corrupted_mapping_acceptance_policy),
            ("legal-active-bound", gate.active_bound_acceptance_policy),
            ("invalid-geometry-declaration", gate.invalid_geometry_acceptance_policy),
            ("out-of-contract-mismatch", gate.mismatch_acceptance_policy),
        ):
            self.assertEqual(
                scenarios[scenario_id]["disposition"], policy(scenarios[scenario_id])
            )

        corrupted_nominal = scenarios["corrupted-mapping"]
        for field, value in (
            ("classification", "unresolved"),
            ("retarget_attempt_count", 1),
            ("support_attempt_mapping_ids", []),
        ):
            with self.subTest(policy="corrupted", field=field):
                corrupted = copy.deepcopy(corrupted_nominal)
                corrupted["diagnostics"][field] = value
                self.assertEqual(
                    gate.corrupted_mapping_acceptance_policy(corrupted),
                    gate.DISPOSITION_UNCLASSIFIED,
                )
        for section, field, value in (
            ("support", "factor_traversal_count", 2),
            ("support", "failures", []),
            ("raw", "residual_traversal_count", 1),
            ("raw", "jacobian_traversal_count", 1),
            ("callbacks", "callback_calls", 1),
            ("solver", "invoked", True),
            ("termination", "class", "solver-returned"),
        ):
            with self.subTest(policy="corrupted", section=section, field=field):
                corrupted = copy.deepcopy(corrupted_nominal)
                corrupted[section][field] = value
                self.assertEqual(
                    gate.corrupted_mapping_acceptance_policy(corrupted),
                    gate.DISPOSITION_UNCLASSIFIED,
                )

        active_nominal = scenarios["legal-active-bound"]
        for field, value in (
            ("classification", "suspicious-active"),
            ("active_mask", [0] * 7),
            ("raw_rank", 6),
            ("feasible_tangent_rank", 5),
        ):
            with self.subTest(policy="active-review", field=field):
                active = copy.deepcopy(active_nominal)
                active["diagnostics"][field] = value
                self.assertEqual(
                    gate.active_bound_acceptance_policy(active),
                    gate.DISPOSITION_REVIEW_REQUIRED,
                )
        active = copy.deepcopy(active_nominal)
        active["diagnostics"]["distance_to_lower_bounds_m"][0] = 2e-9
        self.assertEqual(
            gate.active_bound_acceptance_policy(active),
            gate.DISPOSITION_REVIEW_REQUIRED,
        )
        for section, field, value in (
            ("termination", "success", False),
            ("diagnostics", "geometry_valid", False),
            ("diagnostics", "initialization_valid", False),
            ("support", "failures", [{"factor_id": "x", "reason": "x"}]),
        ):
            with self.subTest(policy="active-failed", section=section, field=field):
                active = copy.deepcopy(active_nominal)
                active[section][field] = value
                self.assertEqual(
                    gate.active_bound_acceptance_policy(active),
                    gate.DISPOSITION_FAILED,
                )
        for field in ("distance_to_lower_bounds_m", "distance_to_upper_bounds_m"):
            active = copy.deepcopy(active_nominal)
            active["diagnostics"][field][-1] = -1e-12
            self.assertEqual(
                gate.active_bound_acceptance_policy(active), gate.DISPOSITION_FAILED
            )
            active = copy.deepcopy(active_nominal)
            active["diagnostics"][field].pop()
            self.assertEqual(
                gate.active_bound_acceptance_policy(active), gate.DISPOSITION_FAILED
            )

        invalid_nominal = scenarios["invalid-geometry-declaration"]
        for section, field, value in (
            ("diagnostics", "classification", "unresolved"),
            ("diagnostics", "message", "generic failure"),
            ("support", "factor_traversal_count", 1),
            ("raw", "residual_traversal_count", 1),
            ("raw", "jacobian_traversal_count", 1),
            ("callbacks", "callback_calls", 1),
            ("solver", "invoked", True),
            ("termination", "class", "solver-returned"),
        ):
            with self.subTest(policy="invalid", section=section, field=field):
                invalid = copy.deepcopy(invalid_nominal)
                invalid[section][field] = value
                self.assertEqual(
                    gate.invalid_geometry_acceptance_policy(invalid),
                    gate.DISPOSITION_UNCLASSIFIED,
                )

        mismatch_nominal = scenarios["out-of-contract-mismatch"]
        for section, field, value in (
            ("termination", "success", False),
            ("diagnostics", "classification", "unresolved"),
            ("diagnostics", "analytic_mismatch_nonzero", False),
            ("diagnostics", "matches_analytic_least_squares_expectation", False),
        ):
            with self.subTest(policy="mismatch", section=section, field=field):
                mismatch = copy.deepcopy(mismatch_nominal)
                mismatch[section][field] = value
                self.assertEqual(
                    gate.mismatch_acceptance_policy(mismatch),
                    gate.DISPOSITION_FAILED,
                )

    def test_coverage_reference_is_independent_of_adequate_selection(self) -> None:
        reference = gate.coverage_reference(self.data)
        self.assertEqual(reference["factor_count"], 454)
        self.assertEqual(reference["mapping_count"], 15)

        scenario = self.data.scenarios["coverage-adequate"]
        first_factor = self.data.factors[scenario.active_factor_ids[0]]
        target_mapping = first_factor.mapping_id
        target_cell = self.data.records[first_factor.observation_id]["coverage_cell"]
        retained_ids = tuple(
            factor_id
            for factor_id in scenario.active_factor_ids
            if not (
                self.data.factors[factor_id].mapping_id == target_mapping
                and self.data.records[self.data.factors[factor_id].observation_id][
                    "coverage_cell"
                ]
                == target_cell
            )
        )
        mutated = copy.copy(self.data)
        mutated.scenarios = dict(self.data.scenarios)
        mutated.scenarios["coverage-adequate"] = gate.Scenario(
            scenario.scenario_id,
            scenario.input_observation_ids,
            retained_ids,
            scenario.evaluation_only_ids,
            scenario.declaration,
        )
        diagnostics = gate.coverage_diagnostics(
            mutated, "coverage-adequate", gate.coverage_reference(mutated)
        )
        self.assertIn(target_mapping, diagnostics["missing_coverage_cells"])
        self.assertIn(
            target_cell, diagnostics["missing_coverage_cells"][target_mapping]
        )
        self.assertEqual(
            gate.coverage_acceptance_policy(diagnostics),
            gate.DISPOSITION_REVIEW_REQUIRED,
        )

    def test_pose_spectra_are_bound_to_recovered_coordinates(self) -> None:
        report = gate.run_gate()
        different_from_truth = []
        for scenario_id in (
            "axisymmetric-free-roll",
            "asymmetric-full-pose",
            "flat-factor-ablation",
        ):
            result = report["scenarios"][scenario_id]
            coordinates = np.asarray(result["raw"]["evaluation_coordinates"])
            expected = gate.pose_spectrum_evidence(self.data, scenario_id, coordinates)
            expected_coordinate_hash = gate.sha256(
                gate.canonical_json(coordinates.tolist())
            )
            self.assertEqual(
                result["raw"]["evaluation_coordinates"],
                result["solver"]["estimate_local_observation_to_model"],
            )
            self.assertEqual(
                result["raw"]["jacobian_sha256"], expected["jacobian_sha256"]
            )
            self.assertEqual(
                result["raw"]["evaluation_coordinates_sha256"],
                expected_coordinate_hash,
            )
            self.assertEqual(
                result["raw"]["jacobian_evaluation_coordinates_sha256"],
                expected_coordinate_hash,
            )
            self.assertEqual(result["raw"]["spectrum"], expected["spectrum"])
            self.assertEqual(
                result["raw"]["spectrum_jacobian_sha256"],
                result["raw"]["jacobian_sha256"],
            )
            truth = report["truth_origin_pose_rank_compatibility"][scenario_id]
            self.assertEqual(truth["evaluation_coordinates"], [0.0] * 6)
            self.assertEqual(
                truth["evaluation_coordinates_sha256"],
                gate.sha256(gate.canonical_json([0.0] * 6)),
            )
            self.assertEqual(
                truth["spectrum_jacobian_sha256"], truth["jacobian_sha256"]
            )
            different_from_truth.append(
                result["raw"]["jacobian_sha256"] != truth["jacobian_sha256"]
            )
        self.assertTrue(any(different_from_truth))

    def test_all_frozen_scenarios_execute_fail_closed(self) -> None:
        report = gate.run_gate()
        scenarios = report["scenarios"]
        self.assertEqual(set(scenarios), set(self.data.scenarios))
        self.assertEqual(len(scenarios), 15)

        adequate = scenarios["coverage-adequate"]
        self.assertEqual(adequate["active_factor_count"], 454)
        self.assertEqual(adequate["diagnostics"]["covered_mapping_count"], 15)
        self.assertFalse(adequate["diagnostics"]["missing_required_mappings"])
        self.assertFalse(adequate["diagnostics"]["missing_coverage_cells"])
        self.assertEqual(adequate["raw"]["spectrum"]["raw_rank"], 7)
        self.assertLessEqual(
            adequate["solver"]["max_abs_error"], gate.RECOVERY_TOLERANCE
        )
        self.assertEqual(adequate["disposition"], "passed")

        uneven = scenarios["coverage-uneven"]
        self.assertEqual(uneven["active_factor_count"], 163)
        self.assertEqual(uneven["diagnostics"]["covered_mapping_count"], 15)
        self.assertFalse(uneven["diagnostics"]["missing_required_mappings"])
        self.assertTrue(uneven["diagnostics"]["missing_coverage_cells"])
        self.assertEqual(uneven["raw"]["spectrum"]["raw_rank"], 7)
        self.assertEqual(uneven["disposition"], "review-required")

        inadequate = scenarios["coverage-inadequate"]
        self.assertEqual(inadequate["active_factor_count"], 112)
        self.assertEqual(inadequate["raw"]["spectrum"]["raw_rank"], 1)
        self.assertTrue(inadequate["termination"]["success"])
        self.assertNotIn("disposition", inadequate["diagnostics"])
        self.assertTrue(
            {"message", "nfev", "status", "success"}.isdisjoint(inadequate["solver"])
        )
        self.assertEqual(inadequate["disposition"], "failed")

        outliers = scenarios["fixed-outliers"]
        self.assertEqual(
            outliers["diagnostics"]["outlier_offsets_m"],
            self.data.scenarios["fixed-outliers"].declaration["outlier_offsets_m"],
        )
        self.assertTrue(outliers["diagnostics"]["linear_loss_control"])
        self.assertIn("open", outliers["diagnostics"]["robust_comparison"])
        self.assertEqual(outliers["raw"]["spectrum"]["raw_rank"], 7)
        self.assertEqual(outliers["diagnostics"]["changed_observation_count"], 3)
        self.assertEqual(outliers["diagnostics"]["unchanged_observation_count"], 618)
        self.assertEqual(outliers["diagnostics"]["truth_nonzero_residual_count"], 3)
        self.assertEqual(outliers["disposition"], "unclassified")
        self.assertFalse(report["solver_settings"]["robust_probe_executed"])

        corrupted = scenarios["corrupted-mapping"]
        self.assertEqual(corrupted["diagnostics"]["classification"], "mapping-suspect")
        self.assertEqual(corrupted["diagnostics"]["unmodified_factor_count"], 453)
        self.assertEqual(corrupted["support"]["factor_traversal_count"], 1)
        self.assertEqual(
            corrupted["diagnostics"]["support_attempt_mapping_ids"],
            ["mapping.axisymmetric.cylinder.band-3"],
        )
        self.assertEqual(corrupted["diagnostics"]["retarget_attempt_count"], 0)
        self.assertEqual(corrupted["callbacks"]["callback_calls"], 0)
        self.assertFalse(corrupted["solver"]["invoked"])

        active = scenarios["legal-active-bound"]
        self.assertEqual(active["diagnostics"]["active_mask"], [-1, 0, 0, 0, 0, 0, 0])
        self.assertLessEqual(
            active["diagnostics"]["distance_to_lower_bounds_m"][0], 1e-9
        )
        self.assertGreaterEqual(
            active["diagnostics"]["distance_to_lower_bounds_m"][0], 0.0
        )
        self.assertTrue(active["diagnostics"]["initialization_valid"])
        self.assertEqual(active["diagnostics"]["raw_rank"], 7)
        self.assertEqual(active["diagnostics"]["feasible_tangent_rank"], 6)
        self.assertTrue(active["diagnostics"]["geometry_valid"])
        self.assertEqual(active["diagnostics"]["classification"], "expected-active")

        invalid = scenarios["invalid-geometry-declaration"]
        self.assertEqual(invalid["diagnostics"]["message"], "radii must be positive")
        self.assertEqual(invalid["support"]["factor_traversal_count"], 0)
        self.assertEqual(invalid["callbacks"]["callback_calls"], 0)
        self.assertFalse(invalid["solver"]["invoked"])

        mismatch = scenarios["out-of-contract-mismatch"]
        self.assertEqual(
            mismatch["diagnostics"]["realization"], "runner-local/non-normative"
        )
        self.assertEqual(
            mismatch["diagnostics"]["classification"], "model-mismatch-suspect"
        )
        self.assertTrue(mismatch["diagnostics"]["non_middle_observations_unchanged"])
        self.assertEqual(
            mismatch["diagnostics"]["declared_training_middle_factor_count"], 104
        )
        self.assertTrue(mismatch["diagnostics"]["analytic_mismatch_nonzero"])
        self.assertTrue(
            mismatch["diagnostics"]["matches_analytic_least_squares_expectation"]
        )
        self.assertLessEqual(
            mismatch["diagnostics"]["fitted_middle_radius_error_m"],
            gate.MISMATCH_ANALYTIC_TOLERANCE_M,
        )
        self.assertLessEqual(
            mismatch["diagnostics"]["middle_residual_vector_max_abs_error_m"],
            gate.MISMATCH_ANALYTIC_TOLERANCE_M,
        )
        self.assertEqual(mismatch["disposition"], "review-required")

        for scenario in scenarios.values():
            self.assertFalse(scenario["callbacks"]["held_out_seen"])

    def test_retained_evidence_and_sidecar_recompute(self) -> None:
        evidence = Path(gate.__file__).with_name(
            "generated-solver-evaluator-v1-evidence.json"
        )
        self.assertEqual(gate.verify_evidence(evidence)["format"], gate.FORMAT)


if __name__ == "__main__":
    unittest.main()
