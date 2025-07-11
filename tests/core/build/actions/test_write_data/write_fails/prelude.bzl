# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is dual-licensed under either the MIT license found in the
# LICENSE-MIT file in the root directory of this source tree or the Apache
# License, Version 2.0 found in the LICENSE-APACHE file in the root directory
# of this source tree. You may select, at your option, one of the
# above-listed licenses.

def _fails_on_invalid_contents(ctx):
    out = ctx.actions.declare_output("out")
    ctx.actions.write(out, {})

fails_on_invalid_contents = rule(
    impl = _fails_on_invalid_contents,
    attrs = {},
)

def _fails_on_invalid_output(ctx):
    ctx.actions.write([], "")

fails_on_invalid_output = rule(
    impl = _fails_on_invalid_output,
    attrs = {},
)
