# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl/feature:defs.bzl", "feature")
load("//antlir/antlir2/bzl/image:defs.bzl", "image")
load("//antlir/antlir2/bzl/package:defs.bzl", "package")
load("//antlir/antlir2/package_managers/apk/rules:apk.bzl", "apk")

def test_apk(
        name: str,
        version: str,
        features: list = [],
        parent_layer: str | None = None,
        depends: list[str] = []) -> str:
    """Create a test APK package from a layer with given features."""
    target_name = "{}-{}".format(name, version)

    image.layer(
        name = target_name + "--layer",
        features = features,
        parent_layer = parent_layer,
        default_os = "wolfi",
        rootless = True,
    )

    package.apk(
        name = target_name + "--package",
        layer = ":" + target_name + "--layer",
        apk_name = name,
        version = version,
        depends = depends,
    )

    apk(
        name = target_name,
        apk = ":" + target_name + "--package",
        apk_name = name,
        version = version,
    )

    return ":" + target_name
