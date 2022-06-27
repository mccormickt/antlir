# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/bzl:shape.bzl", "shape")
load(
    "//antlir/compiler/image/feature/buck2:image_source.shape.bzl",
    "image_source_t",
)

action_t = shape.enum("install", "remove_if_exists")

version_set_t = shape.union(
    # This string corresponds to `version_set_allow_all_versions`.
    str,
    shape.dict(str, str),
)

rpm_action_item_t = shape.shape(
    action = action_t,
    flavor_to_version_set = shape.dict(str, version_set_t),
    source = shape.field(image_source_t, optional = True),
    name = shape.field(str, optional = True),
    flavors_specified = shape.field(bool, default = False),
)
