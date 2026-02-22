# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# OSS stub: the Meta-internal tree has a distro-platform transition that maps
# the current OS configuration onto a dedicated distro platform. In OSS we use
# a no-op identity transition so `distro_platform_deps` resolve against the
# same platform as regular deps.

def _transition_impl(platform: PlatformInfo) -> PlatformInfo:
    return platform

def _rule_impl(ctx: AnalysisContext) -> list[Provider]:
    _ = ctx  # @unused
    return [DefaultInfo(), TransitionInfo(impl = _transition_impl)]

_to_current_distro_platform = rule(
    impl = _rule_impl,
    attrs = {},
    is_configuration_rule = True,
)

def to_current_distro_platform(**kwargs):
    _to_current_distro_platform(**kwargs)
