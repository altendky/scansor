"""Small independently generated tapered flange for real CAD display UI tests."""

from __future__ import annotations

import argparse
import base64
import json
import signal
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Event, RLock, Thread
from types import FrameType
from typing import Any, cast, override
from unittest.mock import patch

import numpy as np

from tests.direction_ui_fixture import direction_ui_fixture


def cad_display_fixture() -> dict[str, Any]:
    """Reuse triangle topology; prescribe exact source geometry without fitting."""
    fixture = direction_ui_fixture()
    raw = bytearray(base64.b64decode(fixture["raw_base64"]))
    header = raw.index(b"end_header\n") + len(b"end_header\n")
    rows = np.frombuffer(
        raw, dtype="<f4", count=fixture["vertices"] * 6, offset=header
    ).reshape(-1, 6)
    height, theta = np.meshgrid(
        np.linspace(-2.0, 0.0, 7),
        np.linspace(0.0, 2 * np.pi, 32, endpoint=False),
        indexing="ij",
    )
    radial = np.stack((np.cos(theta), np.sin(theta), np.zeros_like(theta)), axis=-1)
    for name, center, radius, slope in (
        ("axis-cylinder", [-2, 0, 0], 1.0, 0.0),
        ("plane-cylinder", [2, 0, 0], 0.75, 0.0),
        ("joint-cylinder", [0, 0, 0], 5.0, 0.15),
    ):
        positions = np.asarray(center) + radial * (radius + slope * height)[..., None]
        positions[..., 2] = height
        normals = radial.copy()
        normals[..., 2] = -slope
        normals /= np.linalg.norm(normals, axis=-1)[..., None]
        ids = fixture["ids"][name]
        rows[ids, :3], rows[ids, 3:] = (
            positions.reshape(-1, 3),
            normals.reshape(-1, 3),
        )
    x, y = np.meshgrid(np.linspace(-5, 5, 9), np.linspace(-5, 5, 7), indexing="ij")
    rows[fixture["ids"]["joint-plane"], :3] = np.stack(
        (x, y, np.zeros_like(x)), axis=-1
    ).reshape(-1, 3)
    rows[fixture["ids"]["joint-plane"], 3:] = [0, 0, 1]
    recipe = fixture["recipe"]
    recipe["nodes"] = [
        node
        for node in recipe["nodes"]
        if node["operation"] == "source"
        or node["id"]
        in {
            name + suffix
            for name in ("axis-cylinder", "plane-cylinder", "joint-cylinder")
            for suffix in ("", "-selection")
        }
    ]
    outer = next(node for node in recipe["nodes"] if node["id"] == "joint-cylinder")
    outer.update(kind="cone", label="Tapered outer surface")
    for name, offset in (("flange-top", 0), ("flange-bottom", -2)):
        recipe["nodes"].append(
            {
                "id": name,
                "label": name.replace("-", " ").title(),
                "operation": "reference_plane",
                "construction": "independent",
                "initial_normal": [0, 0, 1],
                "offset": offset,
            }
        )
    surfaces = ["axis-cylinder", "plane-cylinder", "joint-cylinder"]
    for cap in ("flange-top", "flange-bottom"):
        boundaries: list[dict[str, str]] = []
        for surface in surfaces:
            edge = cap + "-" + surface
            recipe["nodes"].append(
                {
                    "id": edge,
                    "label": edge,
                    "operation": "surface_intersection",
                    "first": {"feature": cap},
                    "second": {"feature": surface},
                }
            )
            boundaries.append(
                {
                    "intersection": edge,
                    "keep": "inside" if surface == "joint-cylinder" else "outside",
                }
            )
        recipe["nodes"].append(
            {
                "id": cap + "-face",
                "label": cap + " with two holes",
                "operation": "trimmed_face",
                "surface": {"feature": cap},
                "boundaries": boundaries,
            }
        )
    for surface in surfaces:
        recipe["nodes"].append(
            {
                "id": surface + "-wall",
                "label": surface + " wall",
                "operation": "trimmed_face",
                "surface": {"feature": surface},
                "boundaries": [
                    {"intersection": "flange-top-" + surface, "keep": "negative"},
                    {"intersection": "flange-bottom-" + surface, "keep": "positive"},
                ],
            }
        )
    recipe["nodes"].append(
        {
            "id": "flange-body",
            "label": "Tapered flange with two holes",
            "operation": "body",
            "faces": [
                "flange-top-face",
                "flange-bottom-face",
                *[surface + "-wall" for surface in surfaces],
            ],
            "sewing_tolerance": 1e-6,
        }
    )
    recipe["output"] = "flange-body"
    return {
        "raw_base64": base64.b64encode(raw).decode(),
        "filename": "cad-display-flange.ply",
        "vertices": fixture["vertices"],
        "recipe": recipe,
        "truth": {"top_radius": 5, "bottom_radius": 4.7, "height": 2},
    }


