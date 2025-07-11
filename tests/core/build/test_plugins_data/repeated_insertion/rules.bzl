# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is dual-licensed under either the MIT license found in the
# LICENSE-MIT file in the root directory of this source tree or the Apache
# License, Version 2.0 found in the LICENSE-APACHE file in the root directory
# of this source tree. You may select, at your option, one of the
# above-listed licenses.

def _nop_impl(_ctx):
    return [DefaultInfo()]

Plugin = plugins.kind()

plugin = rule(
    impl = _nop_impl,
    attrs = {},
)

introduce_plugin = rule(
    impl = _nop_impl,
    attrs = {
        "plugin": attrs.plugin_dep(kind = Plugin),
    },
)

different_deps = rule(
    impl = _nop_impl,
    attrs = {
        "direct": attrs.plugin_dep(kind = Plugin),
        "no_propagate": attrs.dep(pulls_plugins = [Plugin]),
    },
)

alias = rule(
    impl = _nop_impl,
    attrs = {
        "actual": attrs.dep(pulls_plugins = [Plugin]),
    },
)
