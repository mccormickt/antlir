# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is dual-licensed under either the MIT license found in the
# LICENSE-MIT file in the root directory of this source tree or the Apache
# License, Version 2.0 found in the LICENSE-APACHE file in the root directory
# of this source tree. You may select, at your option, one of the
# above-listed licenses.

load("@fbsource//tools/build_defs/android/action_error_handler:android_di_error_handler.bzl", "android_di_error_handler")
load("@fbsource//tools/build_defs/android/action_error_handler:kotlin_error_handler.bzl", "kotlin_action_error_handler")

def kotlin_error_handler(ctx: ActionErrorCtx) -> list[ActionSubError]:
    categories = []

    categories += android_di_error_handler(ctx)
    categories += kotlin_action_error_handler(ctx)

    return categories
