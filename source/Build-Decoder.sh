#!/usr/bin/env bash
# Compile an unmodified, local-file-only LGPL FFmpeg for the native release.
# The caller verifies the official source archive's pinned SHA256 before this step.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
version="${CLICKTRACK_FFMPEG_VERSION:-9.0.1}"
archive="${1:-${CLICKTRACK_FFMPEG_SOURCE_ARCHIVE:-}}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || ! -f "$archive" ]]; then
    printf '%s\n' 'Provide the verified ffmpeg-9.0.1.tar.xz source archive.' >&2
    exit 1
fi
archive="$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")"

case "$(uname -s)" in
    Darwin)
        binary=ffmpeg
        export MACOSX_DEPLOYMENT_TARGET="${CLICKTRACK_MAC_MIN_VERSION:-14.0}"
        platform_flags=()
        ;;
    MINGW*|MSYS*)
        case "${MSYSTEM:-}" in
            MINGW64|UCRT64) ;;
            *) printf '%s\n' 'Build Windows FFmpeg in a native MSYS2 MINGW64 or UCRT64 shell.' >&2; exit 1 ;;
        esac
        binary=ffmpeg.exe
        # Avoid a separate libgcc/libwinpthread runtime on recipients' computers.
        platform_flags=(--extra-ldflags=-static)
        ;;
    *) printf '%s\n' 'Build-Decoder.sh supports native macOS and MSYS2 Windows builds.' >&2; exit 1 ;;
esac

jobs="${CLICKTRACK_BUILD_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf 2)}"
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' 'CLICKTRACK_BUILD_JOBS must be a positive number.' >&2
    exit 1
fi
# Reject archives that contain paths outside the expected source directory.
while IFS= read -r member; do
    case "$member" in
        "ffmpeg-$version"|"ffmpeg-$version/"*) ;;
        *) printf 'Unexpected source archive path: %s\n' "$member" >&2; exit 1 ;;
    esac
    case "/$member/" in
        */../*|*/./*) printf 'Unsafe source archive path: %s\n' "$member" >&2; exit 1 ;;
    esac
done < <(tar -tf "$archive")

mkdir -p "$root/work"
build="$(mktemp -d "$root/work/decoder-build.XXXXXX")"
prefix="$root/work/decoder"
mkdir -p "$prefix/bin"
tar -xf "$archive" -C "$build"
cd "$build/ffmpeg-$version"

options=(
    --prefix="$prefix"
    --disable-autodetect --disable-network
    --disable-gpl --disable-nonfree --disable-version3
    --enable-static --disable-shared --disable-debug --disable-doc
    --disable-programs --enable-ffmpeg
    --disable-avdevice --disable-everything
    --enable-decoders --enable-demuxers --enable-parsers
    --enable-protocol=file,pipe
    --enable-muxer=wav --enable-encoder=pcm_f32le
    --enable-filter=aresample,aformat
)
# FFmpeg 9.0.1 always registers abuffer/abuffersink with libavfilter; enabling
# the ffmpeg program also selects its aformat/anull/atrim support filters.
if ! command -v nasm >/dev/null 2>&1; then
    options+=(--disable-x86asm)
fi
./configure "${options[@]}" "${platform_flags[@]}"
make -j "$jobs" "$binary"
install -m 755 "$binary" "$prefix/bin/$binary"
"$prefix/bin/$binary" -hide_banner -version > "$prefix/FFmpeg-build-info.txt"
"$prefix/bin/$binary" -hide_banner -buildconf >> "$prefix/FFmpeg-build-info.txt" 2>&1
"$prefix/bin/$binary" -hide_banner -L > "$prefix/FFmpeg-license.txt" 2>&1
cp COPYING.LGPLv2.1 "$prefix/COPYING.LGPLv2.1"
if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$archive" >> "$prefix/FFmpeg-build-info.txt"
else
    shasum -a 256 "$archive" >> "$prefix/FFmpeg-build-info.txt"
fi
printf 'Native decoder: %s\n' "$prefix/bin/$binary"
