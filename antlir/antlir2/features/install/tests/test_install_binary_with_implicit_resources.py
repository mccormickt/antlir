#!/usr/bin/env fbpython
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import subprocess
import unittest
from pathlib import Path


class TestInstallBinaryWithImplicitResources(unittest.TestCase):
    def test_implicit_resources(self):
        binpath = Path("/binary-with-resources")
        self.assertTrue(binpath.exists())
        self.assertTrue(binpath.is_file(), f"{binpath} is not a file")
        self.assertFalse(binpath.is_symlink(), f"{binpath} is a symlink")
        self.assertEqual(
            subprocess.check_output(["/binary-with-resources"], text=True),
            "Binary resource content: Hello from test resource!\n",
        )

        resources_path = Path("/binary-with-resources.resources.json")
        self.assertTrue(resources_path.exists())
        self.assertTrue(resources_path.is_file(), f"{resources_path} is not a file")
        self.assertFalse(resources_path.is_symlink(), f"{resources_path} is a symlink")
