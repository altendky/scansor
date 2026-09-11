"""Independent atomic publication of computed mesh stages on Linux.

This writes only the caller's owned staging tree. Verification here binds closed
files to the just-computed records; source replay of an existing artifact is a
separate operation. Directory handles anchor verification and the final rename.
The private-directory trust boundary is the same as mesh_workspace.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scansor.errors import ScansorError
from scansor.files import rename_no_replace
from scansor.mesh_accounting import AccountedImport, CompleteContributions
from scansor.mesh_columns import Column
from scansor.mesh_controls import Control, encode_control
from scansor.mesh_errors import MeshImportError, io_failure

type FileRecord = tuple[int, str]
type Check = Callable[[], None]


@dataclass(frozen=True)
class PublishedStage:
    path: Path
    kind: str
    identity: str


def _metadata(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _write_control(directory: int, name: str, record: dict[str, Control]) -> FileRecord:
    raw = encode_control(record)
    descriptor = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=directory,
    )
    with os.fdopen(descriptor, "wb") as stream:
        view, done = memoryview(raw), 0
        while done < len(view):
            count = stream.write(view[done:])
            if type(count) is not int or not 0 < count <= len(view) - done:
                raise MeshImportError(
                    "execution", "publish-control", "control write made no progress"
                )
            done += count
        stream.flush()
        os.fsync(stream.fileno())
    return len(raw), hashlib.sha256(raw).hexdigest()


def _check_control_child(name: str, actual: FileRecord, expected: Control) -> None:
    record: dict[str, Control] = {
        "name": name,
        "byte_count": actual[0],
        "sha256": actual[1],
    }
    if encode_control(record) != encode_control(expected):
        raise MeshImportError(
            "integrity", "publication", "control child differs from computed inventory"
        )


def _hash_file(directory: int, name: str, expected: FileRecord, check: Check) -> None:
    entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(entry.st_mode):
        raise MeshImportError(
            "integrity", "publish-verify", "stage child is not a regular file"
        )
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if _metadata(before) != _metadata(entry) or before.st_size != expected[0]:
            raise MeshImportError(
                "integrity",
                "publish-verify",
                "stage child changed or has the wrong length",
            )
        digest, seen = hashlib.sha256(), 0
        while part := stream.read(65_536):
            check()
            seen += len(part)
            if seen > expected[0]:
                raise MeshImportError(
                    "integrity", "publish-verify", "stage child grew while hashing"
                )
            digest.update(part)
        if (
            seen != expected[0]
            or digest.hexdigest() != expected[1]
            or _metadata(os.fstat(stream.fileno())) != _metadata(before)
            or _metadata(os.stat(name, dir_fd=directory, follow_symlinks=False))
            != _metadata(before)
        ):
            raise MeshImportError(
                "integrity",
                "publish-verify",
                f"stage child differs from computed inventory: {name}",
            )


def verify_closed_tree(
    directory: int, expected: dict[str, FileRecord], check: Check
) -> None:
    """Fixed names, bounded traversal, no symlinks, complete child byte hashes."""
    names = {name.split("/", 1)[0] for name in expected}
    seen: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            check()
            if entry.name not in names:
                raise MeshImportError(
                    "integrity", "publish-verify", "unexpected stage child"
                )
            seen.add(entry.name)
    if seen != names:
        raise MeshImportError("integrity", "publish-verify", "missing stage child")
    for name in sorted(names):
        children = {
            key.split("/", 1)[1]: record
            for key, record in expected.items()
            if key.startswith(name + "/")
        }
        if children:
            entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
            child = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
            )
            try:
                if _identity(entry) != _identity(os.fstat(child)):
                    raise MeshImportError(
                        "integrity",
                        "publish-verify",
                        "stage directory changed while opening",
                    )
                verify_closed_tree(child, children, check)
                os.fsync(child)
                if _identity(
                    os.stat(name, dir_fd=directory, follow_symlinks=False)
                ) != _identity(entry):
                    raise MeshImportError(
                        "integrity", "publish-verify", "stage child directory changed"
                    )
            finally:
                os.close(child)
        else:
            _hash_file(directory, name, expected[name], check)
    os.fsync(directory)


def _materialize_columns(
    directory: int, columns: dict[str, Column], expected_columns: Control, check: Check
) -> dict[str, FileRecord]:
    actual: list[Control] = []
    files: dict[str, FileRecord] = {}
    access = Path(f"/proc/self/fd/{directory}")
    for name, column in columns.items():
        check()
        record = column.inventory()
        actual.append(record)
        files[name] = column.spec.byte_count, str(record["sha256"])
        if column.path is None:
            disk = Column(
                column.spec, path=access / name, max_range_bytes=column.max_range_bytes
            )
            try:
                chunk = column.max_range_bytes // column.spec.stride
                for start in range(0, column.spec.rows, chunk):
                    check()
                    rows = column.read_range(
                        start, min(start + chunk, column.spec.rows)
                    )
                    disk.write_range(start, rows)
                    del rows
                disk.finish()
                if encode_control(disk.inventory()) != encode_control(record):
                    raise MeshImportError(
                        "integrity",
                        "publish-columns",
                        "RAM column changed during materialization",
                    )
            finally:
                disk.close()
        column.close()
    if encode_control(actual) != encode_control(expected_columns):
        raise MeshImportError(
            "integrity", "publish-columns", "columns differ from computed inventory"
        )
    return files


def _publish(
    stage: Path,
    destination: Path,
    kind: str,
    identity: str,
    prepare: Callable[[int], dict[str, FileRecord]],
    check: Check,
) -> PublishedStage:
    # stage.parent is the trusted /proc/self/fd access path held by the foundation;
    # following that one descriptor link is intentional. All actual child opens
    # and the user-supplied destination final component reject symlinks.
    source_parent = os.open(stage.parent, os.O_RDONLY | os.O_DIRECTORY)
    target_parent: int | None = None
    held: int | None = None
    moved = False
    name = f"mesh-{kind}-{identity}"
    try:
        target_parent = os.open(
            destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        target_identity = _identity(os.fstat(target_parent))
        held = os.open(
            stage.name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=source_parent,
        )
        stage_identity = _identity(os.fstat(held))
        expected = prepare(held)
        check()
        verify_closed_tree(held, expected, check)
        check()
        if (
            _identity(os.stat(stage.name, dir_fd=source_parent, follow_symlinks=False))
            != stage_identity
            or _identity(destination.stat(follow_symlinks=False)) != target_identity
        ):
            raise MeshImportError(
                "integrity",
                "publication",
                "stage or destination path changed before publication",
            )
        rename_no_replace(source_parent, stage.name, target_parent, name)
        moved = True
        if (
            _identity(os.stat(name, dir_fd=target_parent, follow_symlinks=False))
            != stage_identity
            or _identity(destination.stat(follow_symlinks=False)) != target_identity
        ):
            raise MeshImportError(
                "integrity",
                "publication",
                "published stage or destination path changed",
            )
        os.fsync(target_parent)
        os.fsync(source_parent)
        return PublishedStage(destination / name, kind, identity)
    except BaseException as error:
        if isinstance(error, OSError):
            error = io_failure(error, "publication")
        elif isinstance(error, ScansorError) and not isinstance(error, MeshImportError):
            error = MeshImportError("execution", "publication", str(error))
        if moved:
            error.add_note(
                f"The verified stage was atomically renamed to {destination / name}; final publication checks failed. It was left untouched."
            )
        raise error
    finally:
        if held is not None:
            os.close(held)
        if target_parent is not None:
            os.close(target_parent)
        os.close(source_parent)


def publish_import(imported: AccountedImport, destination: Path) -> PublishedStage:
    data, identity = imported.foundation, imported.identity
    data.monitor.progress("publish-import", 0, 1)

    def prepare(held: int) -> dict[str, FileRecord]:
        data.source.verify(progress=data.monitor.progress)
        files = _materialize_columns(
            held, data.columns, imported.inventory["columns"], data.monitor.check
        )
        files["source/observations.ply"] = (
            data.source.ply.byte_count,
            data.source.ply.sha256,
        )
        if data.source.sidecar is not None:
            files["source/observations.rsInfo"] = (
                data.source.sidecar.byte_count,
                data.source.sidecar.sha256,
            )
        data.source.close()
        files["summary.json"] = _write_control(held, "summary.json", imported.summary)
        _check_control_child(
            "summary.json", files["summary.json"], imported.inventory["summary"]
        )
        files["inventory.json"] = _write_control(
            held, "inventory.json", imported.inventory
        )
        if files["inventory.json"][1] != identity:
            raise MeshImportError(
                "integrity",
                "publication",
                "import inventory changed during publication",
            )
        return files

    result = _publish(
        data.import_directory,
        destination,
        "import",
        identity,
        prepare,
        data.monitor.check,
    )
    data.monitor.progress("publish-import", 1, 1)
    return result


def publish_contributions(
    imported: AccountedImport,
    contributions: CompleteContributions,
    destination: Path,
) -> PublishedStage:
    if contributions.inventory["import_id"] != imported.identity:
        raise MeshImportError(
            "integrity", "publication", "contributions belong to another import"
        )
    data, identity = imported.foundation, contributions.identity
    data.monitor.progress("publish-contributions", 0, 1)

    def prepare(held: int) -> dict[str, FileRecord]:
        files = _materialize_columns(
            held,
            contributions.columns,
            contributions.inventory["columns"],
            data.monitor.check,
        )
        files["request.json"] = _write_control(
            held, "request.json", contributions.request
        )
        files["summary.json"] = _write_control(
            held, "summary.json", contributions.summary
        )
        _check_control_child(
            "request.json", files["request.json"], contributions.inventory["request"]
        )
        _check_control_child(
            "summary.json", files["summary.json"], contributions.inventory["summary"]
        )
        files["inventory.json"] = _write_control(
            held, "inventory.json", contributions.inventory
        )
        if files["inventory.json"][1] != identity:
            raise MeshImportError(
                "integrity",
                "publication",
                "contribution inventory changed during publication",
            )
        return files

    stage = data.import_directory.parent / "contribution"
    result = _publish(
        stage, destination, "contribution", identity, prepare, data.monitor.check
    )
    data.monitor.progress("publish-contributions", 1, 1)
    return result
