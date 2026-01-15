# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/features/oci/oci_label:oci_label.bzl", "oci_label")

oci_features = struct(
    label = oci_label,
)
