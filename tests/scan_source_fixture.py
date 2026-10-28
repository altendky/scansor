"""Explicitly bounded source materialization for legacy small-fixture assertions."""

from experiments.scan_workspace import ScanWorkspace, VerifiedSource, Workspace


def source_of(workspace: Workspace) -> VerifiedSource:
    assert isinstance(workspace, ScanWorkspace) and workspace.source is not None
    return workspace.source


def read_source_bytes(workspace: Workspace) -> bytes:
    return source_of(workspace).read_all(2_000_000)