def inspect_export(data: bytes) -> dict[str, Any]:
    """Read the actual exported STEP independently of display triangles."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    from tests.test_nozzle_cad import bounds, roundtrip

    with tempfile.TemporaryDirectory(prefix="scansor-cad-display-step-") as temporary:
        metadata, shapes, _mesh, _step = roundtrip(data, Path(temporary))
        if len(shapes) != 1:
            raise ValueError("The flange export must contain one named solid")
        shape = next(iter(shapes.values()))
        properties = GProp_GProps()
        cast(Any, BRepGProp).VolumeProperties_s(shape, properties)
        counts = {}
        for label, kind in (("faces", TopAbs_FACE), ("solids", TopAbs_SOLID)):
            explorer, count = TopExp_Explorer(shape, kind), 0
            while explorer.More():
                count += 1
                explorer.Next()
            counts[label] = count
        return {
            "metadata": metadata,
            "bounds": bounds(shape).tolist(),
            "volume": properties.Mass(),
            **counts,
        }


def serve() -> None:
    """Private port0 fixture; a gate delays the real native meshing callback."""
    import experiments.nozzle_browser as browser
    from experiments.display_geometry import (
        DisplayRefinementRequest,
        prepare_display_refinement,
    )
    from experiments.feature_graph import FeatureGraph

    gate_lock, release, entered, stopping = RLock(), Event(), Event(), Event()
    armed = False
    requests: list[dict[str, Any]] = []
    original = prepare_display_refinement

    def status() -> dict[str, Any]:
        with gate_lock:
            return {
                "entered": entered.is_set(),
                "released": release.is_set(),
                "requests": list(requests),
            }

    def prepare(
        graph: FeatureGraph, request: DisplayRefinementRequest
    ) -> Callable[[Callable[[], bool] | None], dict[str, Any]]:
        nonlocal armed
        callback = original(graph, request)
        with gate_lock:
            requests.append(request.model_dump(mode="json"))
            gated = armed
            armed = False

        def run(cancelled: Callable[[], bool] | None) -> dict[str, Any]:
            if gated:
                entered.set()
                if not release.wait(30):
                    raise TimeoutError("CAD display test gate was not released")
            return callback(cancelled)

        return run

    class Handler(browser.Handler):
        @override
        def do_GET(self) -> None:
            if self.path == "/__test/cad-display":
                self.json_reply(200, {**status(), "busy": self.app.busy()})
            else:
                super().do_GET()

        @override
        def do_POST(self) -> None:
            nonlocal armed
            if self.path != "/__test/cad-display":
                super().do_POST()
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with gate_lock:
                if payload.get("action") == "arm" and not self.app.busy():
                    release.clear()
                    entered.clear()
                    armed = True
                elif payload.get("action") == "release":
                    release.set()
                else:
                    self.json_reply(409, {"error": "Unknown action or active preview"})
                    return
            self.json_reply(200, status())

    with (
        browser.NozzleServer() as server,
        patch.object(browser, "prepare_display_refinement", prepare),
    ):
        server.RequestHandlerClass = Handler

        def stop(_signum: int, _frame: FrameType | None) -> None:
            stopping.set()

        for signum in (signal.SIGINT, signal.SIGTERM):
            _ = signal.signal(signum, stop)
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            _ = stopping.wait()
        finally:
            release.set()
            server.shutdown()
            serving.join(10)
            if serving.is_alive():
                raise RuntimeError("Native CAD display fixture did not retire")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--serve", action="store_true")
    _ = parser.add_argument("--inspect-export", action="store_true")
    arguments = parser.parse_args()
    if arguments.serve:
        serve()
    elif arguments.inspect_export:
        print(json.dumps(inspect_export(sys.stdin.buffer.read())))
    else:
        print(json.dumps(cad_display_fixture()))
