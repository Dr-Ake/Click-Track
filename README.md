# Click-Track

Windows metronome, song tempo finder, saved song library, and beat-snapped practice loops.

The active project is `D:\Click-Track`. Program files, source, settings, library, logs, and development work live here.

- Open the desktop **ClickTrack** shortcut, or run `app\ClickTrack.exe`.
- Editable application code is in `source`; icons are in `assets`.
- Saved songs, tempos, preferences, and loop marks are in `data`.
- Automated tests are in `tests`; development tools, build output, and checkpoints are in `work`.
- See `Quick start.txt` for the controls and supported song formats.

The library keeps references to original song files. Those music files stay in their existing folders.

To run the editable source, use `work\venv\Scripts\python.exe source\app.py`.
To test, use `work\venv\Scripts\python.exe -m pytest tests -q`.
To build a Windows app, run `source\Build.ps1`; the checked result is written to `work\dist\ClickTrack` for verification before replacing `app`.

The compiled app runs without Python installed. Recreating the development environment requires Python 3.12 and `source\requirements-dev.txt`.
