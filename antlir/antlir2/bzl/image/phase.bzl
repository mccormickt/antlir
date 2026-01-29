# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/antlir2_error_handler:handler.bzl", "antlir2_error_handler")
load("//antlir/antlir2/bzl:build_phase.bzl", "BuildPhase")
load("//antlir/antlir2/bzl:types.bzl", "BuildApplianceInfo", "LayerContents")
load("//antlir/antlir2/bzl/feature:feature.bzl", "reduce_features")
load(
    "//antlir/antlir2/features:defs.bzl",
    "FeaturePluginInfo",  # @unused Used as type
)
load(":depgraph.bzl", "build_depgraph")

def _compile(
        *,
        ctx: AnalysisContext,
        identifier: str,
        parent: LayerContents | typing.Any | None,
        logs: OutputArtifact,
        rootless: bool,
        target_arch: str,
        plugins: list[FeaturePluginInfo | typing.Any],
        topo_features: Artifact,
        plans: typing.Any,
        hidden_deps: typing.Any,
        working_format: str,
        parent_facts_db: Artifact | None = None,
        build_appliance: BuildApplianceInfo | Provider | None = None) -> (LayerContents, Artifact):
    """
    Compile features into a new image layer
    """
    antlir2 = ctx.attrs.antlir2[RunInfo]
    if working_format == "btrfs":
        parent_arg = cmd_args(parent.subvol_symlink, format = "--parent={}") if parent else cmd_args()
        subvol_symlink = ctx.actions.declare_output(identifier, "subvol_symlink")
        out_arg = cmd_args(subvol_symlink.as_output(), format = "--output={}")
        contents = LayerContents(
            subvol_symlink = subvol_symlink,
            subvol_symlink_rootless = rootless,
        )
    else:
        fail("unknown working format '{}'".format(working_format))

    facts_db_out = ctx.actions.declare_output(identifier, "facts")

    ctx.actions.run(
        cmd_args(
            cmd_args("sudo") if not rootless else cmd_args(),
            antlir2,
            cmd_args(logs, format = "--logs={}"),
            "compile",
            cmd_args(str(ctx.label), format = "--label={}"),
            parent_arg,
            out_arg,
            cmd_args("--rootless") if rootless else cmd_args(),
            cmd_args(target_arch, format = "--target-arch={}"),
            [
                cmd_args(plugin.plugin, format = "--plugin={}", hidden = [plugin.libs])
                for plugin in plugins
            ],
            cmd_args(topo_features, format = "--features={}"),
            cmd_args(plans, format = "--plans={}"),
            cmd_args(working_format, format = "--working-format={}"),
            cmd_args(parent_facts_db, format = "--parent-facts-db={}") if parent_facts_db else cmd_args(),
            cmd_args(facts_db_out.as_output(), format = "--facts-db-out={}"),
            cmd_args(build_appliance.dir, format = "--build-appliance={}") if build_appliance else cmd_args(),
            hidden = hidden_deps,
        ),
        category = "antlir2",
        env = {
            "RUST_LOG": "antlir2=trace",
        },
        identifier = identifier,
        local_only = (
            # btrfs subvolumes can only exist locally
            working_format == "btrfs" or
            # no sudo access on remote execution
            not rootless or
            # no aarch64 emulation on remote execution
            target_arch == "aarch64"
        ),
        # the old output is used to clean up the local subvolume
        no_outputs_cleanup = working_format == "btrfs",
        error_handler = antlir2_error_handler,
    )

    return contents, facts_db_out

PhaseCompilationResult = record(
    layer = field(LayerContents),
    facts_db = field(Artifact),
    supplements = field(dict[str, typing.Any]),
    phase_sub_targets = field(dict[str, list[Provider]]),
    plans = field(dict[str, typing.Any]),
)

