"""Notice integrity and requirement evidence must survive selection and tampering."""

import hashlib
import json
import runpy
import shutil
import stat
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSEMBLER = runpy.run_path(str(ROOT / "scripts/build-browser-redistribution.py"))
RUNTIME = runpy.run_path(str(ROOT / "scripts/build-browser-runtime.py"))


@pytest.fixture
def package(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    files: list[dict[str, Any]] = []
    rules: dict[str, Any] = {}
    groups: dict[str, list[str]] = {}
    for group in ("numeric", "ui", "cad"):
        data = f"{group} license\n".encode()
        _ = (inputs / f"{group}-LICENSE").write_bytes(data)
        target = f"notices/{group}/LICENSE"
        files.append(
            {
                "path": target,
                "source_file": f"{group}-LICENSE",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "group": group,
            }
        )
        rules[group + "-notices"] = {
            "kind": "assets",
            "paths": [target],
            "basis": "test fixture",
        }
        groups[group] = [group + "-notices"]
    rules["existing-core-and-wheel-notices"] = {
        "kind": "external",
        "reason": "provided by the runtime builder",
    }
    groups["numeric"].append("existing-core-and-wheel-notices")
    bindings = {"runtime_lock_sha256": "a" * 64, "ui_package_lock_sha256": "b" * 64}
    inventory = {
        "schema_version": 1,
        "bindings": bindings,
        "files": files,
        "archives": [],
        "covered_sources": [],
        "requirement_groups": groups,
        "requirements": rules,
    }
    lock = inputs / "inventory.json"
    _ = lock.write_text(json.dumps(inventory))
    output = tmp_path / "package"
    ASSEMBLER["assemble"](lock, output, None)
    return lock, output, bindings


def test_selected_package_is_self_contained_and_omits_other_capabilities(
    package: tuple[Path, Path, dict[str, Any]], tmp_path: Path
):
    lock, original, bindings = package
    manifest, records = RUNTIME["read_redistribution"](
        original, ["numeric"], lock, bindings
    )
    assert manifest["selected_groups"] == ["numeric"]
    assert manifest["redistribution_ready"] is False  # Core requirement stays external.
    assert not any(
        "cad" in record["groups"] or "ui" in record["groups"] for record in records
    )
    selected = tmp_path / "selected"
    selected.mkdir()
    for record in records:
        target = selected / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original / record["path"], target)
    _ = (selected / "manifest.json").write_text(json.dumps(manifest))
    assert ASSEMBLER["verify"](selected, inventory_path=lock) == manifest


def test_manifest_cannot_promote_external_requirements_to_ready(
    package: tuple[Path, Path, dict[str, Any]],
):
    lock, output, _ = package
    path = output / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["groups"]["numeric"]["requirements_complete"] = True
    manifest["redistribution_ready"] = True
    _ = path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="derived evidence"):
        ASSEMBLER["verify"](output, inventory_path=lock)


def test_reviewed_inventory_and_build_bindings_are_required(
    package: tuple[Path, Path, dict[str, Any]], tmp_path: Path
):
    lock, output, bindings = package
    other = tmp_path / "other.json"
    _ = other.write_bytes(lock.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="approved inventory"):
        RUNTIME["read_redistribution"](output, ["ui"], other, bindings)
    with pytest.raises(ValueError, match="build input mismatch"):
        RUNTIME["read_redistribution"](
            output, ["ui"], lock, bindings | {"ui_package_lock_sha256": "c" * 64}
        )


def test_notice_bytes_cannot_be_replaced_by_changing_manifest_hashes(
    package: tuple[Path, Path, dict[str, Any]],
):
    lock, output, _ = package
    notice = output / "notices/ui/LICENSE"
    replacement = b"x" * notice.stat().st_size
    _ = notice.write_bytes(replacement)
    path = output / "manifest.json"
    manifest = json.loads(path.read_text())
    for record in manifest["assets"]:
        if record["path"] == "notices/ui/LICENSE":
            record["sha256"] = hashlib.sha256(replacement).hexdigest()
    _ = path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="pinned inventory"):
        ASSEMBLER["verify"](output, inventory_path=lock)


def test_runtime_overlap_rejection_does_not_create_files_in_input(
    package: tuple[Path, Path, dict[str, Any]],
):
    _, supplement, _ = package
    output = supplement / "new-output"
    with pytest.raises(ValueError, match="must not overlap"):
        RUNTIME["build"](output, None, False, Path("unused"), supplement)
    assert not output.exists()


