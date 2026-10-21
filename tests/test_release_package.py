"""Release archives contain a runnable bundle and notices, never user data."""
from pathlib import Path
import hashlib
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
import release_package as module


@pytest.fixture
def project(tmp_path):
    (tmp_path / "app/_internal/assets").mkdir(parents=True)
    (tmp_path / "app/ClickTrack.exe").write_bytes(b"MZ-fake-executable")
    (tmp_path / "app/_internal/python312.dll").write_bytes(b"runtime")
    (tmp_path / "app/_internal/assets/icon.png").write_bytes(b"icon")
    (tmp_path / "licenses").mkdir()
    (tmp_path / "licenses/LICENSE.txt").write_text("dependency notices", encoding="utf-8")
    (tmp_path / "LICENSE").write_text("application license", encoding="utf-8")
    (tmp_path / "Quick start.txt").write_text("Extract all and open app/ClickTrack.exe", encoding="utf-8")
    for directory, name in (("data", "library.json"), ("work", "private.wav"), ("source", "private.txt")):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / name).write_text("PRIVATE", encoding="utf-8")
    # A stray app-level file cannot be picked up by a broad directory copy.
    (tmp_path / "app/settings.ini").write_text("PRIVATE", encoding="utf-8")
    return tmp_path


def test_clean_archive_structure_hashes_and_checksum(project):
    result = module.package_release(project, "v1.0.0")
    path = Path(result["archive"])
    assert path.name == "ClickTrack-1.0.0-windows-x64.zip"
    prefix = "ClickTrack-1.0.0-windows-x64/"
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert prefix + "app/ClickTrack.exe" in names
        assert prefix + "app/_internal/python312.dll" in names
        assert prefix + "licenses/LICENSE.txt" in names
        assert prefix + "Quick start.txt" in names
        assert prefix + "LICENSE" in names
        assert all(name.startswith(prefix) for name in names)
        assert not any("PRIVATE" in archive.read(name).decode("utf-8", errors="ignore") for name in names)
        manifest = json.loads(archive.read(prefix + "release-manifest.json"))
        assert manifest["version"] == "1.0.0"
        assert manifest["python_required"] is False
        assert manifest["user_data_included"] is False
        for record in manifest["files"]:
            data = archive.read(prefix + record["path"])
            assert record["size"] == len(data)
            assert record["sha256"] == hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert result["sha256"] == digest
    assert Path(result["checksum"]).read_text(encoding="ascii") == f"{digest}  {path.name}\n"


def test_packaging_is_reproducible_and_prefers_fresh_build(project):
    built = project / "work/dist/ClickTrack"
    (built / "_internal").mkdir(parents=True)
    (built / "ClickTrack.exe").write_bytes(b"newer checked build")
    (built / "_internal/runtime.dll").write_bytes(b"new runtime")
    first = module.package_release(project, "1.2.3")
    payload = Path(first["archive"]).read_bytes()
    second = module.package_release(project, "1.2.3")
    assert Path(second["archive"]).read_bytes() == payload
    with zipfile.ZipFile(second["archive"]) as archive:
        assert archive.read("ClickTrack-1.2.3-windows-x64/app/ClickTrack.exe") == b"newer checked build"


@pytest.mark.parametrize("version", ["../1.0.0", "1.0", "01.0.0", "1.00.0", "1.0.0-01", "1.0.0\n", "1/2/3", "v1.0.0/../../"])
def test_rejects_unsafe_or_invalid_versions(project, version):
    with pytest.raises(ValueError):
        module.package_release(project, version)
    assert not (project / "outputs").exists()


@pytest.mark.parametrize("name", ["../bad", "/absolute", "C:/absolute", "a\\b", "a/../b", "a//b"])
def test_rejects_unsafe_archive_member_names(name):
    with pytest.raises(ValueError):
        module._relative_name(name)


def test_missing_executable_or_support_files_is_not_a_release(project):
    (project / "app/ClickTrack.exe").unlink()
    with pytest.raises(ValueError, match="regular file"):
        module.package_release(project, "1.0.0")
    assert not (project / "outputs").exists()


@pytest.mark.parametrize("name", ["library.json", "settings.ini", ".env", "library-recovery-private.json"])
def test_private_files_inside_bundle_are_rejected(project, name):
    (project / "app/_internal" / name).write_text("private", encoding="utf-8")
    with pytest.raises(ValueError, match="Private application data"):
        module.package_release(project, "1.0.0")


def test_symlink_escape_is_rejected(project, tmp_path):
    target = tmp_path / "outside-private.txt"
    target.write_text("private", encoding="utf-8")
    link = project / "app/_internal/linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows host")
    with pytest.raises(ValueError, match="links or junctions"):
        module.package_release(project, "1.0.0")


def test_input_mutation_keeps_previous_archive(project, monkeypatch):
    result = module.package_release(project, "1.0.0")
    previous = Path(result["archive"]).read_bytes()
    original = module._hash_file
    calls = []
    def mutate_after_hash(path):
        digest = original(path)
        if path.name == "ClickTrack.exe" and not calls:
            calls.append(True)
            path.write_bytes(b"changed while packaging")
        return digest
    monkeypatch.setattr(module, "_hash_file", mutate_after_hash)
    with pytest.raises(ValueError, match="changed during packaging"):
        module.package_release(project, "1.0.0")
    assert Path(result["archive"]).read_bytes() == previous
    assert not list((project / "outputs").glob("*.tmp"))
