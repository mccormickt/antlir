# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# OSS stub for the Meta-internal build_appliance_from_dir rule.
#
# Upstream references this from antlir/antlir2/antlir2_packager/BUCK without
# an `@oss-disable` guard, which breaks OSS parsing because the internal
# `facebook/` subtree is not part of the public mirror. This shim forwards to
# the OSS `build_appliance` rule so existing call sites keep working.

load("//antlir/antlir2/bzl/image:build_appliance.bzl", "build_appliance")

def build_appliance_from_dir(*, name, dir, visibility = None, **kwargs):
    build_appliance(
        name = name,
        src = dir,
        visibility = visibility,
        **kwargs
    )
