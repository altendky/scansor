"""Disposable native UI fixture; gates never exist in the production Handler."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
import signal
from pathlib import Path
from threading import Event, Thread
from types import FrameType
from typing import Any, override

from experiments.feature_recipe_import import load_recipe
from experiments.nozzle_browser import Handler, NozzleServer
from experiments.nozzle_session import NozzleWorkspace
from experiments.scan_workspace import check_cancelled


class Gate:
    def __init__(self) -> None:
        self.mode: str = "idle"
        self.entered: Event = Event()
        self.release: Event = Event()

    def wait(self, cancelled: Any = None) -> None:
        self.entered.set()
        for _ in range(2000):
            if self.release.wait(0.01):
                return
            if self.mode == "cooperative":
                check_cancelled(cancelled)
        raise TimeoutError("Native UI fixture gate exceeded its bounded lifetime")


def main() -> None:
    gate, stopping = Gate(), Event()
    example = Path("examples/nozzle-bayonette-simplified")
    workspace = NozzleWorkspace(example)

    class GatedHandler(Handler):
        @override
        def do_GET(self) -> None:
            if self.path == "/__test/cancellation":
                self.json_reply(
                    200, {"mode": gate.mode, "entered": gate.entered.is_set()}
                )
            else:
                super().do_GET()

        @override
        def do_POST(self) -> None:
            if self.path != "/__test/cancellation":
                super().do_POST()
                return
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            mode = data["mode"]
            if mode == "release":
                gate.release.set()
            elif mode in {"cooperative", "noncooperative", "queued"}:
                if self.app.busy():
                    self.json_reply(409, {"error": "Wait for current work"})
                    return
                gate.mode = mode
                gate.entered.clear()
                gate.release.clear()
                if mode == "queued":
                    _ = self.app.worker.submit(gate.wait)
            else:
                self.json_reply(422, {"error": "Unknown fixture gate"})
                return
            self.json_reply(200, {"mode": gate.mode})

    with NozzleServer(
        workspace,
        recipe=load_recipe(example / "recipes/cone-plane.json"),
        example_path=example,
    ) as server:
        server.RequestHandlerClass = GatedHandler
        original = server.graph._evaluate_impl

        def gated(*args: Any, **kwargs: Any) -> dict[str, object]:
            if gate.mode in {"cooperative", "noncooperative"}:
                gate.wait(kwargs.get("cancelled"))
            return original(*args, **kwargs)

        server.graph._evaluate_impl = gated

        def stop(_signum: int, _frame: FrameType | None) -> None:
            stopping.set()

        for signum in (signal.SIGTERM, signal.SIGINT):
            _ = signal.signal(signum, stop)
        serving = Thread(target=server.serve_forever, daemon=True)
        serving.start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            _ = stopping.wait()
        finally:
            gate.release.set()
            server.shutdown()
            serving.join(timeout=10)
            if serving.is_alive():
                raise RuntimeError("Native UI fixture HTTP thread did not retire")


if __name__ == "__main__":
    main()
