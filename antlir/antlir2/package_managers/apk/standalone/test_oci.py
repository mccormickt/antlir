import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest

from oci import file_layer, read_blob


class FileLayerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "tool").write_bytes(b"executable bytes")
        (self.source / "tool").chmod(0o711)
        (self.source / "data").write_bytes(b"different data")
        (self.source / "data").chmod(0o600)

    def test_reproducible_metadata_and_contents(self):
        first, second = self.root / "first.tar", self.root / "second.tar"
        records = file_layer(
            self.source, ["/tool", "/data"], {"/data": 0o640}, 123, first
        )
        os.utime(self.source / "tool", (456, 789))
        file_layer(self.source, ["/data", "/tool"], {"/data": 0o640}, 123, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first) as archive:
            self.assertEqual(archive.getnames(), ["data", "tool"])
            for name, expected, mode in [
                ("data", b"different data", 0o640),
                ("tool", b"executable bytes", 0o755),
            ]:
                entry = archive.getmember(name)
                self.assertEqual(
                    (
                        entry.uid,
                        entry.gid,
                        entry.uname,
                        entry.gname,
                        entry.mtime,
                        entry.mode,
                    ),
                    (0, 0, "", "", 123, mode),
                )
                self.assertEqual(archive.extractfile(name).read(), expected)
                self.assertEqual(
                    records["/" + name]["sha256"], hashlib.sha256(expected).hexdigest()
                )

    def test_changed_bytes_and_mode_change_layer(self):
        first, second = self.root / "first.tar", self.root / "second.tar"
        file_layer(self.source, ["/tool"], {}, 0, first)
        file_layer(self.source, ["/tool"], {"/tool": 0o644}, 0, second)
        self.assertNotEqual(first.read_bytes(), second.read_bytes())
        (self.source / "tool").write_bytes(b"changed bytes")
        file_layer(self.source, ["/tool"], {}, 0, second)
        self.assertNotEqual(first.read_bytes(), second.read_bytes())

    def test_invalid_paths(self):
        for name in [
            "tool",
            "/",
            "/../tool",
            "/a/../tool",
            "//tool",
            "/./tool",
            "/tool/",
            "/.wh.tool",
            "/dir/.wh..wh..opq",
        ]:
            with self.subTest(name=name), self.assertRaisesRegex(
                ValueError, "destination"
            ):
                file_layer(self.source, [name], {}, 0, self.root / "bad.tar")

    def test_invalid_modes_and_file_parent(self):
        for modes in [{"/tool": -1}, {"/tool": 0o4755}, {"/absent": 0o755}]:
            with self.subTest(modes=modes), self.assertRaises(ValueError):
                file_layer(self.source, ["/tool"], modes, 0, self.root / "bad.tar")
        with self.assertRaisesRegex(ValueError, "file parent"):
            file_layer(
                self.source, ["/tool", "/tool/child"], {}, 0, self.root / "bad.tar"
            )

    def test_source_escape_and_directory_rejected(self):
        (self.source / "escape").symlink_to(self.root / "outside")
        (self.root / "outside").write_text("outside")
        (self.source / "directory").mkdir()
        for name in ["/escape", "/directory"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                file_layer(self.source, [name], {}, 0, self.root / "bad.tar")


class DescriptorTest(unittest.TestCase):
    def test_descriptor_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b'{"architecture":"amd64"}'
            value = hashlib.sha256(data).hexdigest()
            path = root / "blobs/sha256" / value
            path.parent.mkdir(parents=True)
            path.write_bytes(data)
            descriptor = {"digest": "sha256:" + value, "size": len(data)}
            self.assertEqual(read_blob(root, descriptor), json.loads(data))
            with self.assertRaises(ValueError):
                read_blob(root, descriptor | {"size": len(data) + 1})
            with self.assertRaises(ValueError):
                read_blob(root, descriptor | {"digest": "sha256:../../outside"})
            path.write_bytes(b"corrupt")
            with self.assertRaises(ValueError):
                read_blob(root, descriptor)


if __name__ == "__main__":
    unittest.main()
