"""Project harness candidate for experiments/browser_static; project dependencies.

Freeze adapts this project's create_fixture geometry and changes only grid counts.
RPC/measure imports and calls the actual, unchanged production numerical modules.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray

if TYPE_CHECKING:
    from experiments.mesh_cone_plane_fit import ConePlaneFitResult

type Array = NDArray[np.float64]
type Arrays = dict[str, Array]
type FitFunction = Callable[
    [Array, Array, Array, Array, Array, tuple[float, float]], ConePlaneFitResult
]
type ResidualFunction = Callable[
    [Array, Array, Array, tuple[float, float]], tuple[Array, Array]
]

TIERS = ((496, 48, 9, 8, 8), (4096, 256, 14, 32, 16), (16384, 512, 28, 64, 32))
SUPPORT = (-3.0, 6.0)
DATA: dict[int, Arrays] = {}
_fit: FitFunction | None = None
_residual: ResidualFunction | None = None


def solver_import() -> None:
    global _fit, _residual
    if _fit is None:
        from experiments.mesh_cone_plane_fit import (
            cone_plane_residual_jacobian,
            fit_cone_plane,
        )

        _fit, _residual = fit_cone_plane, cone_plane_residual_jacobian


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(ntheta: int, nz: int, nx: int, ny: int) -> Arrays:
    axis = np.array([0.12, -0.08, 1.0])
    axis /= np.linalg.norm(axis)
    u = np.cross(axis, [0.0, 1.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(axis, u)
    theta, z = np.meshgrid(
        np.linspace(0, 1, ntheta) ** 1.4 * 1.6 * np.pi, np.linspace(-1, 1, nz)
    )
    cone = (
        np.array([0.3, -0.4, 0.0])
        + z.ravel()[:, None] * axis
        + (3.2 + 0.08 * z.ravel())[:, None]
        * (np.cos(theta.ravel())[:, None] * u + np.sin(theta.ravel())[:, None] * v)
    )
    x, y = np.meshgrid(np.linspace(-3, 3, nx), np.linspace(-3, 3, ny))
    plane = 4.1 * axis + x.ravel()[:, None] * u + y.ravel()[:, None] * v
    rng = np.random.default_rng(123456)
    cone += rng.normal(0, 0.001, cone.shape)
    plane += rng.normal(0, 0.001, plane.shape)
    return dict(
        cone=cone,
        plane=plane,
        cw=np.linspace(0.1, 2, len(cone)),
        pw=np.linspace(0.2, 1, len(plane)),
        initial=np.array([0.0, 0.0, 0.0, 0.0, 3.0, 4.0, 0.0]),
    )


def deterministic_npz(path: Path, arrays: Arrays) -> None:
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, array in arrays.items():
            buffer = io.BytesIO()
            np.lib.format.write_array(buffer, array, allow_pickle=False)
            record = zipfile.ZipInfo(name + ".npy", date_time=(1980, 1, 1, 0, 0, 0))
            record.compress_type = zipfile.ZIP_STORED
            record.external_attr = 0o600 << 16
            archive.writestr(record, buffer.getvalue())


def load_fixture(
    path: Path | str, tier: int | None = None, expected_sha256: str | None = None
) -> dict[str, Any]:
    solver_import()
    path = Path(path)
    digest = sha256(path.read_bytes())
    if expected_sha256 is not None:
        assert digest == expected_sha256, "Frozen fixture byte hash changed"
    with np.load(path, allow_pickle=False) as saved:
        arrays: Arrays = {name: saved[name] for name in saved.files}
    assert set(arrays) == {"cone", "plane", "cw", "pw", "initial"}
    assert all(a.dtype == np.float64 and a.flags.c_contiguous for a in arrays.values())
    assert arrays["cone"].shape[1:] == arrays["plane"].shape[1:] == (3,)
    assert arrays["cw"].shape == (len(arrays["cone"]),)
    assert arrays["pw"].shape == (len(arrays["plane"]),)
    assert arrays["initial"].shape == (7,)
    count = len(arrays["cone"]) + len(arrays["plane"])
    if tier is not None:
        assert count == tier
    for array in arrays.values():
        array.flags.writeable = False
    DATA[count] = arrays
    return dict(
        tier=count,
        fixture_sha256=digest,
        input_array_bytes=sum(a.nbytes for a in arrays.values()),
        counts={"cone": len(arrays["cone"]), "plane": len(arrays["plane"])},
    )


def measure(tier: int) -> dict[str, Any]:
    solver_import()
    assert _fit is not None and _residual is not None
    d = DATA[tier]
    begin = time.perf_counter()
    result = _fit(d["cone"], d["plane"], d["cw"], d["pw"], d["initial"], SUPPORT)
    fit_seconds = time.perf_counter() - begin
    # Parameter conversion is outside both numerical timing sections.
    parameters = np.asarray(result["parameters"], dtype=np.float64)
    begin = time.perf_counter()
    residual, jacobian = _residual(d["cone"], d["plane"], parameters, SUPPORT)
    residual_jacobian_seconds = time.perf_counter() - begin
    assert residual.shape == (tier,) and jacobian.shape == (tier, 7)
    # JSON construction and transport occur only after the clocks stop.
    return dict(
        tier=tier,
        fit=result,
        residuals=residual.tolist(),
        fit_seconds=fit_seconds,
        residual_jacobian_seconds=residual_jacobian_seconds,
        core_seconds=fit_seconds + residual_jacobian_seconds,
        explicit_output_arrays={
            "residual_bytes": residual.nbytes,
            "jacobian_bytes": jacobian.nbytes,
            "dtype": str(residual.dtype),
            "jacobian_shape": list(jacobian.shape),
        },
        caveat="No intra-call allocation/peak sampling; reported output payload excludes intermediate solver arrays.",
    )


def metadata() -> dict[str, Any]:
    solver_import()
    return dict(
        python=sys.version,
        numpy=np.__version__,
        platform=platform.platform(),
        native_OPENBLAS_NUM_THREADS=os.environ.get("OPENBLAS_NUM_THREADS"),
        native_OMP_NUM_THREADS=os.environ.get("OMP_NUM_THREADS"),
        native_MKL_NUM_THREADS=os.environ.get("MKL_NUM_THREADS"),
        native_PYTHONDONTWRITEBYTECODE=os.environ.get("PYTHONDONTWRITEBYTECODE"),
        native_TMPDIR=os.environ.get("TMPDIR"),
        excluded_loaded=[
            name
            for name in ("OCP", "duckdb", "pyarrow", "psutil")
            if name in sys.modules
        ],
    )


def source_provenance(site: Path, repository: Path) -> dict[str, Any]:
    closure = [
        "experiments/fit_coordinates.py",
        "experiments/fit_solver.py",
        "experiments/mesh_cylinder_fit.py",
        "experiments/mesh_cylinder_plane_fit.py",
        "experiments/mesh_cone_plane_fit.py",
        "src/scansor/nonlinear_least_squares.py",
    ]
    runtimes = list((site / "app/runtime").iterdir())
    if len(runtimes) != 1:
        raise ValueError("Expected one immutable runtime directory")
    runtime = runtimes[0]
    runtime_manifest = json.loads((runtime / "manifest.json").read_text())
    with zipfile.ZipFile(runtime / "scansor.zip") as archive:
        source_records: list[dict[str, Any]] = []
        for name in closure:
            archive_name = name.removeprefix("src/")
            native = (repository / name).read_bytes()
            packaged = archive.read(archive_name)
            assert native == packaged, (
                f"Native and frozen browser implementation differ: {name}"
            )
            source_records.append(
                dict(
                    repository_path=name,
                    archive_path=archive_name,
                    sha256=sha256(native),
                    bytes=len(native),
                )
            )
    return dict(
        source_revision=runtime_manifest["source_revision"],
        worker_protocol=runtime_manifest["worker_protocol"],
        source_closure=source_records,
    )


def freeze(directory: Path, site: Path, repository: Path) -> dict[str, Any]:
    provenance = source_provenance(site, repository)
    directory.mkdir(exist_ok=False)
    generator = repository / "experiments/browser_runtime/probe.py"
    runtime = next((site / "app/runtime").iterdir())
    records: list[dict[str, Any]] = []
    for tier, ntheta, nz, nx, ny in TIERS:
        arrays = fixture(ntheta, nz, nx, ny)
        assert len(arrays["cone"]) + len(arrays["plane"]) == tier
        path = directory / f"fixture-{tier}.npz"
        deterministic_npz(path, arrays)
        original_equal = None
        if tier == 496:
            with np.load(runtime / "fixture.npz", allow_pickle=False) as original:
                original_equal = {
                    k: bool(np.array_equal(a, original[k])) for k, a in arrays.items()
                }
            assert all(original_equal.values()), (
                "Base tier differs from retained original create_fixture arrays"
            )
        records.append(
            dict(
                tier=tier,
                file=path.name,
                fixture_sha256=sha256(path.read_bytes()),
                file_bytes=path.stat().st_size,
                counts={"cone": len(arrays["cone"]), "plane": len(arrays["plane"])},
                grids={"theta": ntheta, "axial": nz, "plane_x": nx, "plane_y": ny},
                array_payload_bytes=sum(a.nbytes for a in arrays.values()),
                arrays={
                    k: dict(
                        dtype=str(a.dtype),
                        shape=list(a.shape),
                        bytes=a.nbytes,
                        sha256=sha256(a.tobytes(order="C")),
                    )
                    for k, a in arrays.items()
                },
                retained_original_array_equal=original_equal,
            )
        )
    manifest = dict(
        kind="component-scaling-fixture",
        schema_version=1,
        status="Deterministic synthetic component probe; no large-source or physical-accuracy claim",
        created_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        generator_reference="experiments/browser_runtime/probe.py:create_fixture",
        generator_reference_sha256=sha256(generator.read_bytes()),
        generator_script_sha256=sha256(Path(__file__).read_bytes()),
        adaptation="Only theta/axial and planar grid counts change per tier; geometry, partial angular coverage, RNG seed/noise, weights, initial and objective unchanged.",
        seed=123456,
        noise_standard_deviation=0.001,
        axial_support=list(SUPPORT),
        frozen_dtype="float64",
        numpy_generator_version=np.__version__,
        **provenance,
        fixtures=records,
    )
    _ = (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def rpc() -> None:
    """Native project-harness command channel; caller owns import paths/processes."""
    assert os.environ.get("OPENBLAS_NUM_THREADS") == "1"
    for line in sys.stdin:
        request: dict[str, Any] = {}
        try:
            parsed: object = json.loads(line)
            if not isinstance(parsed, dict):
                raise ValueError("Native request must be a JSON object")
            request = cast(dict[str, Any], parsed)
            if request["operation"] == "metadata":
                result = metadata()
            elif request["operation"] == "load":
                result = load_fixture(
                    request["path"], request["tier"], request["sha256"]
                )
            elif request["operation"] == "measure":
                result = measure(request["tier"])
            elif request["operation"] == "source":
                result = source_provenance(
                    Path(request["site"]), Path(request["repository"])
                )
            elif request["operation"] == "freeze":
                result = freeze(
                    Path(request["directory"]),
                    Path(request["site"]),
                    Path(request["repository"]),
                )
            elif request["operation"] == "close":
                break
            else:
                raise ValueError("Unknown native operation")
            print(
                json.dumps(
                    {"id": request["id"], "ok": True, "result": result}, allow_nan=False
                ),
                flush=True,
            )
        except Exception as error:
            print(
                json.dumps({"id": request.get("id"), "ok": False, "error": str(error)}),
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("operation", choices=["freeze", "rpc"])
    _ = parser.add_argument("--directory", type=Path)
    _ = parser.add_argument("--site", type=Path)
    _ = parser.add_argument(
        "--repository", type=Path, default=Path(__file__).resolve().parents[2]
    )
    args = parser.parse_args()
    sys.path[:0] = [str(args.repository / "src"), str(args.repository)]
    if args.operation == "freeze":
        if args.directory is None or args.site is None:
            parser.error("freeze requires --directory and --site")
        print(json.dumps(freeze(args.directory, args.site, args.repository)))
    else:
        rpc()


if __name__ == "__main__":
    main()
