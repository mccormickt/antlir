import base64
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from build import member, repository_inputs, write_manifest
from snapshot import verify_package


class PackageIntegrityTest(unittest.TestCase):
    @staticmethod
    def archive(entries):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, data in entries:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return gzip.compress(output.getvalue(), mtime=0)

    def setUp(self):
        self.streams = {
            "signature": self.archive([(".SIGN.RSA.fixture.rsa.pub", b"signature")]),
            "data": self.archive([("usr/bin/fixture", b"original payload")]),
        }
        self.datahash = hashlib.sha256(self.streams["data"]).hexdigest()
        self.pkginfo = f"pkgname = fixture\ndatahash = {self.datahash}\n".encode()
        self.streams["control"] = self.archive([(".PKGINFO", self.pkginfo)])
        self.checksum = (
            "Q1"
            + base64.b64encode(hashlib.sha1(self.streams["control"]).digest()).decode()
        )
        self.data, self.package = self.lock()

    def lock(self):
        data = b""
        package = {"name": "fixture", "checksum": self.checksum}
        for name in ("signature", "control", "data"):
            stream = self.streams[name]
            start = len(data)
            data += stream
            algorithm = "sha256" if name == "data" else "sha1"
            package[name] = {
                "range": f"bytes={start}-{len(data) - 1}" if stream else "",
                "checksum": algorithm
                + "-"
                + base64.b64encode(hashlib.new(algorithm, stream).digest()).decode(),
            }
        return data, package

    def test_complete_streams(self):
        verify_package(self.data, self.package)

    def test_unsigned_streams(self):
        self.streams["signature"] = b""
        verify_package(*self.lock())

    def test_payload_not_authenticated_by_lock(self):
        self.streams["data"] = self.archive([("usr/bin/fixture", b"malicious payload")])
        with self.assertRaisesRegex(ValueError, "authenticated datahash mismatch"):
            verify_package(*self.lock())

    def test_control_not_authenticated_by_stream_hash(self):
        self.streams["control"] = self.archive(
            [(".PKGINFO", self.pkginfo + b"pkgver = 2\n")]
        )
        with self.assertRaisesRegex(ValueError, "index control checksum mismatch"):
            verify_package(*self.lock())

    def test_missing_duplicate_or_malformed_metadata(self):
        for entries in (
            [],
            [(".PKGINFO", self.pkginfo), ("./.PKGINFO", self.pkginfo)],
            [(".PKGINFO", b"pkgname = fixture\n")],
            [(".PKGINFO", self.pkginfo + f"datahash = {self.datahash}\n".encode())],
            [(".PKGINFO", b"datahash = \n")],
            [(".PKGINFO", b"datahash = " + b"x" * 64 + b"\n")],
        ):
            with self.subTest(entries=entries), self.assertRaisesRegex(
                ValueError, "PKGINFO"
            ):
                self.streams["control"] = self.archive(entries)
                self.checksum = (
                    "Q1"
                    + base64.b64encode(
                        hashlib.sha1(self.streams["control"]).digest()
                    ).decode()
                )
                verify_package(*self.lock())

    def test_corruption_in_each_stream(self):
        for offset in (
            1,
            len(self.streams["signature"]) + 1,
            len(self.streams["signature"]) + len(self.streams["control"]) + 1,
        ):
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
