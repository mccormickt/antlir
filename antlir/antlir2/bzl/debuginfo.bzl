# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("@prelude//cxx:cxx_toolchain_types.bzl", "CxxToolchainInfo")
load("//antlir/buck2/bzl:ensure_single_output.bzl", "ensure_single_output")

SplitBinaryInfo = provider(fields = [
    "stripped",
    "debuginfo",
    "metadata",
    "dwp",
])

def _split_binary_impl(ctx: AnalysisContext) -> list[Provider]:
    objcopy = ctx.attrs.objcopy[RunInfo] if ctx.attrs.objcopy else ctx.attrs.cxx_toolchain[CxxToolchainInfo].binary_utilities_info.objcopy

    src = ensure_single_output(ctx.attrs.src)

    src_dwp = None
    maybe_dwp = ctx.attrs.src[DefaultInfo].sub_targets.get("dwp")
    if maybe_dwp:
        src_dwp = ensure_single_output(maybe_dwp[DefaultInfo])

    stripped = ctx.actions.declare_output("stripped")
    debuginfo = ctx.actions.declare_output("debuginfo")
    dwp_out = ctx.actions.declare_output("dwp")
    metadata = ctx.actions.declare_output("metadata.json")

    # objcopy needs a temporary file that it can write to. use a buck2 output
    # artifact so that it doesn't try to put it somewhere it doesn't have access
    # to write
    objcopy_tmp = ctx.actions.declare_output("objcopy_tmp")

    ctx.actions.run(
        cmd_args(
            ctx.attrs.debuginfo_splitter[RunInfo],
            cmd_args(objcopy, format = "--objcopy={}"),
            cmd_args(src, format = "--binary={}"),
            (cmd_args(src_dwp, format = "--binary-dwp={}") if src_dwp else []),
            cmd_args(stripped.as_output(), format = "--stripped={}"),
            cmd_args(debuginfo.as_output(), format = "--debuginfo={}"),
            cmd_args(metadata.as_output(), format = "--metadata={}"),
            cmd_args(dwp_out.as_output(), format = "--dwp={}"),
            cmd_args(objcopy_tmp.as_output(), format = "--objcopy-tmp={}"),
        ),
        category = "split",
    )

    return [
        DefaultInfo(sub_targets = {
            "debuginfo": [DefaultInfo(debuginfo)],
            "dwp": [DefaultInfo(dwp_out)],
            "metadata": [DefaultInfo(metadata)],
            "stripped": [DefaultInfo(stripped)],
        }),
        SplitBinaryInfo(
            stripped = stripped,
            debuginfo = debuginfo,
            metadata = metadata,
            dwp = dwp_out,
        ),
    ]

split_binary = anon_rule(
    impl = _split_binary_impl,
    attrs = {
        "cxx_toolchain": attrs.option(attrs.toolchain_dep(default = "toolchains//:cxx", providers = [CxxToolchainInfo]), default = None),
        "debuginfo_splitter": attrs.exec_dep(default = "antlir//antlir/antlir2/tools:debuginfo-splitter"),
        "objcopy": attrs.option(attrs.exec_dep(), default = None),
        "src": attrs.dep(),
    },
    artifact_promise_mappings = {
        "debuginfo": lambda x: x[SplitBinaryInfo].debuginfo,
        "dwp": lambda x: x[SplitBinaryInfo].dwp,
        "metadata": lambda x: x[SplitBinaryInfo].metadata,
        "src": lambda x: x[SplitBinaryInfo].stripped,
    },
)

def split_binary_anon(
        *,
        ctx: AnalysisContext,
        src: Dependency,
        objcopy: Dependency,
        debuginfo_splitter: Dependency) -> AnonTarget:
    return ctx.actions.anon_target(split_binary, {
        "debuginfo_splitter": debuginfo_splitter,
        "name": "debuginfo//" + src.label.package + ":" + src.label.name,
        "objcopy": objcopy,
        "src": src,
    })
