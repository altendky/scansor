from __future__ import annotations

import os
from pathlib import Path

import pytest

import scansor.files as files_module
import scansor.runs as runs_module
from scansor.errors import ScansorError
from scansor.models import (
    InspectJobConfig,
    ResolvedLogLevel,
    ResolvedMaxHeaderBytes,
    ResolvedMaxInputBytes,
    ResolvedMaxVertices,
    ResolvedSettings,
)
from scansor.ply import NPY_MAGIC, load_canonical_npy
from scansor.runs import inspect_source, publish_run, verify_run
from scansor.serialization import canonical_json, parse_canonical_json, sha256
from tests.conftest import write_ply


def settings(*, max_vertices: int = 5_000_000, **sources: str) -> ResolvedSettings:
    values = {
        "log_level": "warning",
        "max_header_bytes": 65_536,
        "max_input_bytes": 67_108_864,
        "max_vertices": max_vertices,
    }
    return ResolvedSettings(
        log_level=ResolvedLogLevel(
            value="warning", source=sources.get("log_level", "default")
        ),
        max_header_bytes=ResolvedMaxHeaderBytes(
            value=values["max_header_bytes"],
            source=sources.get("max_header_bytes", "default"),
        ),
        max_input_bytes=ResolvedMaxInputBytes(
            value=values["max_input_bytes"],
            source=sources.get("max_input_bytes", "default"),
        ),
        max_vertices=ResolvedMaxVertices(
            value=max_vertices, source=sources.get("max_vertices", "default")
        ),
    )


def make_run(tmp_path: Path) -> tuple[Path, Path, ResolvedSettings]:
    source = write_ply(
        tmp_path / "input.ply",
        rows=[(1.0, 2.0, 3.0, 0.0, 1.0, 0.0, 1, 2, 3)],
        normals=True,
        rgb=True,
    )
    output = tmp_path / "run"
    resolved = settings()
    report, canonical = inspect_source(
        InspectJobConfig(
            input_path=source, output_path=output, unit="m", frame="scanner"
        ),
        resolved,
    )
    publish_run(output, report, canonical)
    return source, output, resolved


def snapshot(path: Path) -> dict[str, tuple[int, bytes]]:
    return {
        item.name: (item.stat().st_mtime_ns, item.read_bytes())
        for item in path.iterdir()
    }


def test_run_is_deterministic_and_verify_is_read_only(tmp_path: Path) -> None:
    source, first, resolved = make_run(tmp_path)
    second = tmp_path / "run-2"
    report, canonical = inspect_source(
        InspectJobConfig(
            input_path=source, output_path=second, unit="m", frame="scanner"
        ),
        resolved,
    )
    publish_run(second, report, canonical)
    assert {item.name: item.read_bytes() for item in first.iterdir()} == {
        item.name: item.read_bytes() for item in second.iterdir()
    }
    before = snapshot(first)
    assert verify_run(first, None).run_id == report.run_id
    assert snapshot(first) == before


def test_source_and_output_locations_do_not_change_run_id(tmp_path: Path) -> None:
    first_source = write_ply(tmp_path / "first.ply")
    second_source = tmp_path / "second.ply"
    second_source.write_bytes(first_source.read_bytes())
    resolved = settings()
    first_report, first_canonical = inspect_source(
        InspectJobConfig(
            input_path=first_source,
            output_path=tmp_path / "first-run",
            unit="m",
            frame="scanner",
        ),
        resolved,
    )
    second_report, second_canonical = inspect_source(
        InspectJobConfig(
            input_path=second_source,
            output_path=tmp_path / "second-run",
            unit="m",
            frame="scanner",
        ),
        resolved,
    )
    assert first_report.source.path != second_report.source.path
    assert first_report.run_id == second_report.run_id
    assert first_canonical == second_canonical


@pytest.mark.parametrize("field", ["input_path", "output_path"])
@pytest.mark.parametrize("path", ["bad\0path", "bad\ud800path"])
def test_inspect_job_rejects_invalid_local_paths(
    tmp_path: Path, field: str, path: str
) -> None:
    values = {
        "input_path": tmp_path / "input.ply",
        "output_path": tmp_path / "run",
        "unit": "m",
        "frame": "scanner",
    }
    values[field] = Path(path)
    with pytest.raises(ValueError, match="job path"):
        InspectJobConfig.model_validate(values)


