"""Strict float-free execution requests; paths and tuning never rekey mesh data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scansor.mesh_controls import Control
from scansor.mesh_display_numeric import DisplayTransform
from scansor.mesh_errors import MeshImportError
from scansor.mesh_resources import MAX_BATCH_ROWS


def _check_path(value: object, *, required: bool = False) -> None:
    if value is None and not required:
        return
    if not isinstance(value, Path) or "\0" in str(value):
        raise MeshImportError("structure", "worker-request", "invalid execution path")


def _check_transform(value: object) -> None:
    if value is not None and not isinstance(value, DisplayTransform):
        raise MeshImportError(
            "structure", "worker-request", "invalid display transform"
        )


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
    display: Path | None = None
    expected_display_id: str | None = None
    display_transform: DisplayTransform | None = None

    def __post_init__(self) -> None:
        _check_path(self.source, required=True)
        if self.operation not in (
            "import",
            "verify",
            "display",
            "verify-display",
        ) or self.storage not in (
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
        for path in (
            self.source,
            self.destination,
            self.sidecar,
            self.contribution,
            self.display,
        ):
            _check_path(path)
        for identity in (
            self.expected_import_id,
            self.expected_contribution_id,
            self.expected_display_id,
        ):
            if identity is not None and (
                type(identity) is not str
                or len(identity) != 64
                or any(c not in "0123456789abcdef" for c in identity)
            ):
                raise MeshImportError(
                    "structure", "worker-request", "invalid expected mesh identity"
                )
        _check_transform(self.display_transform)
        if self.operation == "import":
            if (
                self.destination is None
                or self.contribution is not None
                or self.expected_import_id is not None
                or self.expected_contribution_id is not None
                or self.display is not None
                or self.expected_display_id is not None
                or self.display_transform is not None
            ):
                raise MeshImportError(
                    "structure",
                    "worker-request",
                    "import requires a destination and forbids verification fields",
                )
        elif self.operation == "verify":
            if (
                self.destination is not None
                or self.sidecar is not None
                or (
                    self.contribution is None
                    and self.expected_contribution_id is not None
                )
                or self.display is not None
                or self.expected_display_id is not None
                or self.display_transform is not None
            ):
                raise MeshImportError(
                    "structure", "worker-request", "invalid verification fields"
                )
        elif (
            self.contribution is None
            or self.sidecar is not None
            or self.storage == "ram"
            or (
                self.operation == "display"
                and (
                    self.destination is None
                    or self.display is not None
                    or self.expected_display_id is not None
                )
            )
            or (
                self.operation == "verify-display"
                and (
                    self.destination is not None
                    or self.display is None
                    or self.display_transform is not None
                )
            )
        ):
            raise MeshImportError(
                "structure", "worker-request", "invalid display execution fields"
            )

    @property
    def published_kinds(self) -> tuple[str, ...]:
        return (
            ("import", "contribution")
            if self.operation == "import"
            else ("display",)
            if self.operation == "display"
            else ()
        )

    @property
    def readonly_artifacts(self) -> tuple[Path, ...]:
        if self.operation == "import":
            return ()
        return tuple(
            path
            for path in (self.source, self.contribution, self.display)
            if path is not None
        )

    def record(self) -> dict[str, Control]:
        def path(value: Path | None) -> str | None:
            return None if value is None else str(value.absolute())

        return {
            "revision": "mesh-worker-request-v2",
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
            "display": path(self.display),
            "expected_display_id": self.expected_display_id,
            "display_transform": None
            if self.display_transform is None
            else self.display_transform.record(),
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
            "display",
            "expected_display_id",
            "display_transform",
        }
        if (
            not isinstance(value, dict)
            or value.keys() != keys
            or value["revision"] != "mesh-worker-request-v2"
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
        import_id, contribution_id, display_id = (
            value["expected_import_id"],
            value["expected_contribution_id"],
            value["expected_display_id"],
        )
        if (
            type(operation) is not str
            or type(storage) is not str
            or type(budget) is not int
            or (chunk is not None and type(chunk) is not int)
            or (import_id is not None and type(import_id) is not str)
            or (contribution_id is not None and type(contribution_id) is not str)
            or (display_id is not None and type(display_id) is not str)
        ):
            raise MeshImportError(
                "structure", "worker-request", "incorrect request field types"
            )
        transform = None
        if value["display_transform"] is not None:
            # Reuse the existing strict display metadata parser, including its
            # exact operations/meaning fields, rather than accepting extra knobs.
            from scansor.mesh_display_verify import read_transform

            transform = read_transform({"transform": value["display_transform"]})
        return cls(
            operation=operation,
            source=source,
            destination=path("destination"),
            sidecar=path("sidecar"),
            contribution=path("contribution"),
            expected_import_id=import_id,
            expected_contribution_id=contribution_id,
            budget_bytes=budget,
            storage=storage,
            chunk_rows=chunk,
            display=path("display"),
            expected_display_id=display_id,
            display_transform=transform,
        )
