"""Real subprocess failure fixtures for the mesh supervisor; never production modes."""

from __future__ import annotations

import os
import signal
import struct
import sys
from pathlib import Path
from typing import BinaryIO, cast


def main() -> int:
    mode = sys.argv.pop(1)
    access = Path(f"/proc/self/fd/{int(sys.argv[1])}")
    if mode in ("crash-before", "ignore-term", "exit-zero"):
        _ = (access / "work" / "partial.bin").write_bytes(b"owned unfinished work")
    if mode == "crash-before":
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == "exit-zero":
        os._exit(0)
    if mode == "oversized-frame":
        _ = os.write(1, struct.pack(">I", 8 * 1024 * 1024 + 1))
        _ = signal.pause()
    if mode == "truncated-frame":
        _ = os.write(1, struct.pack(">I", 1000) + b"{")
        os._exit(17)
    if mode == "ignore-term":
        _ = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    from scansor.mesh_controls import control_id, decode_control
    from scansor.mesh_worker_protocol import FrameWriter
    from scansor.mesh_worker_request import WorkerRequest

    writer = FrameWriter(cast(BinaryIO, sys.stdout.buffer))
    if mode in ("ignore-term", "wrong-start"):
        request = WorkerRequest.from_record(
            decode_control((access / "request.json").read_bytes())
        )
        writer.send(
            {
                "type": "started",
                "pid": os.getpid(),
                "request_id": control_id(request.record())
                if mode == "ignore-term"
                else "f" * 64,
            }
        )
        _ = signal.pause()
    from scansor import mesh_worker

    if mode == "display-cleanup-failure":
        from scansor import mesh_display

        def fail_display_cleanup(_path: Path, _identity: tuple[int, int]) -> None:
            raise RuntimeError("injected display cleanup failure after publication")

        mesh_display.remove_owned_workspace = fail_display_cleanup  # pyright: ignore[reportPrivateLocalImportUsage]

    if mode == "crash-after-display":
        from scansor.mesh_controls import Control

        send = FrameWriter.send

        def crash_after_display(self: FrameWriter, record: dict[str, Control]) -> None:
            send(self, record)
            if record.get("type") == "published":
                stage = record.get("stage")
                if isinstance(stage, dict) and stage.get("kind") == "display":
                    os.kill(os.getpid(), signal.SIGKILL)

        FrameWriter.send = crash_after_display

    if mode == "crash-copy":
        from scansor import mesh_publication_copy

        def crash_copy(
            _source: int, target: int, _expected: object, _check: object
        ) -> None:
            _ = (Path(f"/proc/self/fd/{target}") / "partial").write_bytes(
                b"owned partial copy"
            )
            os.kill(os.getpid(), signal.SIGKILL)

        mesh_publication_copy.copy_stage_files = crash_copy

    if mode == "crash-after-import":

        def crash(*_args: object, **_kwargs: object) -> None:
            os.kill(os.getpid(), signal.SIGKILL)

        mesh_worker.publish_contributions = crash  # pyright: ignore[reportPrivateLocalImportUsage]
    if mode == "noisy-stderr":
        raw = b"x" * 100_000
        done = 0
        while done < len(raw):
            done += os.write(2, raw[done:])
    result = mesh_worker.main()
    if mode == "crash-after-result":
        os._exit(17)
    if mode == "double-result":
        writer.send({"type": "result", "status": "complete"})
    return result


if __name__ == "__main__":
    raise SystemExit(main())
