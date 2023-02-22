# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/buck2/bzl:layer_info.bzl", Antlir1LayerInfo = "LayerInfo")
load("//antlir/buck2/bzl/feature:feature.bzl", "FeatureInfo", "feature")
load("//antlir/bzl:flatten.bzl", "flatten")
load("//antlir/rpm/dnf2buck:repo.bzl", "RepoSetInfo")
load(":antlir2_dnf.bzl", "compiler_plan_to_local_repos", "repodata_only_local_repos")

LayerInfo = provider(fields = {
    "depgraph": "JSON-serialized depgraph",
    "parent": "LayerInfo from parent_layer, if any",
    "subvol_symlink": "symlink pointing to the built subvol",
})

def _map_image(
        ctx: "context",
        cmd: "cmd_args",
        identifier: str.type,
        parent: ["artifact", None]) -> ("cmd_args", "artifact"):
    """
    Take the 'parent' image, and run some command through 'antlir2 map' to
    produce a new image.
    In other words, this is a mapping function of 'image A -> A1'
    """
    out = ctx.actions.declare_output("subvol-" + identifier)
    cmd = cmd_args(
        "sudo",  # this requires privileged btrfs operations
        ctx.attrs.antlir2[RunInfo],
        "map",
        "--working-dir=buck-image-out/volume/antlir2",
        cmd_args(ctx.attrs.build_appliance[LayerInfo].subvol_symlink, format = "--build-appliance={}"),
        cmd_args(str(ctx.label), format = "--label={}"),
        cmd_args(identifier, format = "--identifier={}"),
        cmd_args(parent, format = "--parent={}") if parent else cmd_args(),
        cmd_args(out.as_output(), format = "--output={}"),
        cmd,
    )
    ctx.actions.run(
        cmd,
        category = "antlir2_isolate",
        identifier = identifier,
        # needs local subvolumes
        local_only = True,
        # 'antlir2 isolate' will clean up an old image if it exists
        no_outputs_cleanup = True,
        env = {
            "RUST_LOG": "antlir2=trace",
        },
    )
    return cmd, out

def _impl(ctx: "context") -> ["provider"]:
    if ctx.attrs.parent_layer and Antlir1LayerInfo in ctx.attrs.parent_layer:
        fail("parent_layer cannot be a re-exported antlir1 layer")

    depgraph_input = _build_depgraph(ctx, "json", None)

    available_rpm_repos = ctx.attrs.available_rpm_repos[RepoSetInfo] if ctx.attrs.available_rpm_repos else None
    dnf_repodatas = repodata_only_local_repos(ctx, available_rpm_repos)

    plan = ctx.actions.declare_output("plan")
    plan_cmd, _ = _map_image(
        ctx = ctx,
        cmd = cmd_args(
            cmd_args(dnf_repodatas, format = "--dnf-repos={}"),
            "plan",
            cmd_args(depgraph_input, format = "--depgraph-json={}"),
            cmd_args(plan.as_output(), format = "--plan={}"),
        ),
        identifier = "plan",
        parent = ctx.attrs.parent_layer[LayerInfo].subvol_symlink if ctx.attrs.parent_layer else None,
    )

    # Part of the compiler plan is any possible dnf transaction resolution,
    # which lets us know what rpms we will need. We can have buck download them
    # and mount in a pre-built directory of all repositories for
    # completely-offline dnf installation (which is MUCH faster and more
    # reliable)
    # TODO: this is basically a no-op if there are no features that require
    # pre-planning, but it does still invoke antlir2 inside of the container so
    # takes ~1-2s even if it does nothing. The performance benefit for the
    # extremely common use case of installing rpms absolutely dwarfs this cost,
    # but it would be nice to only run this if there are relevant features
    dnf_repos_dir = compiler_plan_to_local_repos(ctx, available_rpm_repos, plan)

    compile_cmd, final_subvol = _map_image(
        ctx = ctx,
        cmd = cmd_args(
            cmd_args(dnf_repos_dir, format = "--dnf-repos={}"),
            "compile",
            cmd_args(depgraph_input, format = "--depgraph-json={}"),
        ),
        identifier = "compile",
        parent = ctx.attrs.parent_layer[LayerInfo].subvol_symlink if ctx.attrs.parent_layer else None,
    )

    tree_txt = ctx.actions.declare_output("tree.txt")
    ctx.actions.run(
        cmd_args(ctx.attrs.antlir2_print_tree[RunInfo], final_subvol, "--out", tree_txt.as_output()),
        local_only = True,
        category = "antlir2_print_tree",
    )

    depgraph_output = _build_depgraph(ctx, "json", final_subvol)

    # This script is provided solely for developer convenience. It would
    # actually be a large regression to run this to produce the final image
    # during normal buck operation, as it would prevent buck from caching
    # individual actions when possible (for example, if rpm features do not
    # change, the transaction plan might be cached)
    debug_sequence = [plan_cmd, compile_cmd]
    build_script = ctx.actions.write(
        "build.sh",
        cmd_args(
            "#!/bin/bash -e",
            "export RUST_LOG=warn,antlir2=trace",
            [cmd_args(
                c,
                delimiter = " \\\n  ",
                quote = "shell",
            ) for c in debug_sequence],
            "\n",
        ),
        is_executable = True,
    )

    return [
        LayerInfo(
            depgraph = depgraph_output,
            subvol_symlink = final_subvol,
            parent = ctx.attrs.parent_layer[LayerInfo] if ctx.attrs.parent_layer else None,
        ),
        DefaultInfo(
            default_outputs = [final_subvol],
            sub_targets = {
                "build.sh": [
                    DefaultInfo(build_script),
                    RunInfo(args = cmd_args("/bin/bash", "-e", build_script).hidden(debug_sequence)),
                ],
                "depgraph": [DefaultInfo(
                    default_outputs = [],
                    sub_targets = {
                        "input.dot": [DefaultInfo(default_outputs = [_build_depgraph(ctx, "dot", None)])],
                        "input.json": [DefaultInfo(default_outputs = [depgraph_input])],
                        "output.dot": [DefaultInfo(default_outputs = [_build_depgraph(ctx, "dot", final_subvol)])],
                        "output.json": [DefaultInfo(default_outputs = [depgraph_output])],
                    },
                )],
                "plan": [
                    DefaultInfo(default_outputs = [plan]),
                ],
                "tree.txt": [DefaultInfo(
                    default_outputs = [tree_txt],
                )],
            },
        ),
    ]

