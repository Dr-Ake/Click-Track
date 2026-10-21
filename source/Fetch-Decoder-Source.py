"""Fetch the exact unmodified FFmpeg source used by all release platforms."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
pin = json.loads((ROOT / 'source' / 'decoder-source.json').read_text(encoding='utf-8'))
destination = ROOT / 'work' / 'decoder-source' / pin['filename']
destination.parent.mkdir(parents=True, exist_ok=True)
if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != pin['sha256']:
    temporary = destination.with_suffix('.download')
    try:
        urllib.request.urlretrieve(pin['url'], temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != pin['sha256']:
            raise SystemExit('Decoder source checksum does not match the pinned official archive.')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
print(destination)
