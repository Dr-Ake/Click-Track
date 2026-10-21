# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
import shutil
import PySide6

source = Path(SPECPATH)
root = source.parent
bundled_decoder = root / 'app' / '_internal' / 'ffmpeg.exe'
decoder = os.environ.get('CLICKTRACK_FFMPEG') or (str(bundled_decoder) if bundled_decoder.is_file() else shutil.which('ffmpeg'))
if not decoder:
    raise RuntimeError('FFmpeg must be installed or specified with CLICKTRACK_FFMPEG.')

a = Analysis([str(source / 'app.py')], pathex=[str(source)],
             binaries=[(decoder, '.')], datas=[(str(root / 'assets'), 'assets')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
             excludes=[], noarchive=False, optimize=0)

# Qt uses Windows' built-in ICU, with unsuffixed procedure names. Foreign ICU
# copies discovered on PATH (for example Poppler's ICU78) cannot replace it.
# The Windows 10/11 system DLL exports the API expected by this Qt wheel.
a.binaries = [item for item in a.binaries
              if Path(item[0]).name.lower() != 'icuuc.dll'
              and not Path(item[0]).name.lower().startswith('icudt')
              and not ('portaudio' in item[0].lower() and
                       ('asio' in item[0].lower() or '32bit' in item[0].lower()
                        or item[0].lower().endswith(('.dylib', '.so'))))]
a.datas = [item for item in a.datas
           if not ('portaudio' in item[0].lower() and
                   ('asio' in item[0].lower() or '32bit' in item[0].lower()
                    or item[0].lower().endswith(('.dylib', '.so'))))]

# Use the Qt wheel's matching C++ runtime consistently at the bundle root.
qt = Path(PySide6.__file__).parent
for index, (dest, src, kind) in enumerate(a.binaries):
    if dest.lower() in ('vcruntime140.dll', 'vcruntime140_1.dll'):
        match = qt / Path(dest).name
        if match.is_file():
            a.binaries[index] = (dest, str(match), kind)

pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='ClickTrack',
          debug=False, bootloader_ignore_signals=False, strip=False,
          upx=False, console=False, disable_windowed_traceback=False,
          icon=str(root / 'assets' / 'clicktrack.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='ClickTrack')
