#!/usr/bin/env bash
# Native Mac release. The receiving Mac needs neither Python nor FFmpeg installed.
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
    printf '%s\n' 'Build-Mac.sh must run on macOS.' >&2
    exit 1
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="${CLICKTRACK_PYTHON:-python3}"
version="${1:-${CLICKTRACK_VERSION:-1.0.0}}"
version="${version#v}"
architecture="${2:-$(uname -m)}"
minimum_system="${CLICKTRACK_MAC_MIN_VERSION:-15.0}"

# Validate before creating build or output directories.
"$python" - "$version" "$architecture" "$minimum_system" <<'PY'
import platform
import re
import sys
version, architecture, minimum = sys.argv[1:]
if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
    raise SystemExit("Use a release version containing three numbers, such as 1.0.0.")
if architecture not in ("arm64", "x86_64") or architecture != platform.machine():
    raise SystemExit("Build each architecture with a matching native Mac and Python.")
if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", minimum):
    raise SystemExit("Use a macOS minimum version such as 15.0.")
PY

decoder="${CLICKTRACK_FFMPEG:-$(command -v ffmpeg || true)}"
if [[ ! -f "$decoder" || ! -x "$decoder" || "$(basename "$decoder")" != ffmpeg ]]; then
    printf '%s\n' 'Set CLICKTRACK_FFMPEG to a native executable named ffmpeg.' >&2
    exit 1
fi
decoder="$(cd "$(dirname "$decoder")" && pwd -P)/$(basename "$decoder")"
if [[ " $(lipo -archs "$decoder") " != *" $architecture "* ]]; then
    printf '%s\n' "FFmpeg does not contain the $architecture architecture." >&2
    exit 1
fi
"$decoder" -hide_banner -version

export CLICKTRACK_VERSION="$version" CLICKTRACK_ARCH="$architecture"
export CLICKTRACK_MAC_MIN_VERSION="$minimum_system" CLICKTRACK_FFMPEG="$decoder"
export MACOSX_DEPLOYMENT_TARGET="$minimum_system"

# Use the project's existing artwork; iconutil assembles native macOS icon sizes.
mkdir -p "$root/work"
icon_work="$(mktemp -d "$root/work/mac-icon.XXXXXX")"
iconset="$icon_work/ClickTrack.iconset"
mkdir -p "$iconset"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$root/assets/clicktrack.png" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$root/assets/clicktrack.png" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$icon_work/ClickTrack.icns"
export CLICKTRACK_MAC_ICON="$icon_work/ClickTrack.icns"

"$python" -m PyInstaller --noconfirm --clean \
    --distpath "$root/work/dist" --workpath "$root/work/build-macos-$architecture" \
    "$root/source/ClickTrack-mac.spec"
app="$root/work/dist/ClickTrack.app"
test -x "$app/Contents/MacOS/ClickTrack"
# PyInstaller signs nested Mach-O files and the bundle ad hoc. This is not notarization.
codesign --verify --deep --strict --verbose=2 "$app"

# A newer runner must not quietly raise the release's advertised minimum macOS.
"$python" - "$app" "$architecture" "$minimum_system" <<'PY'
from pathlib import Path
import re
import subprocess
import sys

app, architecture, minimum = sys.argv[1:]
def version_tuple(value):
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (3 - len(parts))

magic = {bytes.fromhex(value) for value in (
    "feedface", "cefaedfe", "feedfacf", "cffaedfe",
    "cafebabe", "bebafeca", "cafebabf", "bfbafeca",
)}
checked = 0
for path in Path(app, "Contents").rglob("*"):
    if path.is_symlink() or not path.is_file():
        continue
    with path.open("rb") as stream:
        if stream.read(4) not in magic:
            continue
    result = subprocess.run(["otool", "-arch", architecture, "-l", str(path)],
                            text=True, capture_output=True, check=True)
    targets = re.findall(r"cmd LC_BUILD_VERSION\s+cmdsize \d+\s+platform \d+\s+minos ([\d.]+)", result.stdout)
    targets += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version ([\d.]+)", result.stdout)
    if not targets:
        raise SystemExit(f"Cannot verify macOS deployment target: {path}")
    for target in targets:
        if version_tuple(target) > version_tuple(minimum):
            raise SystemExit(f"{path} requires macOS {target}; advertised minimum is {minimum}.")
    checked += 1
if not checked:
    raise SystemExit("The Mac bundle contains no verifiable native binaries.")
print(f"Verified {checked} native files against macOS {minimum}.")
PY

# Explicit allowlist: no library, recordings, settings, logs, or build inputs.
stage="$(mktemp -d "$root/work/mac-release-stage.XXXXXX")"
name="ClickTrack-$version-macos-$architecture"
package="$stage/$name"
mkdir -p "$package" "$root/outputs"
ditto "$app" "$package/ClickTrack.app"
ditto "$root/licenses" "$package/licenses"
cp "$root/Quick start.txt" "$package/Quick start.txt"
cp "$root/LICENSE" "$package/LICENSE"
"$python" - "$package" "$version" "$architecture" "$minimum_system" <<'PY'
import json
from pathlib import Path
import sys
package, version, architecture, minimum = sys.argv[1:]
manifest = {
    "application": "ClickTrack", "version": version, "platform": "macos",
    "architecture": architecture, "minimum_macos": minimum,
    "python_required": False, "user_data_included": False,
    "code_signing": "ad-hoc", "notarized": False,
}
Path(package, "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY

# ditto preserves the .app's symlinks and native resource metadata.
temporary_zip="$stage/$name.zip"
ditto -c -k --sequesterRsrc --keepParent "$package" "$temporary_zip"
mv -f "$temporary_zip" "$root/outputs/$name.zip"
(
    cd "$root/outputs"
    shasum -a 256 "$name.zip" > "$name.zip.sha256"
)
printf 'Created %s\n' "$root/outputs/$name.zip"
