"""S3 storage/association evidence in one fresh worker; run workers sequentially.

This deliberately stops before S4 accounting/publication and is not a scale gate.
The caller supplies an existing work directory outside Git and an output path.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np

from scansor.mesh_controls import Control, control_id, encode_control
from scansor.mesh_dispositions import face_dispositions
from scansor.mesh_import import prepare_import
from scansor.mesh_recipes import GridRecipe, generate_file
from scansor.mesh_resources import MIB, memory_snapshot


def run(options: Any) -> dict[str, Control]:
    recipe = GridRecipe(options.width, options.height, noise_bits=3)
    events: list[dict[str, Control]] = []
    indices_digest, corners_digest = hashlib.sha256(), hashlib.sha256()
    # Keep telemetry bounded even if an execution unexpectedly runs for hours.
    event_count = 0

    def progress(event: dict[str, Control]) -> None:
        nonlocal event_count
        event_count += 1
        if len(events) == 256:
            del events[0]
        events.append(event)

    with tempfile.TemporaryDirectory(
        prefix="scansor-s3-probe-", dir=options.workdir
    ) as name:
        directory = Path(name)
        source = generate_file(directory, "source.ply", recipe, chunk_rows=4093)
        with prepare_import(
            source,
            directory,
            budget_bytes=options.budget_mib * MIB,
            storage=options.storage,
            chunk_rows=options.chunk_rows,
            progress=progress,
        ) as foundation:
            inventory = foundation.inventory()
            seen = 0
            for batch in foundation.staging.associated_faces(
                xyz=foundation.columns["xyz.bin"],
                triangles=foundation.columns["triangles.bin"],
            ):
                assert batch.start == seen
                expected = recipe.faces(batch.start, batch.start + len(batch.indices))
                assert np.array_equal(batch.indices, expected)
                status, areas = face_dispositions(
                    batch.indices, batch.corners, vertices=foundation.vertices
                )
                assert not np.any(status) and np.all(areas > 0)
                indices_digest.update(memoryview(batch.indices).cast("B"))
                corners_digest.update(memoryview(batch.corners).cast("B"))
                seen += len(batch.indices)
                del batch, expected, status, areas
            assert seen == recipe.face_count
            execution = foundation.execution_report()
        # Include the monitor's final sample after native resources were closed.
        execution["monitor"] = foundation.monitor.record()
        execution["post_cleanup_memory"] = memory_snapshot()
        peak = execution["post_cleanup_memory"]["os_peak_rss_bytes"]
        assert isinstance(peak, int) and peak <= options.budget_mib * MIB
        assert not foundation.directory.exists()
    root = resources.files("scansor")
    execution["source_hashes"] = {
        name: hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
        for name in (
            "mesh_columns.py",
            "mesh_duckdb.py",
            "mesh_import.py",
            "mesh_resources.py",
            "mesh_snapshot.py",
            "mesh_workspace.py",
        )
    }
    execution["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return {
        "revision": "mesh-import-storage-probe-v1",
        "scope": "S3 source columns and complete coordinate association only",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "recipe": recipe.record(),
        "inventory": inventory,
        "inventory_id": control_id(inventory),
        "association": {
            "faces": seen,
            "indices_sha256": indices_digest.hexdigest(),
            "corners_sha256": corners_digest.hexdigest(),
        },
        "execution": execution,
        "progress_event_count": event_count,
        "last_progress_events": list(events),
        "owned_cleanup_complete": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--workdir", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--storage", choices=("ram", "disk"), required=True)
    _ = parser.add_argument("--budget-mib", type=int, required=True)
    _ = parser.add_argument("--chunk-rows", type=int, required=True)
    _ = parser.add_argument("--width", type=int, default=500)
    _ = parser.add_argument("--height", type=int, default=400)
    options = parser.parse_args()
    result = run(options)
    with options.output.open("xb") as stream:
        _ = stream.write(encode_control(result))


if __name__ == "__main__":
    main()
