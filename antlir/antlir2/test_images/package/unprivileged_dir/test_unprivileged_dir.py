# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict

import importlib.resources
import os
import os.path
import stat
from pathlib import Path

from later.unittest import TestCase


class TestUnprivilegedDir(TestCase):
    def setUp(self) -> None:
        self.maxDiff = None

    def test_standard(self) -> None:
        # python_unittest resources are often (but not always and not entirely)
        # packaged up as a symlink tree. This makes looking at the actual
        # metadata of the underlying dir really hard...
        # So, we can compromise and still write a useful test by ensuring:
        # 1) all file/dir names we expect do exist
        # 2) an executable file is executable
        # 3) a symlink has the right target
        # 4) a file has the correct contents
        # 5) a large file has the correct number of bytes
        # 6) all files are owned by the unprivileged user
        path = Path("/unprivileged_dir")
        uid = os.getuid()
        gid = os.getgid()

        root = Path(os.path.realpath(path))
        files = set()
        dirs = set()
        stats = {}
        for dirpath, dirnames, filenames in root.walk():
            for dirname in dirnames:
                item = dirpath / dirname
                dirs.add(str(item.relative_to(root)))
                try:
                    stat_info = item.stat()
                except FileNotFoundError:
                    stat_info = item.lstat()
                stats[str(item.relative_to(root))] = stat_info
            for filename in filenames:
                item = dirpath / filename
                files.add(str(item.relative_to(root)))
                try:
                    stat_info = item.stat()
                except FileNotFoundError:
                    stat_info = item.lstat()
                stats[str(item.relative_to(root))] = stat_info

        # 1) all file/dir names we expect do exist
        self.assertEqual({"hardlink", "default-dir", ".meta"}, dirs)
        self.assertEqual(
            {
                "only-readable-by-root",
                "default-dir/relative-file-symlink",
                "absolute-dir-symlink",
                ".meta/target",
                "i-have-xattrs",
                "i-have-caps",
                "i-am-owned-by-nonstandard",
                "absolute-file-symlink",
                "antlir2-large-file-256M",
                "hardlink/hello",
                "default-dir/executable",
                "hardlink/aloha",
                "relative-dir-symlink",
            },
            files,
        )

        # 2) an executable file is executable
        self.assertEqual(
            "-r-xr-xr-x",
            stat.filemode(stats["default-dir/executable"].st_mode),
            "executable is not actually executable",
        )

        # 3) a symlink has the right target
        try:
            target = (root / "absolute-file-symlink").resolve()
            self.assertEqual("/default-dir/executable", str(target))
        except FileNotFoundError as e:
            # packaging issues are weird...
            self.assertEqual("/default-dir/executable", e.filename)

        # I can't figure out a sane way to check a relative symlink here...

        # 4) a file has the correct contents
        with open(root / "hardlink/hello") as f:
            self.assertEqual("Hello world\n", f.read())

        # 5) a large file has the correct number of bytes
        self.assertEqual(
            268435513,
            stats["antlir2-large-file-256M"].st_size,
            "large file was not copied fully",
        )

        # 6) all files are owned by the unprivileged user
        for relpath, stat_info in stats.items():
            self.assertEqual(uid, stat_info.st_uid, f"{relpath} owned by wrong user")
            self.assertEqual(gid, stat_info.st_gid, f"{relpath} owned by wrong group")
