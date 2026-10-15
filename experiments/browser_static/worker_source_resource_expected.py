"""Independent source/display digests from the exact packaged source modules."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, cast


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_relative(value: Any) -> str:
    require(
        isinstance(value, str)
        and bool(value)
        and not value.startswith("/")
        and not any(character in value for character in ("\\", "\0", ":"))
        and all(part not in {"", ".", ".."} for part in value.split("/")),
        "unsafe packaged source path",
    )
    return cast(str, value)


def regular_bytes(root: Path, relative: str) -> bytes:
    current = root
    for part in safe_relative(relative).split("/"):
        current = current / part
        require(not current.is_symlink(), "symlink in trusted source path")
    require(current.is_file(), "trusted source is not a regular file")
    return current.read_bytes()


def validated_source_members(
    runtime: Path, manifest: dict[str, Any], repository: Path
) -> dict[str, bytes]:
    """Verify complete ZIP bytes against manifest AND trusted checkout, pre-import.

    Source records do not contain sizes. The matching trusted checkout supplies
    exact expanded sizes before decompression, rather than trusting ZIP headers.
    All members pass before callers create an extraction directory or import.
    """
    records = manifest.get("source_files")
    require(
        isinstance(records, list) and bool(records), "missing packaged source records"
    )
    records = cast(list[Any], records)
    expected: dict[str, tuple[int, str]] = {}
    paths: set[str] = set()
    for item in records:
        require(isinstance(item, dict), "invalid packaged source record")
        record = cast(dict[str, Any], item)
        name = safe_relative(record.get("archive_path"))
        source = safe_relative(record.get("path"))
        require(
            name not in expected and source not in paths,
            "duplicate packaged source record",
        )
        paths.add(source)
        trusted = regular_bytes(repository, source)
        digest = hashlib.sha256(trusted).hexdigest()
        require(
            record.get("sha256") == digest,
            "packaged source record differs from checkout",
        )
        expected[name] = (len(trusted), digest)
    assets = [item for item in manifest["assets"] if item.get("path") == "scansor.zip"]
    require(len(assets) == 1, "missing or duplicate packaged source ZIP asset")
    raw = regular_bytes(runtime, "scansor.zip")
    require(
        type(assets[0].get("bytes")) is int
        and assets[0]["bytes"] == len(raw)
        and assets[0].get("sha256") == hashlib.sha256(raw).hexdigest(),
        "packaged source ZIP identity differs",
    )
    result: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        names = [item.filename for item in members]
        require(
            len(names) == len(set(names)) == len(expected)
            and set(names) == set(expected),
            "packaged source closure differs",
        )
        require(
            sum(item.file_size for item in members)
            == sum(size for size, _ in expected.values()),
            "packaged source expanded size differs",
        )
        for item in members:
            name = safe_relative(item.filename)
            require(
                name == item.orig_filename
                and not item.is_dir()
                and stat.S_IFMT(item.external_attr >> 16) in {0, stat.S_IFREG}
                and not item.flag_bits & 0x2041
                and item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED},
                "unsafe packaged source member type",
            )
            size, digest = expected[name]
            require(item.file_size == size, "packaged source member size differs")
            data = archive.read(item)
            require(
                len(data) == size and hashlib.sha256(data).hexdigest() == digest,
                "packaged source member differs",
            )
            result[name] = data
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--runtime", type=Path, required=True)
    _ = parser.add_argument("--inputs", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.runtime / "manifest.json").read_text())
    inputs = json.loads((args.inputs / "manifest.json").read_text())
    members = validated_source_members(
        args.runtime, manifest, Path(__file__).resolve().parents[2]
    )
    with tempfile.TemporaryDirectory(dir=args.output.parent) as temporary:
        root = Path(temporary)
        for name, data in members.items():
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            _ = destination.write_bytes(data)
        sys.path.insert(0, str(root))
        from experiments.scan_workspace import ScanLimits, prepare_scan

        origin = sys.modules[prepare_scan.__module__].__file__
        if origin is None or not Path(origin).is_relative_to(root):
            raise ValueError("Reference did not import the packaged source")
        limits = ScanLimits(**manifest["worker_protocol"]["source_import"]["limits"])
        results: dict[str, Any] = {}
        for item in inputs["cases"]:
            if item["name"] not in {"raw-nozzle", "horizontal-scene", "grid-512x256"}:
                continue
            name = item["name"]
            raw = (args.inputs / f"{name}.ply").read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("Frozen input changed")
            workspace = cast(Any, prepare_scan(io.BytesIO(raw), f"{name}.ply", limits))
            positions, indices = workspace.display_buffers()
            arrays = {
                "xyz": workspace.data.xyz,
                "original_normals": workspace.data.original_normals,
                "triangles": workspace.data.triangles,
                "normals": workspace.data.normals,
                "weights": workspace.data.weights,
                "local": workspace.local,
            }
            results[name] = {
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "descriptor": workspace.source_descriptor.model_dump(),
                "source_identity": {
                    "descriptor": workspace.source_descriptor.semantic_record(),
                    "reference_sha256": workspace.reference_sha256,
                },
                "observations": {
                    key: {
                        "dtype": str(value.dtype),
                        "shape": list(value.shape),
                        "sha256": hashlib.sha256(value.tobytes()).hexdigest(),
                    }
                    for key, value in arrays.items()
                },
                "display": {
                    key: {
                        "bytes": value.nbytes,
                        "sha256": hashlib.sha256(value.tobytes()).hexdigest(),
                    }
                    for key, value in {
                        "positions": positions,
                        "indices": indices,
                    }.items()
                },
            }
        _ = args.output.write_text(json.dumps(results, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()
