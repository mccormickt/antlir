"""Offline APK recipe and image builds, independent of Antlir's Rust tools."""

ApkRepositoryInfo = provider(fields = {
    "directory": Artifact,
    "arch": str,
})

OciImageInfo = provider(fields = {"directory": Artifact, "arch": str})

def _command(argv):
    return " ".join(["'" + arg.replace("'", "'\"'\"'") + "'" for arg in argv])

def _image_config(ctx):
    if ctx.attrs.config != None:
        if ctx.attrs.packages != None or ctx.attrs.entrypoint != None or ctx.attrs.cmd != None or ctx.attrs.environment or ctx.attrs.user != None or ctx.attrs.workdir != None:
            fail("config cannot be combined with structured image attributes")
        return ctx.attrs.config
    if ctx.attrs.packages == None:
        fail("apk_image requires config or packages")
    config = {"contents": {"packages": ctx.attrs.packages}}
    if ctx.attrs.entrypoint != None:
        config["entrypoint"] = {"command": _command(ctx.attrs.entrypoint)}
    if ctx.attrs.cmd != None:
        config["cmd"] = _command(ctx.attrs.cmd)
    config["environment"] = ctx.attrs.environment
    if ctx.attrs.user != None:
        config["accounts"] = {"run-as": ctx.attrs.user}
    if ctx.attrs.workdir != None:
        config["work-dir"] = ctx.attrs.workdir
    return ctx.actions.write_json("image-config.json", config)

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
    config = _image_config(ctx) if kind == "image" else ctx.attrs.config
    source = ctx.actions.copied_dir("source", {"config.yaml": config} | ctx.attrs.srcs)
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
            "melange": ctx.attrs._melange[DefaultInfo].default_outputs[0] if kind == "recipe" else None,
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
    else:
        providers.append(OciImageInfo(directory = out, arch = ctx.attrs.arch))
    return providers

_common = {
    "srcs": attrs.dict(attrs.string(), attrs.source(allow_directory = True), default = {}),
    "repositories": attrs.list(attrs.dep(providers = [ApkRepositoryInfo])),
    "arch": attrs.enum(["x86_64", "aarch64"]),
    "source_date_epoch": attrs.int(default = 0),
    "_apko": attrs.exec_dep(default = "root//:apko"),
    "_builder": attrs.exec_dep(default = "root//:builder"),
    "_driver": attrs.source(default = "root//:build.py"),
}

apk_recipe = rule(
    impl = partial(_build_impl, kind = "recipe"),
    attrs = _common | {
        "config": attrs.source(),
        "signing_key": attrs.source(),
        "_melange": attrs.exec_dep(default = "root//:melange"),
    },
)

_runtime = {
    "entrypoint": attrs.option(attrs.list(attrs.string()), default = None),
    "cmd": attrs.option(attrs.list(attrs.string()), default = None),
    "environment": attrs.dict(attrs.string(), attrs.string(), default = {}),
}

apk_image = rule(
    impl = partial(_build_impl, kind = "image"),
    attrs = _common | _runtime | {
        "config": attrs.option(attrs.source(), default = None),
        "packages": attrs.option(attrs.list(attrs.string()), default = None),
        "user": attrs.option(attrs.string(), default = None),
        "workdir": attrs.option(attrs.string(), default = None),
    },
)

def _oci_impl(ctx):
    base = ctx.attrs.base[OciImageInfo]
    files = {}
    for path, src in ctx.attrs.files.items():
        parts = path.split("/")
        if not path.startswith("/") or any([p in ["", ".", ".."] or p.startswith(".wh.") for p in parts[1:]]):
            fail("file destinations must be canonical absolute non-whiteout paths: " + path)
        files[path[1:]] = src
    if any([p not in ctx.attrs.files for p in ctx.attrs.file_modes]):
        fail("file_modes must refer to declared files")
    source = ctx.actions.copied_dir("files", files)
    out = ctx.actions.declare_output("output", dir = True)
    spec_artifact = ctx.actions.declare_output("spec.json")
    spec = ctx.actions.write_json(spec_artifact, {
        "kind": "oci",
        "arch": base.arch,
        "base": base.directory,
        "source": source,
        "files": sorted(ctx.attrs.files.keys()),
        "file_modes": ctx.attrs.file_modes,
        "entrypoint": ctx.attrs.entrypoint,
        "cmd": ctx.attrs.cmd,
        "environment": ctx.attrs.environment,
        "epoch": ctx.attrs.source_date_epoch,
        "driver": ctx.attrs._driver,
        "oci_driver": ctx.attrs._oci_driver,
        "regctl": ctx.attrs._regctl[DefaultInfo].default_outputs[0],
    }, with_inputs = True)
    ctx.actions.run(
        cmd_args(ctx.attrs._builder[RunInfo], "sandbox", spec, out.as_output()),
        category = "oci_image",
        local_only = True,
    )
    return [
        DefaultInfo(out, sub_targets = {"spec": [DefaultInfo(spec_artifact, other_outputs = [spec])]}),
        OciImageInfo(directory = out, arch = base.arch),
    ]

oci_image = rule(
    impl = _oci_impl,
    attrs = _runtime | {
        "base": attrs.dep(providers = [OciImageInfo]),
        "files": attrs.dict(attrs.string(), attrs.source(), default = {}),
        "file_modes": attrs.dict(attrs.string(), attrs.int(), default = {}),
        "source_date_epoch": attrs.int(default = 0),
        "_builder": attrs.exec_dep(default = "root//:builder"),
        "_driver": attrs.source(default = "root//:build.py"),
        "_oci_driver": attrs.source(default = "root//:oci.py"),
        "_regctl": attrs.exec_dep(default = "root//:regctl"),
    },
)
