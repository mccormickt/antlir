# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl:platform.bzl", "arch_select")
load("//antlir/antlir2/bzl/image:cfg.bzl", "cfg_attrs")
load(":attrs.bzl", "default_attrs")
load(":cfg.bzl", "package_cfg")
load(":macro.bzl", "package_macro")

# `package.apko` produces an OCI archive from an apko YAML config.
#
# Unlike the rest of the package rules this one does NOT consume an
# image.layer. apko resolves and downloads all packages itself from the
# repositories listed in the config, so the input is just the YAML plus
# the apko binary.
#
# Use this when you want a hermetic Wolfi/Alpine OCI image straight from a
# declarative package list. For layered multi-feature images built via
# antlir2's feature pipeline, use `package.oci` instead.

def _impl(ctx: AnalysisContext) -> list[Provider]:
    output_name = ctx.attrs.out or ctx.label.name
    out = ctx.actions.declare_output(output_name, has_content_based_path = False)
    spec = ctx.actions.write_json(
        "spec.json",
        {"apko": {
            "config": ctx.attrs.config,
            "arch": ctx.attrs.arch,
            "tag": ctx.attrs.tag,
            "_apko": ctx.attrs._apko[RunInfo],
        }},
        with_inputs = True,
    )
    ctx.actions.run(
        cmd_args(
            ctx.attrs._antlir2_packager[RunInfo],
            cmd_args(spec, format = "--spec={}"),
            cmd_args(ctx.attrs._working_format, format = "--working-format={}"),
            cmd_args(out.as_output(), format = "--out={}"),
            "--rootless" if ctx.attrs._rootless else cmd_args(),
        ),
        category = "antlir2_package",
        identifier = "apko",
        # apko runs entirely in userspace but fetches from the internet,
        # so defer to whatever the working format prefers.
        local_only = ctx.attrs._working_format == "btrfs",
    )
    return [DefaultInfo(out)]

_apko_attrs = {
    "arch": attrs.enum(
        ["amd64", "arm64"],
        default = arch_select(x86_64 = "amd64", aarch64 = "arm64"),
        doc = "apko target architecture (amd64 or arm64).",
    ),
    "config": attrs.source(doc = "apko YAML config file"),
    "tag": attrs.string(
        default = "image:latest",
        doc = "OCI image reference written into the archive",
    ),
    "_apko": attrs.exec_dep(
        default = "antlir//antlir/antlir2/package_managers/apk/tools:apko",
    ),
}

# apko is layer-less, so we build our own attrs set rather than inheriting
# common_attrs (which requires a layer dep).
_apko_rule = rule(
    impl = _impl,
    attrs = _apko_attrs | default_attrs | {
        "labels": attrs.list(attrs.string(), default = []),
    } | cfg_attrs(),
    cfg = package_cfg,
)

apko = package_macro(_apko_rule, always_rootless = True)