@pytest.mark.parametrize("path", ["bad\0path", "bad\ud800path"])
def test_publication_translates_invalid_output_paths(tmp_path: Path, path: str) -> None:
    source = write_ply(tmp_path / "input.ply")
    report, canonical = inspect_source(
        InspectJobConfig(
            input_path=source,
            output_path=tmp_path / "valid-run",
            unit="m",
            frame="scanner",
        ),
        settings(),
    )
    with pytest.raises(ScansorError, match="invalid output path"):
        publish_run(Path(path), report, canonical)


def test_job_record_fit_options_are_immutable(tmp_path: Path) -> None:
    _source, run, _resolved = make_run(tmp_path)
    job = verify_run(run, None).job
    assert job.supported_fit_options == ()
    with pytest.raises(AttributeError):
        job.supported_fit_options.append("unsupported")


def test_replacement_input_must_replay_identically(tmp_path: Path) -> None:
    source, run, _resolved = make_run(tmp_path)
    replacement = tmp_path / "replacement.ply"
    replacement.write_bytes(source.read_bytes())
    assert verify_run(run, replacement).source.path == str(source.absolute())
    replacement.write_bytes(replacement.read_bytes() + b"x")
    with pytest.raises(ScansorError, match="trailing bytes"):
        verify_run(run, replacement)


def test_verify_uses_recorded_nondefault_settings(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    recorded = settings(max_vertices=1).model_copy(
        update={
            "max_vertices": ResolvedMaxVertices(value=1, source="command-line"),
        }
    )
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        recorded,
    )
    publish_run(output, report, canonical)
    assert verify_run(output, None).settings == recorded


