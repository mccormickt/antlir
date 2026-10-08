import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from build import member, repository_inputs, write_manifest
from snapshot import verify_package


class PackageIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.data = b"sigCONTROLpayload-data"
        self.package = {"name": "fixture"}
        for name, start, end in (
            ("signature", 0, 3),
            ("control", 3, 10),
            ("data", 10, 22),
        ):
            self.package[name] = {
                "range": f"bytes={start}-{end - 1}",
                "checksum": "sha256-"
                + base64.b64encode(
                    hashlib.sha256(self.data[start:end]).digest()
                ).decode(),
            }

    def test_complete_streams(self):
        verify_package(self.data, self.package)

    def test_unsigned_streams(self):
        package = copy.deepcopy(self.package)
        package["signature"]["range"] = ""
        package["control"]["range"] = "bytes=0-6"
        package["data"]["range"] = "bytes=7-18"
        verify_package(self.data[3:], package)

    def test_corruption_in_each_stream(self):
        for offset in (1, 5, 14):
            with self.subTest(offset=offset), self.assertRaisesRegex(
                ValueError, "checksum mismatch"
            ):
                changed = bytearray(self.data)
                changed[offset] ^= 1
                verify_package(changed, self.package)

    def test_truncation_and_trailing_bytes(self):
        for data in (self.data[:-1], self.data + b"extra"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                verify_package(data, self.package)

    def test_invalid_ranges(self):
        for value in ("bytes=4-9", "bytes=2-9", "bytes=3-2", "bytes=3-22", "3-9"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                package = copy.deepcopy(self.package)
                package["control"]["range"] = value
                verify_package(self.data, package)


class RepositoryIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "snapshot"
        repo = self.root / "packages/x86_64"
        repo.mkdir(parents=True)
        (repo / "APKINDEX.tar.gz").write_bytes(b"index")
        (repo / "fixture.apk").write_bytes(b"package")
        (self.root / "key.pub").write_bytes(b"public key")
        write_manifest(self.root, "x86_64", ["packages"], ["key.pub"])

    def test_valid_repository(self):
        self.assertEqual(
            repository_inputs([self.root], "x86_64"),
            ([str(self.root / "packages")], [str(self.root / "key.pub")]),
        )

    def test_wrong_architecture(self):
        with self.assertRaisesRegex(ValueError, "architecture"):
            repository_inputs([self.root], "aarch64")

    def test_changed_payload(self):
        (self.root / "packages/x86_64/fixture.apk").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            repository_inputs([self.root], "x86_64")

    def test_added_and_removed_files(self):
        extra = self.root / "extra.apk"
        extra.write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "files do not match"):
            repository_inputs([self.root], "x86_64")
        extra.unlink()
        (self.root / "key.pub").unlink()
        with self.assertRaisesRegex(ValueError, "files do not match"):
            repository_inputs([self.root], "x86_64")

    def test_manifest_requires_index_and_key(self):
        path = self.root / "repository.json"
        original = json.loads(path.read_text())
        for field, name in (("repositories", "absent"), ("keys", "absent.pub")):
            manifest = copy.deepcopy(original)
            manifest[field] = [name]
            path.write_text(json.dumps(manifest))
            with self.subTest(field=field), self.assertRaisesRegex(
                ValueError, "missing from manifest"
            ):
                repository_inputs([self.root], "x86_64")

    def test_path_escape(self):
        (self.root / "escape").symlink_to(self.root.parent)
        for name in ("../outside", "/etc/passwd", "escape/outside"):
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "escapes repository"
            ):
                member(self.root, name)


if __name__ == "__main__":
    unittest.main()
