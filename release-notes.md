ClickTrack runs without installing Python, FFmpeg, or other development tools.

Choose your download:

- **Windows 10/11, 64 bit:** `ClickTrack-1.0.0-windows-x64.zip`. Extract the complete ZIP into a writable folder, then open `app/ClickTrack.exe`.
- **Apple Silicon Mac (M1 or newer), macOS 14+:** `ClickTrack-1.0.0-macos-arm64.zip`.
- **Intel Mac, macOS 14+:** `ClickTrack-1.0.0-macos-x86_64.zip`.

On Mac, extract the ZIP and drag `ClickTrack.app` to Applications. These builds have an ad hoc signature and are not notarized. If macOS blocks the first launch, attempt to open it, then use **System Settings → Privacy & Security → Open Anyway**. [Apple's instructions](https://support.apple.com/en-us/102445). Windows may show an unknown publisher notice.

Features include adjustable/tap tempo, offline song BPM detection, an ordered saved song library, beat-snapped seeking, A/B practice loops with song playback, and click-only WAV export. Some recordings need manual tempo or first-click correction; the detector assumes one steady tempo.

The ZIPs include the application, required runtimes, decoder, instructions, and third-party notices. Your songs and saved data are not included. Windows saves data beside the `app` folder; Mac saves to `~/Library/Application Support/ClickTrack`.

Each platform is built on its matching native GitHub runner. Source tests and packaged/extracted-app checks cover window creation, audio decoding, tempo detection, saved songs, and transport controls. Physical audio-device listening on a Mac remains a user acceptance check.

`SHA256SUMS.txt` verifies downloads. `ffmpeg-9.0.1.tar.xz` and the build recipe are provided for the separate LGPL audio decoder; you do not need these source files to run ClickTrack.
