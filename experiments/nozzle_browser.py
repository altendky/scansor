"""Local HTTP adapter for the nozzle selection experiment (not a product server)."""

from __future__ import annotations

import argparse
import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar, override

from pydantic import ValidationError

from experiments.nozzle_session import NozzleSession, NozzleWorkspace, SessionFit

ASSETS = Path(__file__).with_name("browser_viewer")


class NozzleServer(ThreadingHTTPServer):
    """One bounded fit worker; HTTP remains available during fitting."""

    def __init__(self, workspace: NozzleWorkspace, port: int = 0) -> None:
        self.workspace: NozzleWorkspace = workspace
        self.worker: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1)
        self.lock: threading.Lock = threading.Lock()
        self.job: Future[SessionFit] | None = None
        self.job_id: str = ""
        self.buffers: dict[str, bytes] = {
            "/mesh/positions": workspace.local.astype("<f4").tobytes(),
            "/mesh/indices": workspace.data.triangles.astype("<u4").tobytes(),
        }
        super().__init__(("127.0.0.1", port), Handler)

    @override
    def server_close(self) -> None:
        super().server_close()
        self.worker.shutdown(wait=True, cancel_futures=True)


class Handler(BaseHTTPRequestHandler):
    @property
    def app(self) -> NozzleServer:
        assert isinstance(self.server, NozzleServer)
        return self.server

    files: ClassVar[dict[str, tuple[str, str]]] = {
        "/navigation-math.js": ("navigation-math.js", "text/javascript"),
        "/navigation.js": ("navigation.js", "text/javascript"),
        "/vendor/TrackballControls.js": (
            "node_modules/three/examples/jsm/controls/TrackballControls.js",
            "text/javascript",
        ),
        "/": ("index.html", "text/html"),
        "/app.js": ("app.js", "text/javascript"),
        "/selection.js": ("selection.js", "text/javascript"),
        "/style.css": ("style.css", "text/css"),
        "/vendor/three.module.js": (
            "node_modules/three/build/three.module.js",
            "text/javascript",
        ),
        "/vendor/three.core.js": (
            "node_modules/three/build/three.core.js",
            "text/javascript",
        ),
        "/vendor/OrbitControls.js": (
            "node_modules/three/examples/jsm/controls/OrbitControls.js",
            "text/javascript",
        ),
        "/vendor/LICENSE": ("node_modules/three/LICENSE", "text/plain"),
    }

    def reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        _ = self.wfile.write(body)

    def json_reply(self, status: int, value: object) -> None:
        self.reply(
            status, json.dumps(value, allow_nan=False).encode(), "application/json"
        )

    def allowed(self) -> bool:
        # Reject cross-origin browser requests and DNS rebinding; native clients
        # can omit Origin, but mutations still require the explicit JSON header.
        host = f"127.0.0.1:{self.app.server_port}"
        if (
            self.headers.get("Host") != host
            or self.headers.get("Origin", f"http://{host}") != f"http://{host}"
        ):
            self.json_reply(403, {"error": "use the printed local URL"})
            return False
        return True

    def do_GET(self) -> None:
        if not self.allowed():
            return
        if self.path == "/api/meta":
            self.json_reply(200, self.app.workspace.metadata())
        elif self.path.startswith("/api/fit/"):
            with self.app.lock:
                job, job_id = self.app.job, self.app.job_id
            if job is None or self.path != f"/api/fit/{job_id}":
                self.json_reply(404, {"error": "fit no longer available; run it again"})
            elif not job.done():
                self.json_reply(200, {"status": "running"})
            else:
                try:
                    self.json_reply(200, {"status": "complete", "result": job.result()})
                except Exception as error:  # A failed fit is a visible job outcome.
                    self.json_reply(200, {"status": "failed", "error": str(error)})
        elif self.path in self.app.buffers:
            self.reply(200, self.app.buffers[self.path], "application/octet-stream")
        elif self.path in self.files:
            filename, mime = self.files[self.path]
            try:
                self.reply(200, (ASSETS / filename).read_bytes(), mime)
            except FileNotFoundError:
                self.json_reply(
                    404,
                    {
                        "error": "missing asset; run npm ci in experiments/browser_viewer"
                    },
                )
        else:
            self.json_reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.allowed():
            return
        if self.path not in ("/api/session", "/api/fit"):
            self.json_reply(404, {"error": "not found"})
            return
        if (
            self.headers.get("X-Scansor-Request") != "1"
            or self.headers.get("Content-Type") != "application/json"
        ):
            self.json_reply(403, {"error": "expected explicit JSON request"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1_000_000:
                raise ValueError("expected a session under 1 MB")
            self.connection.settimeout(5)
            session = self.app.workspace.validate(
                NozzleSession.model_validate_json(self.rfile.read(length))
            )
            if self.path == "/api/session":
                self.json_reply(200, session.model_dump())
                return
            with self.app.lock:
                if self.app.job is not None and not self.app.job.done():
                    self.json_reply(409, {"error": "a fit is already running"})
                    return
                self.app.job_id = uuid.uuid4().hex
                self.app.job = self.app.worker.submit(self.app.workspace.fit, session)
                job_id = self.app.job_id
            self.json_reply(202, {"job_id": job_id})
        except (ValueError, ValidationError, TimeoutError) as error:
            self.json_reply(422, {"error": str(error)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--example", type=Path, default=Path("examples/nozzle-bayonette-simplified")
    )
    _ = parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if not (ASSETS / "node_modules/three/build/three.module.js").is_file():
        parser.error(
            "run npm ci --ignore-scripts --prefix experiments/browser_viewer first"
        )
    with NozzleServer(NozzleWorkspace(args.example), args.port) as server:
        print(
            f"Nozzle selection experiment: http://127.0.0.1:{server.server_port}/",
            flush=True,
        )
        with suppress(KeyboardInterrupt):
            server.serve_forever()


if __name__ == "__main__":
    main()
