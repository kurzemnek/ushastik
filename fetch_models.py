"""Build-time only: download pinned public model assets and verify every byte."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(root):
    source = Path(__file__).resolve().parent
    manifest = json.loads((source / "macos/models.json").read_text())
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for asset in manifest:
        target = root / asset["path"]
        if target.is_file() and sha256(target) == asset["sha256"]:
            continue
        if shutil.disk_usage(root).free < 2 * 1024 ** 3:
            raise RuntimeError("Для моделей нужно не менее 2 ГиБ свободного места")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="model-download-", dir=root) as temp:
            downloaded = Path(temp) / "asset"
            request = urllib.request.Request(asset["url"], headers={"User-Agent": "Ushastik-build/0.3.1"})
            with urllib.request.urlopen(request, timeout=120) as response, downloaded.open("wb") as output:
                shutil.copyfileobj(response, output, 4 * 1024 * 1024)
            payload = downloaded
            if "archive_member" in asset:
                if sha256(downloaded) != asset["archive_sha256"]:
                    raise ValueError(f"Archive checksum mismatch: {asset['path']}")
                payload = Path(temp) / "extracted"
                with tarfile.open(downloaded, "r:bz2") as archive:
                    member = archive.getmember(asset["archive_member"])
                    if not member.isfile():
                        raise ValueError("Model archive member is not a regular file")
                    with archive.extractfile(member) as input_stream, payload.open("wb") as output:
                        shutil.copyfileobj(input_stream, output)
            if sha256(payload) != asset["sha256"]:
                raise ValueError(f"Model checksum mismatch: {asset['path']}")
            shutil.copyfile(payload, target)
        print(f"Verified {asset['path']}", flush=True)
    for directory in ("model", "speaker-model"):
        shutil.copytree(source / "macos/model-notices" / directory, root / directory, dirs_exist_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    fetch(parser.parse_args().root)