def compile_phase(
        *,
        ctx: AnalysisContext,
        phase: BuildPhase,
        features: list[typing.Any],
        all_plugins: dict[str, FeaturePluginInfo],
        parent_layer: LayerContents | None,
        parent_facts_db: Artifact | None,
        supplements: dict[str, typing.Any],
        previous_phase_plans: dict[str, typing.Any],
        # Pre-computed DNF inputs
        dnf_available_repos: list[typing.Any],
        dnf_excluded_rpms: list[str],
        dnf_versionlock: Artifact | None,
        dnf_versionlock_extend: dict[str, str],
        # Other inputs
        flavor_info: typing.Any | None,
        build_appliance: typing.Any | None,
        target_arch: str,
        rootless: bool,
        working_format: str,
        layer_label: Label) -> PhaseCompilationResult:
    """
    Compile a single build phase.

    This function handles all the logic for compiling one phase of the image build,
    including feature planning, depgraph construction, and actual compilation.
    """
    identifier = phase.value

    # Some feature types must be reduced to one instance per phase (eg
    # package managers)
    features = reduce_features(features)

    # Collect plugins needed for this phase
    phase_plugins = {}
    for feat in features:
        plugin = str(feat.plugin)
        if plugin not in all_plugins:
            fail("{}: '{}' was not found in the list of plugins ({}), but it was used - this should be impossible".format(ctx.label, plugin, all_plugins.keys()))
        phase_plugins[plugin] = all_plugins[plugin]

    # All deps that are needed for *compiling* the features (but not depgraph analysis)
    compile_feature_hidden_deps = [
        [feat.analysis.required_artifacts for feat in features],
        [feat.analysis.required_run_infos for feat in features],
    ]

    # Cover all the other inputs needed for compiling a feature by writing
    # it to a json file. This is just an easy way to just traverse the
    # structure to find any artifacts, but this json file is not directly
    # read anywhere
    compile_feature_hidden_deps.append(
        ctx.actions.write_json(
            ctx.actions.declare_output(identifier, "features.json"),
            [f.analysis.data for f in features],
            with_inputs = True,
        ),
    )

    extend_facts = []

    plans = {}
    plan_sub_targets = {}
    result_supplements = dict(supplements)
    for feature in features:
        extend_facts.extend(feature.analysis.extend_facts_json)
        planner = feature.analysis.planner
        if planner:
            kwargs = {}
            if planner.label:
                kwargs["label"] = layer_label
            if planner.flavor:
                kwargs["flavor"] = flavor_info
            if planner.build_appliance:
                kwargs["build_appliance"] = build_appliance[BuildApplianceInfo] if build_appliance else None
            if planner.target_arch:
                kwargs["target_arch"] = target_arch
            if planner.parent_layer_contents:
                kwargs["parent_layer_contents"] = parent_layer
            if planner.dnf:
                kwargs |= {
                    "dnf_available_repos": dnf_available_repos,
                    "dnf_excluded_rpms": dnf_excluded_rpms,
                    "dnf_versionlock": dnf_versionlock,
                    "dnf_versionlock_extend": dnf_versionlock_extend,
                }
            for id in planner.previous_phase_plans:
                if id not in previous_phase_plans:
                    fail("previous_phase_plan '{}' does not exist".format(id))
                kwargs["previous_phase_plan_{}".format(id)] = previous_phase_plans[id]

            plan_infos = planner.fn(
                ctx = ctx,
                identifier = identifier,
                rootless = rootless,
                feature = feature,
                **(kwargs | planner.kwargs)
            )
            for pi in plan_infos:
                if pi.id in plans:
                    fail("plan ids should be unique, but got '{}' multiple times".format(pi.id))
                plans[pi.id] = pi
                compile_feature_hidden_deps.append(pi.hidden)
                if pi.sub_artifacts:
                    plan_sub_targets[pi.id] = [DefaultInfo(
                        pi.output,
                        sub_targets = {
                            key: [DefaultInfo(artifact)]
                            for key, artifact in pi.sub_artifacts.items()
                        },
                    )]
                extend_facts.extend(pi.extend_facts_json)
                if pi.mutate_supplements:
                    result_supplements = pi.mutate_supplements(result_supplements)

        if feature.analysis.mutate_supplements:
            result_supplements = feature.analysis.mutate_supplements(result_supplements)

    phase_sub_targets = {}
    phase_sub_targets["plan"] = [DefaultInfo(sub_targets = plan_sub_targets)]

    plans_json = ctx.actions.write_json(
        ctx.actions.declare_output(identifier, "plans.json"),
        {id: pi.output for id, pi in plans.items() if pi.output != None},
        with_inputs = True,
    )

    # facts_db also holds the depgraph
    facts_db, topo_features = build_depgraph(
        ctx = ctx,
        plugins = phase_plugins,
        features = features,
        extend_facts = extend_facts,
        identifier = identifier,
        parent = parent_facts_db,
        phase = phase,
    )
    phase_sub_targets["depgraph"] = [DefaultInfo(facts_db)]
    phase_sub_targets["topo_features.json"] = [DefaultInfo(topo_features)]

    logs = {}
    logs["compile"] = ctx.actions.declare_output(identifier, "compile.log")
    layer, facts_db = _compile(
        ctx = ctx,
        identifier = identifier,
        parent = parent_layer,
        logs = logs["compile"].as_output(),
        rootless = rootless,
        target_arch = target_arch,
        plugins = phase_plugins.values(),
        topo_features = topo_features,
        plans = plans_json,
        hidden_deps = compile_feature_hidden_deps,
        working_format = working_format,
        parent_facts_db = parent_facts_db,
        build_appliance = build_appliance[BuildApplianceInfo] if build_appliance else None,
    )

    all_logs = ctx.actions.declare_output(identifier, "logs", dir = True)
    ctx.actions.symlinked_dir(all_logs, {key + ".log": artifact for key, artifact in logs.items()})
    if layer.subvol_symlink:
        phase_sub_targets["subvol_symlink"] = [DefaultInfo(layer.subvol_symlink)]

    phase_sub_targets["facts"] = [DefaultInfo(facts_db)]
    phase_sub_targets["logs"] = [DefaultInfo(all_logs, sub_targets = {
        key: [DefaultInfo(artifact)]
        for key, artifact in logs.items()
    })]

    return PhaseCompilationResult(
        layer = layer,
        facts_db = facts_db,
        supplements = result_supplements,
        phase_sub_targets = phase_sub_targets,
        plans = plans,
    )