def test_refuses_overwrite_and_preserves_failed_stage(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    resolved = settings()
    request = InspectJobConfig(
        input_path=source, output_path=output, unit="m", frame="f"
    )
    report, canonical = inspect_source(request, resolved)
    output.mkdir()
    marker = output / "marker"
    marker.write_text("preserve", encoding="ascii")
    with pytest.raises(ScansorError, match="refusing to overwrite"):
        publish_run(output, report, canonical)
    assert marker.read_text(encoding="ascii") == "preserve"
    marker.unlink()
    output.rmdir()

    def fail_write(*args, **kwargs):
        raise OSError("injected")

    monkeypatch.setattr("scansor.runs.write_new_file", fail_write)
    with pytest.raises(OSError, match="injected"):
        publish_run(output, report, canonical)
    assert not output.exists()
    stages = list(tmp_path.glob(".run.scansor-stage-*"))
    assert len(stages) == 1
    assert list(stages[0].iterdir()) == []


def test_failed_publication_preserves_replaced_staging_path(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    moved_stage = tmp_path / "moved-stage"
    replacement: list[Path] = []

    def replace_stage_then_fail(*args, **kwargs):
        stage = next(tmp_path.glob(".run.scansor-stage-*"))
        stage.rename(moved_stage)
        stage.mkdir()
        replacement.append(stage)
        raise OSError("injected after stage replacement")

    monkeypatch.setattr("scansor.runs.write_new_file", replace_stage_then_fail)
    with pytest.raises(OSError, match="injected after stage replacement"):
        publish_run(output, report, canonical)
    assert replacement[0].is_dir()
    assert moved_stage.is_dir()


def test_failed_publication_preserves_o_excl_collision(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    original = runs_module.write_new_file

    def collide(directory_fd: int, name: str, data: bytes):
        if name == "canonical.npy":
            descriptor = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(b"concurrent")
        return original(directory_fd, name, data)

    monkeypatch.setattr("scansor.runs.write_new_file", collide)
    with pytest.raises(FileExistsError):
        publish_run(output, report, canonical)
    stage = next(tmp_path.glob(".run.scansor-stage-*"))
    assert (stage / "canonical.npy").read_bytes() == b"concurrent"


def test_failed_publication_preserves_partial_created_file(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )

    def partial_write(directory_fd: int, name: str, data: bytes):
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(b"partial")
        raise OSError("injected partial write")

    monkeypatch.setattr("scansor.runs.write_new_file", partial_write)
    with pytest.raises(OSError, match="injected partial write"):
        publish_run(output, report, canonical)
    stage = next(tmp_path.glob(".run.scansor-stage-*"))
    assert (stage / "canonical.npy").read_bytes() == b"partial"


@pytest.mark.parametrize("mutation", ["add", "replace"])
def test_publish_rejects_staged_artifact_races(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    original = runs_module.hash_run_file

    def mutate_after_final_hash(directory_fd: int, name: str, max_bytes: int):
        result = original(directory_fd, name, max_bytes)
        if name == "manifest.sha256":
            if mutation == "add":
                descriptor = os.open(
                    "unexpected",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=directory_fd,
                )
                os.close(descriptor)
            else:
                os.unlink("report.json", dir_fd=directory_fd)
                os.symlink(source, "report.json", dir_fd=directory_fd)
        return result

    monkeypatch.setattr("scansor.runs.hash_run_file", mutate_after_final_hash)
    with pytest.raises(ScansorError, match="staged artifact"):
        publish_run(output, report, canonical)
    assert not output.exists()
    assert len(list(tmp_path.glob(".run.scansor-stage-*"))) == 1


def test_publication_rejects_parent_replacement_after_rename(
    tmp_path: Path, monkeypatch
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    source = write_ply(parent / "input.ply")
    output = parent / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    original = runs_module.rename_no_replace
    moved = tmp_path / "moved"

    def replace_parent(*args, **kwargs):
        original(*args, **kwargs)
        parent.rename(moved)
        parent.mkdir()

    monkeypatch.setattr("scansor.runs.rename_no_replace", replace_parent)
    with pytest.raises(ScansorError, match="output path changed"):
        publish_run(output, report, canonical)
    assert not output.exists()
    assert (moved / "run").is_dir()


def test_publication_never_deletes_concurrent_output_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    original = runs_module.rename_no_replace
    moved_published = tmp_path / "moved-published"

    def replace_output(*args, **kwargs):
        original(*args, **kwargs)
        output.rename(moved_published)
        output.mkdir()
        (output / "concurrent-marker").write_text("preserve", encoding="ascii")

    monkeypatch.setattr("scansor.runs.rename_no_replace", replace_output)
    with pytest.raises(ScansorError, match="output path changed"):
        publish_run(output, report, canonical)
    assert (output / "concurrent-marker").read_text(encoding="ascii") == "preserve"
    assert (moved_published / "manifest.json").is_file()


@pytest.mark.parametrize("mutation", ["add", "replace"])
def test_publication_revalidates_artifacts_after_rename(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    original = runs_module.rename_no_replace

    def mutate_published_run(*args, **kwargs):
        original(*args, **kwargs)
        if mutation == "add":
            (output / "unexpected").write_bytes(b"race")
        else:
            moved = tmp_path / "moved-report.json"
            (output / "report.json").rename(moved)
            (output / "report.json").write_bytes(moved.read_bytes())

    monkeypatch.setattr("scansor.runs.rename_no_replace", mutate_published_run)
    with pytest.raises(ScansorError, match="staged artifact"):
        publish_run(output, report, canonical)


def test_publication_rehashes_artifacts_after_rename(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    original = runs_module.hash_run_file
    calls = 0

    def fail_second_canonical_hash(directory_fd: int, name: str, max_bytes: int):
        nonlocal calls
        result = original(directory_fd, name, max_bytes)
        if name == "canonical.npy":
            calls += 1
            if calls == 2:
                return result[0], "0" * 64
        return result

    monkeypatch.setattr("scansor.runs.hash_run_file", fail_second_canonical_hash)
    with pytest.raises(ScansorError, match="staged artifact content"):
        publish_run(output, report, canonical)
    assert calls == 2


@pytest.mark.parametrize(
    "failure", ["output-probe", "mkdir", "stage-open", "stage-fstat"]
)
def test_publication_setup_failures_close_descriptors(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    source = write_ply(tmp_path / "input.ply")
    output = tmp_path / "run"
    report, canonical = inspect_source(
        InspectJobConfig(input_path=source, output_path=output, unit="m", frame="f"),
        settings(),
    )
    real_open = os.open
    real_stat = os.stat
    real_mkdir = os.mkdir
    real_fstat = os.fstat
    opened: list[int] = []

    def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
        if failure == "stage-open" and dir_fd is not None:
            raise PermissionError("injected stage open failure")
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        opened.append(descriptor)
        return descriptor

    def failing_stat(path, *, dir_fd=None, follow_symlinks=True):
        if failure == "output-probe" and dir_fd is not None:
            raise PermissionError("injected output probe failure")
        return real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def failing_mkdir(path, mode=0o777, *, dir_fd=None):
        if failure == "mkdir":
            raise PermissionError("injected mkdir failure")
        return real_mkdir(path, mode, dir_fd=dir_fd)

    def failing_fstat(descriptor):
        if failure == "stage-fstat" and len(opened) == 2:
            raise PermissionError("injected stage fstat failure")
        return real_fstat(descriptor)

    with monkeypatch.context() as context:
        context.setattr(runs_module.os, "open", tracking_open)
        context.setattr(runs_module.os, "stat", failing_stat)
        context.setattr(runs_module.os, "mkdir", failing_mkdir)
        context.setattr(runs_module.os, "fstat", failing_fstat)
        with pytest.raises(PermissionError, match="injected"):
            publish_run(output, report, canonical)

    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize(
    "name", ["canonical.npy", "report.json", "manifest.json", "manifest.sha256"]
)
def test_corruption_is_rejected(tmp_path: Path, name: str) -> None:
    _, run, _resolved = make_run(tmp_path)
    path = run / name
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ScansorError):
        verify_run(run, None)


def test_unexpected_file_and_symlink_artifact_are_rejected(tmp_path: Path) -> None:
    _, run, _resolved = make_run(tmp_path)
    (run / "unexpected").write_bytes(b"")
    with pytest.raises(ScansorError, match="unexpected"):
        verify_run(run, None)
    (run / "unexpected").unlink()
    (run / "report.json").unlink()
    (run / "report.json").symlink_to(tmp_path / "input.ply")
    with pytest.raises(ScansorError, match="cannot open run artifact"):
        verify_run(run, None)


def test_symlink_source_parent_and_special_file_are_rejected(tmp_path: Path) -> None:
    source = write_ply(tmp_path / "input.ply")
    link = tmp_path / "link.ply"
    link.symlink_to(source)
    request = InspectJobConfig(
        input_path=link, output_path=tmp_path / "run", unit="m", frame="f"
    )
    with pytest.raises(ScansorError, match="non-symlink"):
        inspect_source(request, settings())
    parent_link = tmp_path / "parent-link"
    parent_link.symlink_to(tmp_path, target_is_directory=True)
    request = request.model_copy(update={"input_path": parent_link / "input.ply"})
    with pytest.raises(ScansorError, match="parent"):
        inspect_source(request, settings())
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    request = request.model_copy(update={"input_path": fifo})
    with pytest.raises(ScansorError, match="regular"):
        inspect_source(request, settings())


@pytest.mark.parametrize("replacement", ["file", "parent"])
def test_read_regular_rejects_path_replacement_during_read(
    tmp_path: Path, monkeypatch, replacement: str
) -> None:
    parent = tmp_path / "source"
    parent.mkdir()
    source = write_ply(parent / "input.ply")
    source_bytes = source.read_bytes()
    real_fdopen = os.fdopen
    replaced = False

    def replace_path() -> None:
        if replacement == "file":
            source.rename(tmp_path / "moved-input.ply")
            source.write_bytes(source_bytes)
        else:
            parent.rename(tmp_path / "moved-source")
            parent.mkdir()
            source.write_bytes(source_bytes)

    class ReplacingStream:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self) -> int:
            return self.stream.fileno()

        def read(self, size: int = -1) -> bytes:
            nonlocal replaced
            data = self.stream.read(size)
            if not replaced:
                replaced = True
                replace_path()
            return data

    def replacing_fdopen(*args, **kwargs):
        return ReplacingStream(real_fdopen(*args, **kwargs))

    monkeypatch.setattr(files_module.os, "fdopen", replacing_fdopen)
    with pytest.raises(ScansorError, match="changed while reading"):
        files_module.read_regular(source, "test input", len(source_bytes))
    assert replaced


def test_json_rejects_duplicate_nonfinite_and_noncanonical() -> None:
    for data, message in (
        (b'{"x":1,"x":2}\n', "duplicate"),
        (b'{"x":NaN}\n', "nonfinite"),
        (b'{"x": 1}\n', "not canonical"),
    ):
        with pytest.raises(ScansorError, match=message):
            parse_canonical_json(data, "test", 100)
    assert parse_canonical_json(canonical_json({"x": 1}), "test", 100) == {"x": 1}


def test_verification_rejects_run_root_replacement(tmp_path: Path, monkeypatch) -> None:
    _, run, _resolved = make_run(tmp_path)
    moved = tmp_path / "moved-run"
    original = runs_module.hash_run_file

    def replace_after_hash(directory_fd: int, name: str, max_bytes: int):
        result = original(directory_fd, name, max_bytes)
        if name == "manifest.sha256":
            run.rename(moved)
            run.mkdir()
        return result

    monkeypatch.setattr("scansor.runs.hash_run_file", replace_after_hash)
    with pytest.raises(ScansorError, match="run path changed"):
        verify_run(run, None)


@pytest.mark.parametrize("mutation", ["root", "add", "replace"])
def test_verification_revalidates_anchored_run_after_replay(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    _, run, _resolved = make_run(tmp_path)
    original = runs_module.read_regular
    moved_run = tmp_path / "moved-run"

    def mutate_run_after_source_read(*args, **kwargs):
        result = original(*args, **kwargs)
        if mutation == "root":
            run.rename(moved_run)
            run.mkdir()
        elif mutation == "add":
            (run / "unexpected").write_bytes(b"race")
        else:
            report_path = run / "report.json"
            report_path.rename(tmp_path / "moved-report.json")
            report_path.write_bytes(b"replacement")
        return result

    monkeypatch.setattr("scansor.runs.read_regular", mutate_run_after_source_read)
    message = "run path changed" if mutation == "root" else "final verification"
    with pytest.raises(ScansorError, match=message):
        verify_run(run, None)


@pytest.mark.parametrize("mutation", ["add", "symlink"])
def test_final_verification_rejects_artifact_entry_races(
    tmp_path: Path, monkeypatch, mutation: str
) -> None:
    source, run, _resolved = make_run(tmp_path)
    original = runs_module.hash_run_file

    def mutate_after_final_hash(directory_fd: int, name: str, max_bytes: int):
        result = original(directory_fd, name, max_bytes)
        if name == "manifest.sha256":
            if mutation == "add":
                (run / "unexpected").write_bytes(b"race")
            else:
                report_path = run / "report.json"
                report_path.rename(tmp_path / "moved-report.json")
                report_path.symlink_to(source)
        return result

    monkeypatch.setattr("scansor.runs.hash_run_file", mutate_after_final_hash)
    with pytest.raises(ScansorError, match="final verification"):
        verify_run(run, None)


def _write_rewritten_report(run: Path, report: object) -> None:
    report_path = run / "report.json"
    manifest_path = run / "manifest.json"
    report_bytes = canonical_json(report)
    report_path.write_bytes(report_bytes)
    manifest = parse_canonical_json(
        manifest_path.read_bytes(), "manifest", 8 * 1024 * 1024
    )
    manifest["artifacts"]["report.json"] = {
        "byte_count": len(report_bytes),
        "sha256": sha256(report_bytes),
    }
    manifest_bytes = canonical_json(manifest)
    manifest_path.write_bytes(manifest_bytes)
    (run / "manifest.sha256").write_bytes(
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    )


def _write_rewritten_manifest(run: Path, manifest: object) -> None:
    manifest_bytes = canonical_json(manifest)
    (run / "manifest.json").write_bytes(manifest_bytes)
    (run / "manifest.sha256").write_bytes(
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    )


def _remove_persisted_field(
    run: Path, artifact: str, field_path: tuple[str, ...]
) -> None:
    path = run / f"{artifact}.json"
    record = parse_canonical_json(path.read_bytes(), artifact, 8 * 1024 * 1024)
    parent = record
    for field in field_path[:-1]:
        parent = parent[field]
    del parent[field_path[-1]]
    if artifact == "report":
        _write_rewritten_report(run, record)
    else:
        _write_rewritten_manifest(run, record)


def _rewrite_run_settings(run: Path, field: str, value: object) -> None:
    report = parse_canonical_json(
        (run / "report.json").read_bytes(), "report", 8 * 1024 * 1024
    )
    report["settings"][field]["value"] = value
    _write_rewritten_report(run, report)


def _rewrite_run_source_path(run: Path, value: str) -> None:
    report = parse_canonical_json(
        (run / "report.json").read_bytes(), "report", 8 * 1024 * 1024
    )
    report["source"]["path"] = value
    _write_rewritten_report(run, report)


def _rewrite_run_canonical(run: Path, data: bytes) -> None:
    report = parse_canonical_json(
        (run / "report.json").read_bytes(), "report", 8 * 1024 * 1024
    )
    report["canonical"] = {
        **report["canonical"],
        "byte_count": len(data),
        "sha256": sha256(data),
    }
    (run / "canonical.npy").write_bytes(data)
    _write_rewritten_report(run, report)
    manifest_path = run / "manifest.json"
    manifest = parse_canonical_json(
        manifest_path.read_bytes(), "manifest", 8 * 1024 * 1024
    )
    manifest["artifacts"]["canonical.npy"] = {
        "byte_count": len(data),
        "sha256": sha256(data),
    }
    manifest_bytes = canonical_json(manifest)
    manifest_path.write_bytes(manifest_bytes)
    (run / "manifest.sha256").write_bytes(
        f"{sha256(manifest_bytes)}  manifest.json\n".encode("ascii")
    )


def malformed_unicode_field_npy() -> bytes:
    header = (
        "{'descr': [('\\u0100', '<f8')], 'fortran_order': False, 'shape': (1,), }\n"
    ).encode("ascii")
    return (
        NPY_MAGIC + b"\x02\x00" + len(header).to_bytes(4, "little") + header + b"\0" * 8
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("log_level", 1), ("max_header_bytes", "65536"), ("max_vertices", "1")],
)
def test_integrity_consistent_wrong_setting_types_fail_cleanly(
    tmp_path: Path, field: str, value: object
) -> None:
    _, run, _resolved = make_run(tmp_path)
    _rewrite_run_settings(run, field, value)
    with pytest.raises(ScansorError, match="persisted run model is invalid"):
        verify_run(run, None)


@pytest.mark.parametrize(
    ("artifact", "field_path"),
    [
        ("manifest", ("format",)),
        ("manifest", ("format_status",)),
        ("report", ("format",)),
        ("report", ("format_status",)),
        ("report", ("canonical", "coordinate_unit")),
        ("report", ("canonical", "media_type")),
        ("report", ("job", "deterministic")),
        ("report", ("job", "model")),
        ("report", ("job", "normal_handling")),
        ("report", ("job", "random_seed")),
        ("report", ("job", "selection")),
        ("report", ("job", "supported_fit_options")),
        ("report", ("semantic_absences", "active_factor_ids")),
        ("report", ("semantic_absences", "factors")),
        ("report", ("semantic_absences", "fit_result")),
        ("report", ("semantic_absences", "held_out_roles")),
        ("report", ("semantic_absences", "mappings")),
        ("report", ("semantic_absences", "memberships")),
        ("report", ("semantic_absences", "model")),
        ("report", ("semantic_absences", "observations")),
        ("report", ("semantic_absences", "publication_state")),
    ],
)
def test_integrity_consistent_omitted_persisted_defaults_are_rejected(
    tmp_path: Path, artifact: str, field_path: tuple[str, ...]
) -> None:
    _, run, _resolved = make_run(tmp_path)
    _remove_persisted_field(run, artifact, field_path)
    with pytest.raises(ScansorError, match=f"persisted {artifact}.*canonical model"):
        verify_run(run, None)


@pytest.mark.parametrize("path", ["bad\0path", "bad\ud800path"])
def test_integrity_consistent_invalid_source_paths_fail_cleanly(
    tmp_path: Path, path: str
) -> None:
    _, run, _resolved = make_run(tmp_path)
    _rewrite_run_source_path(run, path)
    with pytest.raises(ScansorError, match="persisted run model is invalid"):
        verify_run(run, None)


def test_integrity_consistent_malformed_npy_fails_cleanly(tmp_path: Path) -> None:
    _, run, _resolved = make_run(tmp_path)
    _rewrite_run_canonical(run, b"PK\x03\x04malformed zip")
    with pytest.raises(ScansorError, match=r"canonical\.npy"):
        verify_run(run, None)


def test_malformed_extreme_numbers_and_empty_npy_fail_cleanly() -> None:
    for data in (
        b'{"x":1e999}\n',
        b'{"x":' + b"9" * 5_000 + b"}\n",
        b"[" * 1_500 + b"0" + b"]" * 1_500 + b"\n",
    ):
        with pytest.raises(ScansorError):
            parse_canonical_json(data, "test", 10_000)
    with pytest.raises(ScansorError, match=r"canonical\.npy"):
        load_canonical_npy(b"")
    with pytest.raises(ScansorError, match="required NPY magic"):
        load_canonical_npy(b"PK\x03\x04not an npy")
    with pytest.raises(ScansorError, match=r"canonical\.npy"):
        load_canonical_npy(malformed_unicode_field_npy())
    oversized_header = NPY_MAGIC + b"\x02\x00" + (4_097).to_bytes(4, "little")
    with pytest.raises(ScansorError, match="header exceeds"):
        load_canonical_npy(oversized_header)
