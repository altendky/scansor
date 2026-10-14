"""Static assembly preserves verified inputs, provenance, and safe output boundaries."""

import importlib.util
import json
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

Inputs = tuple[Path, Path, Path]


def builder_module() -> ModuleType:
    script = Path(__file__).resolve().parents[1] / "scripts/build-browser-app.py"
    spec = importlib.util.spec_from_file_location("browser_app_builder", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = builder_module()


def record(path: str, data: bytes) -> dict[str, object]:
    return {"path": path, "bytes": len(data), "sha256": BUILDER.digest(data)}


@pytest.fixture
def inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Inputs:
    root, runtime = tmp_path / "repo", tmp_path / "runtime"
    root.mkdir()
    runtime.mkdir()
    mappings = {name: name for name in BUILDER.UI_FILES} | {
        "workspace.js": "dist/workspace.js",
        "workspace.css": "dist/workspace.css",
        **BUILDER.VENDOR_FILES,
    }
    for relative in [*mappings.values(), "backend-config.js", *BUILDER.BUILD_INPUTS]:
        target = root / BUILDER.VIEWER / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_text(f"input {relative}\n")
    for name in ("LICENSE-MIT", "LICENSE-APACHE"):
        _ = (root / name).write_text(name)
    mesh = b"bounded fixture"
    fixture = {
        "name": "nozzle-bayonette-simplified",
        "status": "captured bounded example",
        "vertices": 24999,
        "triangles": 49994,
        "units": "unknown source units",
        "files": {"mesh.ply": {"bytes": len(mesh), "sha256": BUILDER.digest(mesh)}},
    }
    sources = {
        BUILDER.EXAMPLE + "/manifest.json": json.dumps(fixture).encode(),
        BUILDER.EXAMPLE + "/mesh.ply": mesh,
        "scansor/__init__.py": b"__version__ = 'test'\n",
    }
    with zipfile.ZipFile(runtime / "scansor.zip", "w") as archive:
        for name, data in sources.items():
            archive.writestr(name, data)
    files = {
        "scansor.zip": (runtime / "scansor.zip").read_bytes(),
        "worker.mjs": b"worker",
        "integrity.mjs": b"integrity",
        "runtime/pyodide.mjs": b"runtime",
        "notices/LICENSE": b"notice",
    }
    for name, data in files.items():
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = target.write_bytes(data)
    manifest = {
        "schema_version": 1,
        "worker_protocol": {
            "version": 4,
            "bootstrap": "initialize",
            "workspace": "retained-nozzle-numerical",
            "cad": "lazy-approved-ocp",
        },
        "source_revision": "native-revision",
        "source_worktree_dirty": False,
        "versions": {"pyodide": "test"},
        "cad": True,
        "redistribution_ready": False,
        "redistribution_gaps": ["pending notices"],
        "assets": [record(name, data) for name, data in files.items()],
        "source_files": [
            {"path": name, "archive_path": name, "sha256": BUILDER.digest(data)}
            for name, data in sources.items()
        ],
    }
    _ = (runtime / "manifest.json").write_text(json.dumps(manifest))

    def repository_state(_root: Path) -> dict[str, str | bool]:
        return {"revision": "ui-revision", "worktree_dirty": True}

    monkeypatch.setattr(
        BUILDER,
        "repository_state",
        repository_state,
    )
    return root, runtime, tmp_path / "output"


def test_static_package_has_complete_hashes_and_separate_ui_runtime_provenance(
    inputs: Inputs,
):
    root, runtime, output = inputs
    # Unlisted probe outputs and test/development assets never enter the package.
    _ = (runtime / "browser-results.json").write_text("unlisted report")
    _ = (root / BUILDER.VIEWER / "unrelated.test.js").write_text("test")
    manifest = BUILDER.build(output, runtime, root=root)
    assert BUILDER.verify(output) == manifest
    assert manifest["source_revision"] == "ui-revision"
    assert {entry["path"] for entry in manifest["build_inputs"]} == {
        f"{BUILDER.VIEWER}/{name}" for name in BUILDER.BUILD_INPUTS
    }
    assert manifest["source_worktree_dirty"] is True
    assert manifest["runtime"]["source_revision"] == "native-revision"
    assert manifest["runtime"]["source_worktree_dirty"] is False
    assert manifest["redistribution_ready"] is False
    assert manifest["capabilities"] == {
        "runtime_bootstrap": True,
        "numerical": True,
        "cad": True,
    }
    assert manifest["bounded_examples"][0]["vertices"] == 24999
    assert manifest["bounded_examples"][0]["units"] == "unknown source units"
    runtime_hash = BUILDER.digest((runtime / "manifest.json").read_bytes())
    assert manifest["runtime"]["path"] == f"app/runtime/{runtime_hash}"
    config = (output / "app/backend-config.js").read_text()
    assert '"type": "worker"' in config
    assert f"./runtime/{runtime_hash}/worker.mjs" in config
    listed = {item["path"] for item in manifest["assets"]}
    assert "app/index.html" in listed and "app/backend-config.js" in listed
    assert f"app/runtime/{runtime_hash}/manifest.json" in listed
    assert "manifest.json" not in listed
    assert not any(
        name.endswith(("browser-results.json", "unrelated.test.js")) for name in listed
    )
    assert listed == {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file()
    } - {"manifest.json"}


def test_corrupt_runtime_fails_without_creating_output(inputs: Inputs):
    root, runtime, output = inputs
    _ = (runtime / "worker.mjs").write_bytes(b"broken")
    with pytest.raises(ValueError, match="asset integrity failure"):
        BUILDER.build(output, runtime, root=root)
    assert not output.exists()


def test_numerical_profile_disables_cad_in_config_and_manifest(inputs: Inputs):
    root, runtime, output = inputs
    target = runtime / "manifest.json"
    manifest = json.loads(target.read_text())
    manifest["cad"] = False
    _ = target.write_text(json.dumps(manifest))
    built = BUILDER.build(output, runtime, root=root)
    assert built["capabilities"]["numerical"] is True
    assert built["capabilities"]["cad"] is False
    assert '"cad": false' in (output / "app/backend-config.js").read_text()


def test_missing_ui_input_fails_without_creating_output(inputs: Inputs):
    root, runtime, output = inputs
    (root / BUILDER.VIEWER / "bootstrap.js").unlink()
    with pytest.raises(ValueError, match="missing UI input"):
        BUILDER.build(output, runtime, root=root)
    assert not output.exists()


def test_existing_output_preserves_user_work(inputs: Inputs):
    root, runtime, output = inputs
    output.mkdir()
    _ = (output / "keep.txt").write_text("user work")
    with pytest.raises(ValueError, match="must be empty"):
        BUILDER.build(output, runtime, root=root)
    assert (output / "keep.txt").read_text() == "user work"


def test_external_symlink_into_repo_is_not_a_safe_output(inputs: Inputs):
    root, runtime, output = inputs
    inside = root / "empty"
    inside.mkdir()
    output.symlink_to(inside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside the repository"):
        BUILDER.build(output, runtime, root=root)
    assert list(inside.iterdir()) == []


def test_output_cannot_mutate_runtime_input(inputs: Inputs):
    root, runtime, _ = inputs
    with pytest.raises(ValueError, match="must not overlap"):
        BUILDER.build(runtime / "new-output", runtime, root=root)


def test_runtime_asset_symlink_cannot_escape_input(inputs: Inputs):
    root, runtime, output = inputs
    worker = runtime / "worker.mjs"
    worker.unlink()
    external = output.parent / "external-worker.mjs"
    _ = external.write_bytes(b"worker")
    worker.symlink_to(external)
    with pytest.raises(ValueError, match="escapes artifact"):
        BUILDER.build(output, runtime, root=root)


@pytest.mark.parametrize(
    "relative",
    [
        "../escape",
        "/absolute",
        "runtime/../escape",
        "runtime\\escape",
        "runtime//asset",
        "asset?query",
    ],
)
def test_asset_paths_reject_unsafe_names(tmp_path: Path, relative: str):
    with pytest.raises(ValueError, match="invalid artifact path"):
        BUILDER.checked_path(tmp_path, relative)


def test_complete_package_verification_rejects_same_size_ui_corruption(inputs: Inputs):
    root, runtime, output = inputs
    BUILDER.build(output, runtime, root=root)
    target = output / "app/app.js"
    _ = target.write_bytes(b"x" * target.stat().st_size)
    with pytest.raises(ValueError, match="asset integrity failure"):
        BUILDER.verify(output)


def test_duplicate_runtime_records_fail_before_copying(inputs: Inputs):
    root, runtime, output = inputs
    target = runtime / "manifest.json"
    manifest = json.loads(target.read_text())
    manifest["assets"].append(manifest["assets"][0])
    _ = target.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="duplicate runtime asset"):
        BUILDER.build(output, runtime, root=root)
    assert not output.exists()


@pytest.mark.parametrize("protocol", ["missing", "bootstrap", "previous-workspace"])
def test_old_probe_runtime_fails_before_creating_an_application(
    inputs: Inputs, protocol: str
):
    root, runtime, output = inputs
    target = runtime / "manifest.json"
    manifest = json.loads(target.read_text())
    if protocol == "bootstrap":
        manifest["worker_protocol"] = {"version": 1, "bootstrap": "initialize"}
    elif protocol == "previous-workspace":
        manifest["worker_protocol"]["version"] = 3
    else:
        del manifest["worker_protocol"]
    _ = target.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="workspace protocol"):
        BUILDER.build(output, runtime, root=root)
    assert not output.exists()
