"""Fetch the pinned official Homebrew payload for building Station Mini Root."""
from pathlib import Path
import hashlib
import urllib.request
root=Path(__file__).resolve().parents[1]
url="https://github.com/hertebeznat-cell/alice-homebrew/releases/download/v0.4.1/alice-homebrew-linux-arm64"
expected="f9527ff3aae066e3058b37706de217f9f7a9b71cd131a565c1ffa0366d0f811a"
size=9568418
target=root/'alice-homebrew/dist/alice-homebrew-linux-arm64'
if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:
    print('Pinned Homebrew payload already present')
else:
    target.parent.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen(url,timeout=90) as response:
        data=response.read(size+1)
    if len(data)!=size or hashlib.sha256(data).hexdigest()!=expected:
        raise RuntimeError('Homebrew payload size or SHA-256 mismatch')
    pending=target.with_suffix('.pending')
    pending.write_bytes(data)
    pending.replace(target)
    print('Verified Homebrew ARM64 payload ready')