def _build_depgraph(ctx: "context", format: str.type, subvol: ["artifact", None]) -> "artifact":
    output = ctx.actions.declare_output("depgraph." + format + (".pre" if not subvol else ""))
    ctx.actions.run(
        cmd_args(
            # Inspecting already-built images often requires root privileges
            "sudo" if subvol else cmd_args(),
            ctx.attrs.antlir2[RunInfo],
            "depgraph",
            cmd_args(str(ctx.label), format = "--label={}"),
            format,
            ctx.attrs.features[FeatureInfo].json_files.project_as_args("feature_json"),
            cmd_args(
                ctx.attrs.parent_layer[LayerInfo].depgraph,
                format = '--parent={{"label": "{}", "graph_path": "{{}}"}}'.format(str(ctx.attrs.parent_layer.label)),
            ) if ctx.attrs.parent_layer else cmd_args(),
            cmd_args(subvol, format = "--add-built-items={}") if subvol else cmd_args(),
            cmd_args(output.as_output(), format = "--out={}"),
        ),
        category = "antlir2_depgraph",
        identifier = format + ("/pre" if not subvol else ""),
        local_only = bool(subvol),
    )
    return output

_antlir2_layer = rule(
    impl = _impl,
    attrs = {
        "antlir2": attrs.default_only(attrs.exec_dep(default = "//antlir/staging/antlir2/antlir2:antlir2")),
        "antlir2_print_tree": attrs.default_only(attrs.exec_dep(default = "//antlir/staging/antlir2/antlir2_print_tree:antlir2_print_tree")),
        "available_rpm_repos": attrs.option(attrs.dep(providers = [RepoSetInfo]), default = None),
        "build_appliance": attrs.dep(providers = [LayerInfo], default = "//antlir/staging/antlir2:build-appliance"),
        "features": attrs.dep(providers = [FeatureInfo]),
        "parent_layer": attrs.option(attrs.dep(providers = [LayerInfo]), default = None),
        "rpm_repo_proxy": attrs.default_only(attrs.exec_dep(default = "//antlir/rpm/repo_proxy:repo-proxy")),
    },
)

def antlir2_layer(
        *,
        name: str.type,
        # Features does not have a direct type hint, but it is still validated
        # by a type hint inside feature.bzl. Feature targets or
        # InlineFeatureInfo providers are accepted, at any level of nesting
        features = [],
        **kwargs):
    features = flatten.flatten(features, item_type = ["InlineFeatureInfo", str.type])

    feature_target = name + "--features"
    feature(
        name = feature_target,
        visibility = [":" + name],
        features = features,
    )
    feature_target = ":" + feature_target

    return _antlir2_layer(
        name = name,
        features = feature_target,
        **kwargs
    )

def _antlir2_re_export(ctx: "context") -> ["provider"]:
    subvol_symlink = ctx.attrs.antlir1_layer[Antlir1LayerInfo].subvol_symlink
    return [
        LayerInfo(subvol_symlink = subvol_symlink),
        ctx.attrs.antlir1_layer[Antlir1LayerInfo],
        DefaultInfo(default_outputs = [subvol_symlink]),
    ]

antlir2_re_export_layer = rule(
    impl = _antlir2_re_export,
    attrs = {
        "antlir1_layer": attrs.dep(providers = [Antlir1LayerInfo]),
    },
)