# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl:platform.bzl", "rule_with_default_target_platform")
load("//antlir/antlir2/bzl:types.bzl", "FlavorApkInfo", "FlavorDnfInfo", "FlavorInfo")
# @oss-disable[end= ]: load("//antlir/antlir2/bzl/flavor/facebook:rou.bzl", fb_extract_rou = "extract_rou")
load("//antlir/antlir2/package_managers/dnf/rules:repo.bzl", "RepoSetInfo")

_flavor_attrs = {
    "default_apk_repo_urls": attrs.list(
        attrs.string(),
        default = [],
        doc = "Default APK repository URLs for apk-based distros (e.g. Wolfi)",
    ),
    "default_apk_signing_keys": attrs.list(
        attrs.source(),
        default = [],
        doc = "Default APK signing key files for apk-based distros",
    ),
    "default_dnf_excluded_rpms": attrs.list(
        attrs.string(),
        default = [],
    ),
    "default_dnf_extra_repo_set": attrs.option(
        attrs.dep(providers = [RepoSetInfo]),
        default = None,
    ),
    "default_dnf_repo_set": attrs.option(
        attrs.dep(providers = [RepoSetInfo]),
        default = None,
    ),
    "default_dnf_versionlock": attrs.option(
        attrs.source(),
        default = None,
    ),
    "rpm_reflink_flavor": attrs.option(attrs.string(), default = None),
}

def _impl(ctx: AnalysisContext) -> list[Provider]:
    dnf_info = None
    if ctx.attrs.default_dnf_repo_set:
        dnf_info = FlavorDnfInfo(
            default_excluded_rpms = ctx.attrs.default_dnf_excluded_rpms,
            default_extra_repo_set = ctx.attrs.default_dnf_extra_repo_set,
            default_repo_set = ctx.attrs.default_dnf_repo_set,
            default_versionlock = ctx.attrs.default_dnf_versionlock,
            reflink_flavor = ctx.attrs.rpm_reflink_flavor,
        )

    apk_info = None
    if ctx.attrs.default_apk_repo_urls:
        apk_info = FlavorApkInfo(
            default_repo_urls = ctx.attrs.default_apk_repo_urls,
            default_signing_keys = ctx.attrs.default_apk_signing_keys,
        )

    return [
        FlavorInfo(
            apk_info = apk_info,
            dnf_info = dnf_info,
            label = ctx.label,
        ),
        DefaultInfo(
            sub_targets = {
                "default_versionlock": [DefaultInfo(ctx.attrs.default_dnf_versionlock)],
            }
        ),
    # @oss-disable[end= ]: ] + (fb_extract_rou(ctx.attrs.default_dnf_repo_set[RepoSetInfo]) if ctx.attrs.default_dnf_repo_set else [])
    ] # @oss-enable

_flavor = rule(
    impl = _impl,
    attrs = _flavor_attrs,
)

_flavor_macro = rule_with_default_target_platform(_flavor)

def flavor(**kwargs):
    # TODO(T224478114) The flavor depends on the build_appliance as an exec_dep,
    # which is mostly used in local_only=True actions, so put that in
    # exec_compatible_with too to force it to resolve the same way (or at least
    # to the same cpu architecture)
    kwargs.setdefault("exec_compatible_with", ["prelude//platforms:may_run_local"])
    return _flavor_macro(**kwargs)
