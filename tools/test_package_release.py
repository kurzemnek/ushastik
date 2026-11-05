"""Preservation/regression checks for the raw ZIP32 document updater."""
import hashlib
from pathlib import Path
import stat
import struct
import tempfile
import unittest
import warnings
import zipfile

from tools.package_release import CD, EOCD, LOCAL, UnsupportedArchive, package_release, read_directory


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        (self.source / "licenses" / "nested").mkdir(parents=True)
        (self.source / "macos").mkdir()
        for path, text in {
            "LICENSE": "MIT\nCopyright 2026\n", "THIRD_PARTY_NOTICES.md": "# Notices\n",
            "ПРОЧТИ МЕНЯ.txt": "Windows: распакуйте всё\n",
            "macos/ПРОЧТИ МЕНЯ.txt": "Mac: откройте приложение\n",
            "licenses/FFmpeg.txt": "LGPL text\n", "licenses/nested/Python.txt": "PSF text\n",
        }.items():
            (self.source / path).write_text(text, encoding="utf-8")
        self.archive, self.output = self.root / "in.zip", self.root / "out.zip"

    def member(self, archive, name, payload, mode=0o100755):
        info = zipfile.ZipInfo(name, (2026, 11, 5, 12, 34, 56))
        info.create_system = 3
        info.external_attr = mode << 16
        info.internal_attr = 1
        info.comment = b"original-member-comment"
        info.extra = struct.pack("<HH", 0xCAFE, 4) + b"test"
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, payload)

    def snapshot(self, path):
        directory = read_directory(path)
        with zipfile.ZipFile(path, metadata_encoding="utf-8") as archive:
            return directory, {info.filename: {
                "metadata": {slot: getattr(info, slot) for slot in zipfile.ZipInfo.__slots__
                             if slot not in ("_raw_time", "_end_offset") and hasattr(info, slot)},
                "sha256": digest(archive.read(info.filename)),
            } for info in archive.infolist()}

    def test_macos_preserves_unflagged_utf8_symlink_modes_metadata_and_local_bytes(self):
        prefix = "Ушастик.app/Contents/"
        with zipfile.ZipFile(self.archive, "w") as archive:
            archive.comment = b"archive comment preserved verbatim"
            self.member(archive, prefix + "MacOS/Ushastik", b"native executable\x00")
            self.member(archive, prefix + "Frameworks/Current", b"Versions/A", stat.S_IFLNK | 0o755)
            self.member(archive, "__MACOSX/._Ушастик.app", b"resource fork")
        # ditto can emit UTF-8 names while leaving general-purpose bit 11 unset.
        raw = bytearray(self.archive.read_bytes())
        for member in read_directory(self.archive).members:
            struct.pack_into("<H", raw, member.offset + 6,
                             struct.unpack_from("<H", raw, member.offset + 6)[0] & ~0x800)
        position = read_directory(self.archive).offset
        while raw[position:position + 4] == b"PK\x01\x02":
            values = CD.unpack_from(raw, position)
            struct.pack_into("<H", raw, position + 8, values[3] & ~0x800)
            position += CD.size + values[10] + values[11] + values[12]
        self.archive.write_bytes(raw)
        before_dir, before = self.snapshot(self.archive)
        original_hash = digest(self.archive.read_bytes())
        result = package_release(self.archive, self.output, self.source, "macos")
        after_dir, after = self.snapshot(self.output)
        self.assertEqual(digest(self.archive.read_bytes()), original_hash)
        self.assertEqual(self.output.read_bytes()[:before_dir.offset], raw[:before_dir.offset])
        self.assertEqual(after_dir.comment, before_dir.comment)
        for member in before_dir.members:
            self.assertEqual(member.record, next(x.record for x in after_dir.members if x.name == member.name))
            self.assertEqual(before[member.name], after[member.name])
        self.assertEqual(stat.S_IFMT(after[prefix + "Frameworks/Current"]["metadata"]["external_attr"] >> 16), stat.S_IFLNK)
        self.assertIn("LICENSES/nested/Python.txt", after)
        self.assertNotIn(prefix + "LICENSE", after)
        self.assertEqual(result["replaced"], [])
        with zipfile.ZipFile(self.output, metadata_encoding="utf-8") as archive:
            self.assertIsNone(archive.testzip())

    def test_windows_replaces_only_old_readme_without_duplicate_central_member(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            self.member(archive, "Ушастик/Ushastik.exe", b"windows executable")
            self.member(archive, "Ушастик/_internal/model/model.bin", b"model content")
            self.member(archive, "Ушастик/ПРОЧТИ МЕНЯ.txt", b"old instructions")
        before_dir, before = self.snapshot(self.archive)
        raw = self.archive.read_bytes()
        package_release(self.archive, self.output, self.source, "windows")
        after_dir, after = self.snapshot(self.output)
        self.assertEqual(self.output.read_bytes()[:before_dir.offset], raw[:before_dir.offset])
        for name in ("Ушастик/Ushastik.exe", "Ушастик/_internal/model/model.bin"):
            self.assertEqual(before[name], after[name])
            self.assertEqual(next(m.record for m in before_dir.members if m.name == name),
                             next(m.record for m in after_dir.members if m.name == name))
        with zipfile.ZipFile(self.output, metadata_encoding="utf-8") as archive:
            name = "Ушастик/ПРОЧТИ МЕНЯ.txt"
            self.assertEqual(archive.namelist().count(name), 1)
            self.assertEqual(archive.read(name), (self.source / "ПРОЧТИ МЕНЯ.txt").read_bytes())
            self.assertIn("Ушастик/LICENSES/FFmpeg.txt", archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_rejects_zip64_bad_eof_duplicates_and_existing_output(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            self.member(archive, "Ушастик/Ushastik.exe", b"exe")
        original = self.archive.read_bytes()
        cases = {"trailing": original + b"junk", "truncated": original[:-1]}
        zip64 = bytearray(original)
        end = zip64.rfind(b"PK\x05\x06")
        struct.pack_into("<H", zip64, end + 10, 0xFFFF)
        cases["zip64"] = bytes(zip64)
        for name, raw in cases.items():
            with self.subTest(name=name):
                self.archive.write_bytes(raw)
                with self.assertRaises(UnsupportedArchive):
                    package_release(self.archive, self.output, self.source, "windows")
                self.assertFalse(self.output.exists())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "w") as archive:
                self.member(archive, "Ушастик/Ushastik.exe", b"one")
                self.member(archive, "Ушастик/Ushastik.exe", b"two")
        with self.assertRaisesRegex(UnsupportedArchive, "duplicate"):
            package_release(self.archive, self.output, self.source, "windows")
        self.archive.write_bytes(original)
        self.output.write_bytes(b"keep me")
        with self.assertRaises(ValueError):
            package_release(self.archive, self.output, self.source, "windows")
        self.assertEqual(self.output.read_bytes(), b"keep me")


if __name__ == "__main__":
    unittest.main()
