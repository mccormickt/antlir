"""Offline APK recipe and image builds, independent of Antlir's Rust tools."""

ApkRepositoryInfo = provider(fields = {
    "directory": Artifact,
    "arch": str,
})

def _snapshot_impl(ctx: AnalysisContext) -> list[Provider]:
    return [
        DefaultInfo(ctx.attrs.directory),
        ApkRepositoryInfo(directory = ctx.attrs.directory, arch = ctx.attrs.arch),
    ]

apk_snapshot = rule(
    impl = _snapshot_impl,
    attrs = {
        "directory": attrs.source(allow_directory = True),
        "arch": attrs.enum(["x86_64", "aarch64"]),
    },
)

def _build_impl(ctx: AnalysisContext, kind: str) -> list[Provider]:
    for repo in ctx.attrs.repositories:
        if repo[ApkRepositoryInfo].arch != ctx.attrs.arch:
            fail("repository {} has the wrong architecture".format(repo.label))
    if "config.yaml" in ctx.attrs.srcs:
        fail("config.yaml is reserved for the config attribute")
    source = ctx.actions.copied_dir("source", {"config.yaml": ctx.attrs.config} | ctx.attrs.srcs)
    out = ctx.actions.declare_output("output", dir = True)
    spec_artifact = ctx.actions.declare_output("spec.json")
    spec = ctx.actions.write_json(
        spec_artifact,
        {
            "kind": kind,
            "arch": ctx.attrs.arch,
            "source": source,
            "repositories": [r[ApkRepositoryInfo].directory for r in ctx.attrs.repositories],
            "signing_key": ctx.attrs.signing_key if kind == "recipe" else None,
            "apko": ctx.attrs._apko[DefaultInfo].default_outputs[0],
            "melange": ctx.attrs._melange[DefaultInfo].default_outputs[0],
            "driver": ctx.attrs._driver,
            "epoch": ctx.attrs.source_date_epoch,
        },
        with_inputs = True,
    )
    ctx.actions.run(
        cmd_args(ctx.attrs._builder[RunInfo], "sandbox", spec, out.as_output()),
        category = "apk_" + kind,
        local_only = True,
    )
    providers = [DefaultInfo(out, sub_targets = {"spec": [DefaultInfo(spec_artifact, other_outputs = [spec])]})]
    if kind == "recipe":
        providers.append(ApkRepositoryInfo(directory = out, arch = ctx.attrs.arch))
    return providers

_common = {
    "config": attrs.source(),
    "srcs": attrs.dict(attrs.string(), attrs.source(allow_directory = True), default = {}),
    "repositories": attrs.list(attrs.dep(providers = [ApkRepositoryInfo])),
    "arch": attrs.enum(["x86_64", "aarch64"]),
    "source_date_epoch": attrs.int(default = 0),
    "_apko": attrs.exec_dep(default = "root//:apko"),
    "_melange": attrs.exec_dep(default = "root//:melange"),
    "_builder": attrs.exec_dep(default = "root//:builder"),
    "_driver": attrs.source(default = "root//:build.py"),
}

apk_recipe = rule(
    impl = partial(_build_impl, kind = "recipe"),
    attrs = _common | {"signing_key": attrs.source()},
)

apk_image = rule(
    impl = partial(_build_impl, kind = "image"),
    attrs = _common,
)
