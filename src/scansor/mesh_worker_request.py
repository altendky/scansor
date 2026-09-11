"""Strict float-free execution requests; paths and tuning never rekey mesh data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scansor.mesh_controls import Control
from scansor.mesh_errors import MeshImportError
from scansor.mesh_resources import MAX_BATCH_ROWS


def _check_path(value: object, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, Path) or "\0" in str(value):
        raise MeshImportError("structure", "worker-request", "invalid execution path")


@dataclass(frozen=True)
class WorkerRequest:
    operation: str
    source: Path
    destination: Path | None = None
    sidecar: Path | None = None
    contribution: Path | None = None
    expected_import_id: str | None = None
    expected_contribution_id: str | None = None
    budget_bytes: int = 512 * 1024 * 1024
    storage: str = "auto"
    chunk_rows: int | None = None

    def __post_init__(self) -> None:
        _check_path(self.source, required=True)
        if self.operation not in ("import", "verify") or self.storage not in (
            "auto",
            "ram",
            "disk",
        ):
            raise MeshImportError(
                "structure", "worker-request", "unknown operation or storage"
            )
        if (
            type(self.budget_bytes) is not int
            or not 1 <= self.budget_bytes <= 2**63 - 1
        ):
            raise MeshImportError(
                "structure", "worker-request", "invalid worker memory budget"
            )
        if self.chunk_rows is not None and (
            type(self.chunk_rows) is not int
            or not 1 <= self.chunk_rows <= MAX_BATCH_ROWS
        ):
            raise MeshImportError(
                "structure", "worker-request", "invalid worker batch bound"
            )
        for path in (self.source, self.destination, self.sidecar, self.contribution):
            _check_path(path)
        for identity in (self.expected_import_id, self.expected_contribution_id):
            if identity is not None and (
                type(identity) is not str
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise MeshImportError(
                    "structure", "worker-request", "invalid expected mesh identity"
                )
        if self.operation == "import":
            if (
                self.destination is None
                or self.contribution is not None
                or self.expected_import_id is not None
                or self.expected_contribution_id is not None
            ):
                raise MeshImportError(
                    "structure",
                    "worker-request",
                    "import requires a destination and forbids verification fields",
                )
        elif (
            self.destination is not None
            or self.sidecar is not None
            or (self.contribution is None and self.expected_contribution_id is not None)
        ):
            raise MeshImportError(
                "structure", "worker-request", "invalid verification fields"
            )

    def record(self) -> dict[str, Control]:
        def path(value: Path | None) -> str | None:
            return None if value is None else str(value.absolute())

        return {
            "revision": "mesh-worker-request-v1",
            "operation": self.operation,
            "source": path(self.source),
            "destination": path(self.destination),
            "sidecar": path(self.sidecar),
            "contribution": path(self.contribution),
            "expected_import_id": self.expected_import_id,
            "expected_contribution_id": self.expected_contribution_id,
            "budget_bytes": self.budget_bytes,
            "storage": self.storage,
            "chunk_rows": self.chunk_rows,
        }

    @classmethod
    def from_record(cls, value: Control) -> WorkerRequest:
        keys = {
            "revision",
            "operation",
            "source",
            "destination",
            "sidecar",
            "contribution",
            "expected_import_id",
            "expected_contribution_id",
            "budget_bytes",
            "storage",
            "chunk_rows",
        }
        if (
            not isinstance(value, dict)
            or value.keys() != keys
            or value["revision"] != "mesh-worker-request-v1"
        ):
            raise MeshImportError(
                "structure",
                "worker-request",
                "unknown, missing or malformed request fields",
            )

        def path(key: str, *, optional: bool = True) -> Path | None:
            item = value[key]
            if item is None and optional:
                return None
            if type(item) is not str or not Path(item).is_absolute() or "\0" in item:
                raise MeshImportError(
                    "structure",
                    "worker-request",
                    "request paths must be absolute strings",
                )
            return Path(item)

        source = path("source", optional=False)
        assert source is not None
        operation, storage, budget, chunk = (
            value["operation"],
            value["storage"],
            value["budget_bytes"],
            value["chunk_rows"],
        )
        import_id, contribution_id = (
            value["expected_import_id"],
            value["expected_contribution_id"],
        )
        if (
            type(operation) is not str
            or type(storage) is not str
            or type(budget) is not int
            or (chunk is not None and type(chunk) is not int)
            or (import_id is not None and type(import_id) is not str)
            or (contribution_id is not None and type(contribution_id) is not str)
        ):
            raise MeshImportError(
                "structure", "worker-request", "incorrect request field types"
            )
        return cls(
            operation,
            source,
            path("destination"),
            path("sidecar"),
            path("contribution"),
            import_id,
            contribution_id,
            budget,
            storage,
            chunk,
        )