@pytest.mark.parametrize(
    "fault", ["traversal", "duplicate", "symlink", "corrupt-member"]
)
def test_archive_failures_never_write_outside_fresh_output(
    package: tuple[Path, Path, dict[str, Any]], tmp_path: Path, fault: str
):
    lock, _, _ = package
    archive_path = lock.parent / "notices.zip"
    member = "package/LICENSE"
    data = b"pinned license"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(archive_path, "w") as archive:
            info = zipfile.ZipInfo(member)
            if fault == "symlink":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, data)
            if fault == "traversal":
                archive.writestr("../escape", b"unselected")
            if fault == "duplicate":
                archive.writestr(member, data)
    inventory = json.loads(lock.read_text())
    inventory["archives"] = [
        {
            "source_file": archive_path.name,
            "bytes": archive_path.stat().st_size,
            "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "group": "ui",
            "members": [
                {
                    "archive_path": member,
                    "path": "notices/ui/extra/LICENSE",
                    "bytes": len(data),
                    "sha256": "0" * 64
                    if fault == "corrupt-member"
                    else hashlib.sha256(data).hexdigest(),
                }
            ],
        }
    ]
    _ = lock.write_text(json.dumps(inventory))
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        ASSEMBLER["assemble"](lock, output, None)
    assert not (tmp_path / "escape").exists()
    assert not (lock.parent / "escape").exists()
    assert not (output / "manifest.json").exists()


def test_packaged_verification_does_not_need_original_source_inputs(
    package: tuple[Path, Path, dict[str, Any]], tmp_path: Path
):
    lock, output, _ = package
    reviewed_lock = tmp_path / "reviewed-lock.json"
    _ = shutil.copyfile(lock, reviewed_lock)
    shutil.rmtree(lock.parent)
    manifest = ASSEMBLER["verify"](output, inventory_path=reviewed_lock)
    assert manifest["groups"]["ui"]["requirements_complete"] is True


def test_selection_uses_the_verified_snapshot_without_reopening_package(
    package: tuple[Path, Path, dict[str, Any]],
):
    lock, output, _ = package
    verified = ASSEMBLER["verify"](output, inventory_path=lock)
    shutil.rmtree(output)
    selected = ASSEMBLER["select_verified_manifest"](verified, ["ui"])
    assert selected["inventory_sha256"] == verified["inventory_sha256"]
    assert selected["bindings"] == verified["bindings"]
    assert selected["selected_groups"] == ["ui"]


@pytest.mark.parametrize(
    "cad_asset", ["original-path", "renamed-bytes", "unknown-cad-group"]
)
def test_numerical_profile_cannot_ship_cad_without_cad_requirements(cad_asset: str):
    numeric = {
        "path": "runtime/numeric.whl",
        "bytes": 1,
        "sha256": "a" * 64,
        "group": "numeric",
    }
    cad = {"path": "runtime/cad.whl", "bytes": 2, "sha256": "b" * 64, "group": "cad"}
    lock = {"assets": [numeric, cad], "licenses": []}
    shipped = cad | {"group": "probe"}
    if cad_asset == "renamed-bytes":
        shipped["path"] = "hidden/renamed.whl"
    elif cad_asset == "unknown-cad-group":
        shipped = cad | {"path": "new/unreviewed.whl", "sha256": "c" * 64}
    with pytest.raises(ValueError, match="includes CAD assets"):
        RUNTIME["validate_runtime_profile"](
            {"cad": False, "assets": [numeric, shipped]}, lock
        )


def test_cad_profile_cannot_prepend_an_unreviewed_executable():
    numeric = {
        "path": "runtime/numeric.whl",
        "bytes": 1,
        "sha256": "a" * 64,
        "group": "numeric",
    }
    cad = {"path": "runtime/cad.whl", "bytes": 2, "sha256": "b" * 64, "group": "cad"}
    unknown = cad | {"path": "runtime/unknown.whl", "sha256": "c" * 64}
    with pytest.raises(ValueError, match="unreviewed executable"):
        RUNTIME["validate_runtime_profile"](
            {"cad": True, "assets": [numeric, unknown, cad]},
            {"assets": [numeric, cad], "licenses": []},
        )
