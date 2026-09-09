from __future__ import annotations

import math
from typing import Literal

from scansor.declared_generation_models import FixtureDefinition, FixtureSample
from scansor.errors import ScansorError
from scansor.model_declarations import ModelDeclaration
from scansor.rigid_transform import RigidTransform
from scansor.stepped_model_declarations import stepped_model_declaration
from scansor.tube_model_declarations import tube_model_declaration


def _angles(count: int) -> tuple[tuple[int, float], ...]:
    return tuple(
        (index, (2.0 * math.pi * index / count) + math.pi / count)
        for index in range(count)
    )


def _cylinder_role(z_index: int, angle_index: int) -> Literal["training", "held-out"]:
    return "held-out" if z_index == 2 or angle_index in {3, 4} else "training"


def _plane_role(angle_index: int) -> Literal["training", "held-out"]:
    return "held-out" if angle_index == 2 else "training"


def _nominal_by_parameter_id(declaration: ModelDeclaration) -> dict[str, float]:
    return {
        parameter.parameter_id: parameter.nominal
        for parameter in declaration.parameters
    }


def _canonical_samples(
    declaration: ModelDeclaration,
    samples: list[FixtureSample],
) -> tuple[FixtureSample, ...]:
    element_order = {
        element.element_id: index for index, element in enumerate(declaration.elements)
    }
    unknown = {sample.element_id for sample in samples} - set(element_order)
    if unknown:
        raise ValueError(f"fixture sample references undeclared element {min(unknown)}")
    keys = tuple((sample.element_id, sample.key) for sample in samples)
    if len(keys) != len(set(keys)):
        raise ValueError("fixture sample keys must be unique within each element")
    return tuple(
        sorted(
            samples,
            key=lambda sample: (element_order[sample.element_id], sample.key),
        )
    )


def _stepped_samples(declaration: ModelDeclaration) -> tuple[FixtureSample, ...]:
    nominal = _nominal_by_parameter_id(declaration)
    samples: list[FixtureSample] = []
    radii = (nominal["r1"], nominal["r2"], nominal["r3"])
    stations = (0.0, nominal["s20"], nominal["s50"], nominal["s80"])
    datum_x = nominal["datum_x"]
    for band, radius in enumerate(radii):
        for z_index in range(5):
            z = (
                stations[band]
                + 0.002
                + ((stations[band + 1] - stations[band] - 0.004) * z_index / 4)
            )
            for angle_index, angle in _angles(16):
                x = radius * math.cos(angle)
                if band == 1 and x > datum_x - 0.001:
                    continue
                y = radius * math.sin(angle)
                samples.append(
                    FixtureSample(
                        element_id=f"cylinder.band-{band + 1}",
                        key=f"z{z_index:02d}.a{angle_index:02d}",
                        normal_model=(math.cos(angle), math.sin(angle), 0.0),
                        point_model_m=(x, y, z),
                        role=_cylinder_role(z_index, angle_index),
                    )
                )

    plane_specs = (
        ("plane.station-0", 0.0, 0.0, radii[0] - 0.001, -1.0),
        (
            "plane.station-20",
            stations[1],
            radii[0] + 0.001,
            radii[1] - 0.001,
            -1.0,
        ),
        (
            "plane.station-50",
            stations[2],
            radii[2] + 0.001,
            radii[1] - 0.001,
            1.0,
        ),
        ("plane.station-80", stations[3], 0.0, radii[2] - 0.001, 1.0),
    )
    for element_id, z, radial_min, radial_max, normal_z in plane_specs:
        radial_values = (
            (radial_max * 0.45, radial_max * 0.75)
            if radial_min == 0.0
            else (
                radial_min + (radial_max - radial_min) * 0.35,
                radial_min + (radial_max - radial_min) * 0.70,
            )
        )
        for radial_index, radius in enumerate(radial_values):
            for angle_index, angle in _angles(8):
                x = radius * math.cos(angle)
                if element_id in {"plane.station-20", "plane.station-50"} and (
                    x > datum_x - 0.001
                ):
                    continue
                samples.append(
                    FixtureSample(
                        element_id=element_id,
                        key=f"r{radial_index:02d}.a{angle_index:02d}",
                        normal_model=(0.0, 0.0, normal_z),
                        point_model_m=(x, radius * math.sin(angle), z),
                        role=_plane_role(angle_index),
                    )
                )

    half_width = math.sqrt(nominal["r2"] ** 2 - datum_x**2)
    datum_z_start = round(stations[1] + 0.001, 12)
    datum_z_span = round(stations[2] - stations[1] - 0.002, 12)
    for z_index in range(5):
        z = datum_z_start + (datum_z_span * z_index / 4)
        for y_index in range(5):
            y = -half_width + 0.001 + ((2.0 * (half_width - 0.001)) * y_index / 4)
            samples.append(
                FixtureSample(
                    element_id="plane.datum-flat",
                    key=f"z{z_index:02d}.y{y_index:02d}",
                    normal_model=(1.0, 0.0, 0.0),
                    point_model_m=(datum_x, y, z),
                    role=("held-out" if z_index == 2 or y_index == 2 else "training"),
                )
            )
    return _canonical_samples(declaration, samples)


