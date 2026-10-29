"""Disposable native UI fixture with one bounded, private preview gate."""

# Test-only source adoption leaves production constructors and routes unchanged.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import io
import json
import signal
from threading import Event, RLock, Thread
from types import FrameType
from typing import Any, override
from unittest.mock import patch

import experiments.seed_surface_authoring as authoring
from experiments.feature_graph import Recipe
from experiments.nozzle_browser import Handler, NozzleServer
from experiments.scan_project import ScanProject
from experiments.scan_workspace import ScanLimits
from experiments.seed_surface_authoring import SurfaceSelectionPreviewRequest
from experiments.seed_surface_selection import (
    SurfaceSelectionResult,
    grow_surface_selection,
)
from tests.native_scan_fixture import native_grid_fixture

LIMITS = ScanLimits(2_000_000, 10_000, 20_000, 100_000_000)


class PreviewGate:
    def __init__(self) -> None:
        self.lock: RLock = RLock()
        self.release: Event = Event()
        self.entered: Event = Event()
        self.hold_next: bool = False
        self.requests: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {"entered": self.entered.is_set(), "requests": list(self.requests)}

    def arm(self) -> None:
        with self.lock:
            self.hold_next = True
            self.release.clear()
            self.entered.clear()
            self.requests.clear()

    def grow(self, *args: Any, **kwargs: Any) -> SurfaceSelectionResult:
        with self.lock:
            held = self.hold_next
            self.hold_next = False
        if held:
            self.entered.set()
            if not self.release.wait(20):
                raise TimeoutError("Native private preview gate exceeded 20 seconds")
        return grow_surface_selection(*args, **kwargs)


def initial_project() -> ScanProject:
    project = ScanProject()
    empty = project.accepted
    accepted = project.commit(
        project.prepare_add(
            io.BytesIO(native_grid_fixture(9, 9).raw),
            "preview-grid.ply",
            LIMITS,
            generation=0,
            token=str(empty.graph.snapshot()["token"]),
        )
    )
    recipe = Recipe.model_validate(accepted.graph.export_recipe())
    payload = recipe.model_dump(mode="json")
    payload["nodes"].extend(
        [
            {
                "id": "seeds",
                "label": "Painted seeds",
                "operation": "selection",
                "source": "scan",
                "ids": [40],
            },
            {
                "id": "baseline-input",
                "label": "Baseline observations",
                "operation": "selection",
                "source": "scan",
                "ids": list(range(81)),
            },
            {
                "id": "surface",
                "label": "Seed surface",
                "operation": "surface_selection",
                "seed_selection": "seeds",
            },
            {
                "id": "baseline",
                "label": "Retained baseline plane",
                "operation": "fit",
                "kind": "plane",
                "selections": ["baseline-input"],
            },
        ]
    )
    payload["output"] = "baseline"
    changed = accepted.graph.replace(
        Recipe.model_validate(payload), str(accepted.graph.snapshot()["token"])
    )
    _ = accepted.graph.ensure_current(str(changed["token"]), all_actions=True)
    return project


def main() -> None:
    gate, stopping = PreviewGate(), Event()

    class GatedHandler(Handler):
        @override
        def do_GET(self) -> None:
            if self.path == "/__test/surface-preview":
                self.json_reply(200, gate.status())
            else:
                super().do_GET()

        @override
        def do_POST(self) -> None:
            if self.path != "/__test/surface-preview":
                super().do_POST()
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload.get("action") == "release":
                gate.release.set()
            elif payload.get("action") == "arm" and not self.app.busy():
                gate.arm()
            else:
                self.json_reply(409, {"error": "Unknown action or active preview"})
                return
            self.json_reply(200, gate.status())

    class PreviewServer(NozzleServer):
        @override
        def start_surface_preview(
            self, request: SurfaceSelectionPreviewRequest
        ) -> dict[str, Any]:
            with gate.lock:
                gate.requests.append(request.model_dump(mode="json"))
            return super().start_surface_preview(request)

    with (
        PreviewServer() as server,
        patch.object(authoring, "grow_surface_selection", gate.grow),
    ):
        server._project = initial_project()
        server.RequestHandlerClass = GatedHandler

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
            gate.release.set()
            server.shutdown()
            serving.join(10)
            if serving.is_alive():
                raise RuntimeError("Native surface preview HTTP thread did not retire")


if __name__ == "__main__":
    main()
