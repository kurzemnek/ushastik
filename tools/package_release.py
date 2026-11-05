"""Add public release documents without repacking any original ZIP member.

Supports single-disk ZIP32 only. macOS ditto archives may contain UTF-8 names
without the UTF-8 flag; names are interpreted as UTF-8 while their original
local records and central-directory records remain byte-for-byte unchanged.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path, PurePosixPath
import struct
import tempfile
import time
import zipfile


EOCD = struct.Struct("<4s4H2IH")
CD = struct.Struct("<4s6H3I5H2I")
LOCAL = struct.Struct("<4s5H3I2H")
MAX32 = 0xFFFFFFFF
MAX16 = 0xFFFF


class UnsupportedArchive(ValueError):
    """Malformed ZIP or a feature this conservative updater cannot preserve."""


@dataclass(frozen=True)
class Member:
    name: str
    record: bytes
    offset: int
    compressed_size: int


@dataclass(frozen=True)
class Directory:
    offset: int
    members: tuple[Member, ...]
    comment: bytes


def fail(message):
    raise UnsupportedArchive(message)


def check_extra(extra):
    position = 0
    while position < len(extra):
        if len(extra) - position < 4:
            fail("Truncated ZIP extra field")
        kind, size = struct.unpack_from("<HH", extra, position)
        position += 4
        if position + size > len(extra):
            fail("Truncated ZIP extra-field payload")
        if kind == 1:
            fail("ZIP64 is unsupported")
        position += size


def read_directory(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - (EOCD.size + MAX16)))
        tail = stream.read()
        position = len(tail)
        found = None
        while True:
            position = tail.rfind(b"PK\x05\x06", 0, position)
            if position < 0:
                break
            if position + EOCD.size <= len(tail):
                fields = EOCD.unpack_from(tail, position)
                if position + EOCD.size + fields[-1] == len(tail):
                    found = (size - len(tail) + position, fields, tail[position + EOCD.size:])
                    break
        if found is None:
            fail("Missing valid end-of-central-directory at exact EOF")
        eocd_offset, fields, comment = found
        _, disk, cd_disk, disk_count, count, cd_size, cd_offset, _ = fields
        if count == MAX16 or disk_count == MAX16 or cd_size == MAX32 or cd_offset == MAX32:
            fail("ZIP64 is unsupported")
        if disk or cd_disk or disk_count != count:
            fail("Multi-disk ZIP is unsupported")
        if eocd_offset >= 20:
            stream.seek(eocd_offset - 20)
            if stream.read(4) == b"PK\x06\x07":
                fail("ZIP64 locator is unsupported")
        if cd_offset + cd_size != eocd_offset:
            fail("Central-directory bounds are invalid or contain unsupported trailing records")
        stream.seek(cd_offset)
        raw = stream.read(cd_size)
        position, members, names = 0, [], set()
        for _ in range(count):
            if position + CD.size > len(raw):
                fail("Truncated central directory")
            values = CD.unpack_from(raw, position)
            if values[0] != b"PK\x01\x02":
                fail("Invalid central-directory signature")
            flags, compressed, uncompressed = values[3], values[8], values[9]
            nlen, xlen, clen, start_disk, offset = values[10], values[11], values[12], values[13], values[16]
            end = position + CD.size + nlen + xlen + clen
            if end > len(raw):
                fail("Truncated central-directory member")
            if start_disk or compressed == MAX32 or uncompressed == MAX32 or offset == MAX32:
                fail("ZIP64/multi-disk member is unsupported")
            if flags & 1:
                fail("Encrypted ZIP members are unsupported")
            filename = raw[position + CD.size:position + CD.size + nlen]
            try:
                name = filename.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise UnsupportedArchive("Archive member name is not UTF-8") from exc
            if not name or "\x00" in name or name in names:
                fail(f"Empty, NUL-containing, or duplicate member: {name!r}")
            names.add(name)
            check_extra(raw[position + CD.size + nlen:position + CD.size + nlen + xlen])
            if offset + LOCAL.size > cd_offset:
                fail(f"Local-header bounds are invalid: {name}")
            stream.seek(offset)
            local = stream.read(LOCAL.size)
            if len(local) != LOCAL.size:
                fail("Truncated local header")
            local_values = LOCAL.unpack(local)
            if local_values[0] != b"PK\x03\x04":
                fail(f"Invalid local-header signature: {name}")
            if local_values[7] == MAX32 or local_values[8] == MAX32:
                fail("ZIP64 local member is unsupported")
            local_nlen, local_xlen = local_values[-2:]
            if offset + LOCAL.size + local_nlen + local_xlen + compressed > cd_offset:
                fail(f"Compressed member extends into the central directory: {name}")
            if stream.read(local_nlen) != filename:
                fail(f"Local/central filename mismatch: {name}")
            check_extra(stream.read(local_xlen))
            members.append(Member(name, raw[position:end], offset, compressed))
            position = end
        if position != len(raw):
            fail("Unexpected central-directory records or wrong member count")
    return Directory(cd_offset, tuple(members), comment)


def documents(source, platform, directory, release_readme=None):
    names = {member.name for member in directory.members}
    if platform == "windows":
        executables = [PurePosixPath(n) for n in names if PurePosixPath(n).name == "Ushastik.exe"]
        if len(executables) != 1 or len(executables[0].parts) > 2:
            fail("Expected one top-level Ushastik.exe, optionally inside one root folder")
        prefix = str(executables[0].parent)
        prefix = "" if prefix == "." else prefix + "/"
        readme = source / "ПРОЧТИ МЕНЯ.txt"
        replaced = {prefix + "ПРОЧТИ МЕНЯ.txt"} & names
    else:
        apps = [n for n in names if n.endswith(".app/Contents/MacOS/Ushastik")
                and len(PurePosixPath(n).parts) == 4]
        if len(apps) != 1:
            fail("Expected one top-level .app/Contents/MacOS/Ushastik")
        prefix, replaced = "", set()
        readme = source / "macos" / "ПРОЧТИ МЕНЯ.txt"

    required = {prefix + "RELEASE_README.txt": Path(release_readme) if release_readme else readme,
                prefix + "LICENSE": source / "LICENSE",
                prefix + "THIRD_PARTY_NOTICES.md": source / "THIRD_PARTY_NOTICES.md"}
    if platform == "windows":
        required[prefix + "ПРОЧТИ МЕНЯ.txt"] = readme
    licenses = source / "licenses"
    if not licenses.is_dir() or licenses.is_symlink():
        raise ValueError(f"Missing regular source licenses directory: {licenses}")
    license_count = 0
    for path in sorted(licenses.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Source license symlinks are unsupported: {path}")
        if path.is_file():
            required[prefix + "LICENSES/" + path.relative_to(licenses).as_posix()] = path
            license_count += 1
    if not license_count:
        raise ValueError("Source licenses directory is empty")
    collisions = (required.keys() & names) - replaced
    if collisions:
        fail(f"Release documents already exist: {sorted(collisions)!r}")
    for path in required.values():
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing regular source document: {path}")
    return required, replaced


def package_release(archive, output, source, platform, release_readme=None):
    archive, output, source = (Path(p).resolve() for p in (archive, output, source))
    if platform not in ("windows", "macos"):
        raise ValueError("platform must be windows or macos")
    if archive == output or output.exists():
        raise ValueError("Choose a new output path; input and existing outputs are never overwritten")
    directory = read_directory(archive)
    added, replaced = documents(source, platform, directory, release_readme)
    # The add-on is small license/document text only. Original media/models are
    # streamed raw, so no original filename flags, symlinks or modes are rewritten.
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as addon:
        for name, path in added.items():
            timestamp = time.localtime(path.stat().st_mtime)[:6]
            info = zipfile.ZipInfo(name, timestamp)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            addon.writestr(info, path.read_bytes())
    addon_bytes = memory.getvalue()
    # Parse through the same strict path, without a second permanent archive.
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".release-addon-", suffix=".zip", dir=output.parent, delete=False) as temp:
            temp.write(addon_bytes)
            addon_path = Path(temp.name)
        try:
            extra = read_directory(addon_path)
        finally:
            addon_path.unlink(missing_ok=True)
        kept = [member for member in directory.members if member.name not in replaced]
        count = len(kept) + len(extra.members)
        new_cd_offset = directory.offset + extra.offset
        if count >= MAX16 or new_cd_offset >= MAX32:
            fail("Result would require ZIP64")
        records = [member.record for member in kept]
        for member in extra.members:
            record = bytearray(member.record)
            offset = directory.offset + member.offset
            if offset >= MAX32:
                fail("Result local-header offset would require ZIP64")
            struct.pack_into("<I", record, 42, offset)
            records.append(bytes(record))
        cd_size = sum(map(len, records))
        if cd_size >= MAX32 or new_cd_offset + cd_size + EOCD.size + len(directory.comment) >= MAX32:
            fail("Result would exceed conservative ZIP32 size limit")
        with tempfile.NamedTemporaryFile(prefix=".release-package-", suffix=".zip", dir=output.parent, delete=False) as target:
            temp_name = Path(target.name)
            with archive.open("rb") as original:
                remaining = directory.offset
                while remaining:
                    chunk = original.read(min(4 * 1024 * 1024, remaining))
                    if not chunk:
                        fail("Input changed or was truncated during packaging")
                    target.write(chunk)
                    remaining -= len(chunk)
            target.write(addon_bytes[:extra.offset])
            for record in records:
                target.write(record)
            target.write(EOCD.pack(b"PK\x05\x06", 0, 0, count, count, cd_size, new_cd_offset, len(directory.comment)))
            target.write(directory.comment)
        # Confirm the resulting directory before publishing. No original member
        # is decoded/recompressed, including macOS symlinks and resource forks.
        final_directory = read_directory(temp_name)
        assert len(final_directory.members) == count
        with zipfile.ZipFile(temp_name, "r", metadata_encoding="utf-8") as check:
            assert len(check.infolist()) == count
            for name, path in added.items():
                if check.read(name) != path.read_bytes():
                    fail(f"Written document did not verify: {name}")
        if output.exists():
            raise FileExistsError(output)
        os.replace(temp_name, output)
        temp_name = None
        return {"input": str(archive), "output": str(output), "platform": platform,
                "original_local_bytes_preserved": directory.offset,
                "original_entries_preserved": len(kept), "replaced": sorted(replaced),
                "added": list(added), "entries": count, "bytes": output.stat().st_size}
    finally:
        if temp_name is not None:
            temp_name.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--platform", choices=("windows", "macos"), required=True)
    parser.add_argument("--release-readme", type=Path)
    args = parser.parse_args()
    result = package_release(args.input, args.output, args.source, args.platform, args.release_readme)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
