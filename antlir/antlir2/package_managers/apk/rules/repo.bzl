# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl:platform.bzl", "arch_select", "rule_with_default_target_platform")
load("//antlir/antlir2/bzl:types.bzl", "BuildApplianceInfo")
load(":apk.bzl", "ApkInfo")

ApkRepoInfo = provider(fields = [
    "repo_dir",
    "all_apks",
])

def _impl(ctx: AnalysisContext) -> list[Provider]:
    out = ctx.actions.declare_output("repo", dir = True)
    apk_files = [apk_dep[ApkInfo].raw_apk for apk_dep in ctx.attrs.apks]
    if not apk_files:
        fail("apk_repo {} has no apks; the rule needs at least one entry".format(ctx.label))
    arch = ctx.attrs.arch

    ctx.actions.run(
        cmd_args(
            ctx.attrs._indexer[RunInfo],
            "--rootless",
            cmd_args(ctx.attrs.build_appliance[BuildApplianceInfo].dir, format = "--build-appliance={}"),
            cmd_args(out.as_output(), format = "--out-dir={}"),
            cmd_args(arch, format = "--arch={}"),
            [cmd_args(apk, format = "--apk={}") for apk in apk_files],
            cmd_args(ctx.attrs.signing_key, format = "--signing-key={}") if ctx.attrs.signing_key else cmd_args(),
        ),
        category = "apk_repo",
    )

    return [
        ApkRepoInfo(
            repo_dir = out,
            all_apks = ctx.attrs.apks,
        ),
        DefaultInfo(out),
    ]

_apk_repo = rule(
    impl = _impl,
    attrs = {
        "apks": attrs.list(attrs.dep(providers = [ApkInfo])),
        "arch": attrs.string(
            default = arch_select(x86_64 = "x86_64", aarch64 = "aarch64"),
            doc = """
                APK index arch subdirectory and `apk index --arch` value.
                Defaults to the buck target arch so apk_repo follows the
                same x86_64/aarch64 selection as `package.apk`.
            """,
        ),
        "build_appliance": attrs.exec_dep(
            providers = [BuildApplianceInfo],
            default = "//flavor/wolfi:build-appliance",
            doc = """
                Build appliance used to run `apk index` hermetically.
                Must contain `apk-tools`. Defaults to the wolfi BA.
            """,
        ),
        "signing_key": attrs.option(
            attrs.source(),
            default = None,
            doc = """
                Optional RSA private key to sign the APKINDEX.tar.gz with.
                When set, the indexer invokes `apk index --sign-key`. The
                matching public key must be installed under
                `/etc/apk/keys/` in any layer that consumes this repo.
            """,
        ),
        "_indexer": attrs.exec_dep(
            default = "//antlir/antlir2/package_managers/apk/rules:indexer",
        ),
    },
)

apk_repo = rule_with_default_target_platform(_apk_repo)
