"""Verify that all pre-existing thesis files still match the pre-build manifest."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / 'data/source-manifest.json').read_text(encoding='utf-8'))
changed = []
for relative, expected in manifest.items():
    path = root.parent / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        changed.append(relative)
if changed:
    raise SystemExit('Missing or changed original files:\n' + '\n'.join(changed))
print(f'Verified: all {len(manifest)} pre-existing thesis files are unchanged.')