def _tube_samples(declaration: ModelDeclaration) -> tuple[FixtureSample, ...]:
    nominal = _nominal_by_parameter_id(declaration)
    bore_radius = nominal["bore-radius"]
    outside_radius = nominal["outside-radius"]
    axial_length = nominal["axial-length"]
    samples: list[FixtureSample] = []

    axial_guard_m = 0.003
    wall_specs = (
        ("wall.outer", outside_radius, 1.0),
        ("wall.bore", bore_radius, -1.0),
    )
    for element_id, radius, radial_orientation in wall_specs:
        for z_index in range(5):
            z = axial_guard_m + ((axial_length - 2.0 * axial_guard_m) * z_index / 4)
            for angle_index, angle in _angles(16):
                radial_x = math.cos(angle)
                radial_y = math.sin(angle)
                samples.append(
                    FixtureSample(
                        element_id=element_id,
                        key=f"z{z_index:02d}.a{angle_index:02d}",
                        normal_model=(
                            radial_orientation * radial_x,
                            radial_orientation * radial_y,
                            0.0,
                        ),
                        point_model_m=(radius * radial_x, radius * radial_y, z),
                        role=_cylinder_role(z_index, angle_index),
                    )
                )

    wall_thickness = outside_radius - bore_radius
    radial_values = (
        bore_radius + 0.35 * wall_thickness,
        bore_radius + 0.70 * wall_thickness,
    )
    for element_id, z, normal_z in (
        ("end.near", 0.0, -1.0),
        ("end.far", axial_length, 1.0),
    ):
        for radial_index, radius in enumerate(radial_values):
            for angle_index, angle in _angles(8):
                samples.append(
                    FixtureSample(
                        element_id=element_id,
                        key=f"r{radial_index:02d}.a{angle_index:02d}",
                        normal_model=(0.0, 0.0, normal_z),
                        point_model_m=(
                            radius * math.cos(angle),
                            radius * math.sin(angle),
                            z,
                        ),
                        role=_plane_role(angle_index),
                    )
                )
    return _canonical_samples(declaration, samples)


def _stepped_fixture_definition() -> FixtureDefinition:
    declaration = stepped_model_declaration("asymmetric-datum-flat")
    return FixtureDefinition(
        declaration=declaration,
        fixture_id="asymmetric-stepped-v1",
        source_frame="asymmetric-stepped-v1-synthetic-observation-frame",
        transform=RigidTransform(
            rotation=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            translation_m=(0.0, 0.0, 0.0),
        ),
        samples=_stepped_samples(declaration),
    )


def _tube_fixture_definition() -> FixtureDefinition:
    declaration = tube_model_declaration()
    return FixtureDefinition(
        declaration=declaration,
        fixture_id="coaxial-tube-v1",
        source_frame="coaxial-tube-v1-synthetic-observation-frame",
        transform=RigidTransform(
            rotation=((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)),
            translation_m=(0.031, -0.017, 0.009),
        ),
        samples=_tube_samples(declaration),
    )


def fixture_definition(fixture_id: str) -> FixtureDefinition:
    if fixture_id == "asymmetric-stepped-v1":
        return _stepped_fixture_definition()
    if fixture_id == "coaxial-tube-v1":
        return _tube_fixture_definition()
    raise ScansorError(f"unsupported declared synthetic fixture ID {fixture_id!r}")
