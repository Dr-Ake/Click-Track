"""Create a clean, reproducible portable Windows release from a checked bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import zipfile


_VERSION = re.compile(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z")
_STAMP = (2000, 1, 1, 0, 0, 0)
_BLOCKED_NAMES = {"library.json", "settings.ini", ".env"}


def normalize_version(value):
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Choose a semantic version such as 1.0.0.")
    match = _VERSION.fullmatch(value)
    if not match or any(len(part) > 1 and part.startswith("0") for part in match.group(1, 2, 3)):
        raise ValueError("Choose a semantic version such as 1.0.0.")
    prerelease = match.group(4)
    if prerelease and any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease.split(".")):
        raise ValueError("Numeric prerelease identifiers cannot have leading zeros.")
    return value[1:] if value.startswith("v") else value


def _relative_name(value):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value or ":" in value:
        raise ValueError("The release contains an unsafe archive path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("The release contains an unsafe archive path.")
    return str(path)


def _is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _regular_file(path, root):
    if _is_link(path):
        raise ValueError(f"Release inputs cannot be links or junctions: {path.name}")
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("A release input leaves its allowed source folder.") from exc
    if not path.is_file():
        raise ValueError(f"A release input is not a regular file: {path.name}")
    return path


def _walk_files(root):
    if _is_link(root) or not root.is_dir():
        raise ValueError(f"Release input must be a normal folder: {root.name}")
    for directory, directories, files in os.walk(root, followlinks=False):
        current = Path(directory)
        if _is_link(current):
            raise ValueError("Release inputs cannot contain links or junctions.")
        for name in directories:
            if _is_link(current / name):
                raise ValueError("Release inputs cannot contain links or junctions.")
            if name.lower() in (".git", "__pycache__"):
                raise ValueError("Development files cannot be included in a release bundle.")
        for name in sorted(files):
            path = _regular_file(current / name, root)
            if name.lower() in _BLOCKED_NAMES or name.lower().startswith("library-recovery-"):
                raise ValueError(f"Private application data cannot be packaged: {name}")
            yield path


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_info(path):
    info = zipfile.ZipInfo(_relative_name(path), date_time=_STAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _release_readme(version):
    return (
        f"ClickTrack {version} for Windows x64\n\n"
        "1. Extract the complete ZIP into a folder you can write to.\n"
        "2. Open the app folder and double-click ClickTrack.exe.\n\n"
        "No Python installation, account, or API key is required.\n"
        "Keep ClickTrack.exe and its _internal support folder together.\n"
        "Your library and settings are created in data beside the app folder.\n"
        "Song files stay in the original folders you choose.\n"
        "See Quick start.txt for song analysis, click tracks, and practice loops.\n"
        "Third-party license notices are in licenses.\n"
    ).encode("utf-8")


def package_release(project_root, version, app_directory=None, output_directory=None):
    """Package only known distributable inputs; never package saved user data."""
    version = normalize_version(version)
    root = Path(project_root).resolve()
    if app_directory is None:
        built = root / "work" / "dist" / "ClickTrack"
        app = built if (built / "ClickTrack.exe").is_file() else root / "app"
    else:
        app = Path(app_directory)
        app = app if app.is_absolute() else root / app
    if _is_link(app) or not app.is_dir():
        raise ValueError("Choose a built ClickTrack app folder.")
    executable = _regular_file(app / "ClickTrack.exe", app)
    internal = app / "_internal"
    if not internal.is_dir():
        raise ValueError("The built app is missing its _internal support folder.")
    licenses = root / "licenses"
    quick_start = _regular_file(root / "Quick start.txt", root)

    inputs = [("app/ClickTrack.exe", executable), ("Quick start.txt", quick_start)]
    inputs.extend(("app/_internal/" + path.relative_to(internal).as_posix(), path)
                  for path in _walk_files(internal))
    inputs.extend(("licenses/" + path.relative_to(licenses).as_posix(), path)
                  for path in _walk_files(licenses))
    for candidate in ("LICENSE", "LICENSE.txt"):
        if (root / candidate).exists():
            inputs.append((candidate, _regular_file(root / candidate, root)))
    inputs = sorted(((_relative_name(name), path) for name, path in inputs), key=lambda item: item[0])
    names = [name.casefold() for name, _ in inputs]
    if len(names) != len(set(names)):
        raise ValueError("Release paths must be unique on Windows.")

    records = [{"path": name, "size": path.stat().st_size, "sha256": _hash_file(path)}
               for name, path in inputs]
    manifest = dict(application="ClickTrack", version=version, platform="windows-x64",
                    python_required=False, entrypoint="app/ClickTrack.exe",
                    user_data_included=False, files=records)
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    package_name = f"ClickTrack-{version}-windows-x64"
    output = Path(output_directory) if output_directory is not None else root / "outputs"
    if not output.is_absolute():
        output = root / output
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / (package_name + ".zip")
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix=".clicktrack-release-", suffix=".zip.tmp", dir=output)
        os.close(descriptor)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as archive:
            for (name, path), record in zip(inputs, records):
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as source, archive.open(_zip_info(package_name + "/" + name), "w", force_zip64=True) as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        target.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                if size != record["size"] or digest.hexdigest() != record["sha256"]:
                    raise ValueError("A release input changed during packaging. Build again before releasing.")
            archive.writestr(_zip_info(package_name + "/release-manifest.json"), manifest_data)
            archive.writestr(_zip_info(package_name + "/README.txt"), _release_readme(version))
        os.replace(temporary, archive_path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    archive_hash = _hash_file(archive_path)
    checksum_path = output / (archive_path.name + ".sha256")
    checksum_path.write_text(f"{archive_hash}  {archive_path.name}\n", encoding="ascii")
    return dict(version=version, archive=str(archive_path), checksum=str(checksum_path),
                sha256=archive_hash, files=len(inputs) + 2,
                uncompressed_bytes=sum(record["size"] for record in records))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--app-directory")
    parser.add_argument("--output-directory")
    args = parser.parse_args()
    result = package_release(args.project_root, args.version, args.app_directory, args.output_directory)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
