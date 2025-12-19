# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import importlib.resources
import json
import unittest


class TestSupplements(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()

    def test_supplements(self) -> None:
        supplements = json.loads(
            importlib.resources.read_text(__package__, "supplements.json")
        )
        self.assertEqual(
            supplements,
            {
                "msgs": ["parent", "child"],
                "planner_msgs": ["parent", "child"],
            },
        )
