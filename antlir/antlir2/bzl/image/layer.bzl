# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# @oss-disable[end= ]: load("@fbsource//tools/target_determinator/macros:ci.bzl", "ci")
load("@prelude//utils:expect.bzl", "expect", "expect_non_none")
load("//antlir/antlir2/antlir2_rootless:package.bzl", "antlir2_rootless_config_set", "get_antlir2_rootless")
load("//antlir/antlir2/bzl:binaries_require_repo.bzl", "binaries_require_repo")
load("//antlir/antlir2/bzl:build_phase.bzl", "BuildPhase")
load("//antlir/antlir2/bzl:platform.bzl", "arch_select")
load("//antlir/antlir2/bzl:selects.bzl", "selects")
load("//antlir/antlir2/bzl:types.bzl", "FeatureInfo", "FlavorInfo", "LayerContents", "LayerInfo")
load("//antlir/antlir2/bzl/feature:feature.bzl", "feature_attrs", "feature_rule", "shared_features_attrs")

load("//antlir/bzl:oss_shim.bzl", all_fbpkg_mounts = "ret_empty_list") # @oss-enable
# @oss-disable[end= ]: load("//antlir/antlir2/bzl/image/facebook:fbpkg_mount_utils.bzl", "all_fbpkg_mounts")

load("//antlir/bzl:oss_shim.bzl", fb_defaults = "empty_dict") # @oss-enable
# @oss-disable[end= ]: load("//antlir/antlir2/bzl/image/facebook:layer.bzl", "fb_defaults")
load("//antlir/antlir2/features:defs.bzl", "FeaturePluginInfo", "FeaturePluginPluginKind")
load(
    "//antlir/antlir2/features/mount:mount.bzl",
    "DefaultMountpointInfo",
)
load("//antlir/antlir2/os:package.bzl", "get_default_os_for_package")
# @oss-disable[end= ]: load("//antlir/antlir2/os/facebook:package.bzl", "get_default_rou_for_package")
load("//antlir/antlir2/package_managers/dnf/rules:repo.bzl", "RepoInfo", "RepoSetInfo")
load("//antlir/bzl:build_defs.bzl", "config", "get_visibility")
load("//antlir/bzl:constants.bzl", "REPO_CFG")
load("//antlir/bzl:internal_external.bzl", "is_facebook")
load("//antlir/bzl:types.bzl", "types")
load(":cfg.bzl", "attrs_selected_by_cfg", "cfg_attrs", "layer_cfg")
load(":mount_types.bzl", "mount_record")  # @unused Used as type
load(
    ":mounts.bzl",
    "all_mounts",
    "container_mount_args",
)
load(":phase.bzl", "compile_phase")

def _container_sub_target(
        binary: Dependency | None,
        layer: LayerContents,
        mounts: list[mount_record],
        rootless: bool,
        binaries_require_repo: bool | None) -> list[Provider]:
    if not binary:
        return [DefaultInfo()]
    dev_mode_args = cmd_args()
    if binaries_require_repo:
        dev_mode_args = cmd_args(
            "--artifacts-require-repo",
            cmd_args([cmd_args("--bind-mount-ro", p, p) for p in REPO_CFG.host_mounts_for_repo_artifacts]),
        )
    return [
        DefaultInfo(),
        RunInfo(cmd_args(
            "sudo" if not rootless else cmd_args(),
            binary[RunInfo],
            "--rootless" if rootless else cmd_args(),
            cmd_args(layer.subvol_symlink, format = "--subvol={}"),
            cmd_args([container_mount_args(mount) for mount in mounts]),
            dev_mode_args,
        )),
    ]

def _impl(ctx: AnalysisContext) -> Promise:
    feature_anon_kwargs = {key.removeprefix("_feature_"): getattr(ctx.attrs, key) for key in dir(ctx.attrs) if key.startswith("_feature_")}
    feature_anon_kwargs["name"] = str(ctx.label.raw_target())
    return ctx.actions.anon_target(
        feature_rule,
        feature_anon_kwargs,
    ).promise.map(partial(_impl_with_features, ctx = ctx))

def _extra_repo_name_to_repo(repo_name: str, flavor_info: FlavorInfo) -> Dependency | None:
    default_repos = flavor_info.dnf_info.default_repo_set[RepoSetInfo].repos
    extra_repos = flavor_info.dnf_info.default_extra_repo_set[RepoSetInfo].repos

    for repo in extra_repos:
        if repo[RepoInfo].logical_id == repo_name:
            return repo

    for repo in default_repos:
        if repo[RepoInfo].logical_id == repo_name:
            return None

    fail("Unknown extra repo: {}. Possible choices are {}".format(
        repo_name,
        [repo[RepoInfo].logical_id for repo in extra_repos],
    ))

def _impl_with_features(features: ProviderCollection, *, ctx: AnalysisContext) -> list[Provider]:
    flavor = None
    if ctx.attrs.parent_layer and ctx.attrs.flavor:
        parent_flavor = ctx.attrs.parent_layer[LayerInfo].flavor
        if parent_flavor:
            expect(
                ctx.attrs.flavor[FlavorInfo].label.raw_target() == parent_flavor[FlavorInfo].label.raw_target(),
                "{}: flavor ({}) was different from parent_layer's ({}) flavor ({})",
                ctx.label.raw_target(),
                ctx.attrs.flavor[FlavorInfo].label.raw_target(),
                ctx.attrs.parent_layer.label.raw_target(),
                parent_flavor[FlavorInfo].label.raw_target(),
            )
    if ctx.attrs.parent_layer:
        flavor = ctx.attrs.parent_layer[LayerInfo].flavor
    if not flavor:
        flavor = ctx.attrs.flavor
    flavor_info = flavor[FlavorInfo] if flavor else None
    build_appliance = ctx.attrs.build_appliance

    # Expose a number of things as sub-targets for both humans doing `buck
    # build` and cases where we must access a specific output from the macro
    # layer where we don't have proper rules and access to providers
    sub_targets = {
        "features": [features[FeatureInfo], features[DefaultInfo]],
    }
    if ctx.attrs.parent_layer:
        sub_targets["parent_layer"] = ctx.attrs.parent_layer.providers

    if flavor:
        sub_targets["flavor"] = flavor.providers

    all_features = features[FeatureInfo].features

    plugin_list = (ctx.attrs._plugins or []) + (
        ctx.plugins[FeaturePluginPluginKind] if FeaturePluginPluginKind in ctx.plugins else []
    )
    all_plugins = {}
    for plugin in plugin_list:
        all_plugins[str(plugin.label.raw_target())] = plugin[FeaturePluginInfo]

    dnf_available_repos = []
    if types.is_list(ctx.attrs.dnf_available_repos):
        dnf_available_repos = ctx.attrs.dnf_available_repos
    elif ctx.attrs.dnf_available_repos != None:
        dnf_available_repos = list(ctx.attrs.dnf_available_repos[RepoSetInfo].repos)
    else:
        dnf_available_repos = list(flavor_info.dnf_info.default_repo_set[RepoSetInfo].repos)

    dnf_additional_repos = ctx.attrs.dnf_additional_repos or []
    if not types.is_list(dnf_additional_repos):
        dnf_additional_repos = [dnf_additional_repos]

    dnf_additional_repos = dnf_additional_repos + ctx.attrs._dnf_auto_additional_repos

    for repo in dnf_additional_repos:
        if types.is_string(repo):
            extra_repo = _extra_repo_name_to_repo(repo, flavor_info)
            if extra_repo != None:
                dnf_available_repos.append(extra_repo)
        elif RepoSetInfo in repo:
            dnf_available_repos.extend(repo[RepoSetInfo].repos)
        elif RepoInfo in repo:
            dnf_available_repos.append(repo)
        else:
            fail("Unknown type for repo {} in dnf_additional_repos: ".format(repo))

    for logical_id in ctx.attrs.dnf_exclude_repos:
        to_remove = None
        for repo in dnf_available_repos:
            if repo[RepoInfo].logical_id == logical_id:
                to_remove = repo
        if not to_remove:
            fail("Logical id '{}' does not match any repo ({}), remove it".format(
                logical_id,
                [r[RepoInfo].logical_id for r in dnf_available_repos],
            ))
        dnf_available_repos.remove(to_remove)

    dnf_versionlock = ctx.attrs.dnf_versionlock or flavor_info.dnf_info.default_versionlock
    dnf_excluded_rpms = list(ctx.attrs.dnf_excluded_rpms) if ctx.attrs.dnf_excluded_rpms != None else list(flavor_info.dnf_info.default_excluded_rpms)

    # rpmsign is missing a dependency: /usr/lib64/libtss2-rc.so.0
    # (P557719932). This failure occurss because tpm2-tss provides
    # /usr/lib64/libtss2-rc.so.0, but aziot-identity-service contains
    # /usr/lib64/aziot-identity-service/libtss2-rc.so.0 and dnf will happily
    # install that to satisfy the rpmsign dependency, even though it doesn't
    # actually do that. Since aziot-identity-service isn't actually used
    # anywhere, just exclude it
    if "aziot-identity-service" not in dnf_excluded_rpms:
        dnf_excluded_rpms.append("aziot-identity-service")

    # https://fb.workplace.com/groups/upstreampackaging/posts/2218930438568048
    if "mft" not in dnf_excluded_rpms:
        dnf_excluded_rpms.append("mft")

    # The image build is split into phases based on features' `build_phase`
    # property.
    # This gets us some caching benefits (for example, if a feature in a layer
    # changed but does not change the rpm installations, that intermediate layer
    # can still be cached and not have to re-install rpms).
    #
    # Equally importantly, this enables more correctness in the dependency
    # graph, since the depgraph will immediately recognize any rpm-installed
    # files in the layer, users created by package installation, etc.
    #
    # Effectively, this is the same as if image authors separated their layer
    # rules into a layer that installs rpms, then an immediate child layer that
    # contains all the other features. In practice that's really hard and
    # inconvenient for image authors, but is incredibly useful for everyone
    # involved, so we can do it for them implicitly.

    layer = ctx.attrs.parent_layer[LayerInfo].contents if ctx.attrs.parent_layer else None
    facts_db = ctx.attrs.parent_layer[LayerInfo].facts_db if ctx.attrs.parent_layer else None
    supplements = dict(ctx.attrs.parent_layer[LayerInfo].supplements) if ctx.attrs.parent_layer else {}
    debug_sub_targets = {}
    phase_contents = []

    # See Planner.previous_phase_plans for rationale
    previous_phase_plans = {}

    for phase in BuildPhase.values():
        phase = BuildPhase(phase)

        features = [
            feat
            for feat in all_features
            # Cross-cell enum type comparisons with bare == can fail, so compare
            # .value explicitly
            if feat.analysis.build_phase.value == phase.value
        ]

        # Build phase can be skipped if it doesn't contain any features, but if
        # this is the final phase and nothing has been built yet, we need to
        # fall through and produce an empty subvolume so it can still be used as
        # a parent_layer and/or snapshot its own parent's contents
        if not features and not (phase == BuildPhase("compile") and layer == None):
            continue

        # Compile this phase
        result = compile_phase(
            ctx = ctx,
            phase = phase,
            features = features,
            all_plugins = all_plugins,
            parent_layer = layer,
            parent_facts_db = facts_db,
            supplements = supplements,
            previous_phase_plans = previous_phase_plans,
            dnf_available_repos = dnf_available_repos,
            dnf_excluded_rpms = dnf_excluded_rpms,
            dnf_versionlock = dnf_versionlock,
            dnf_versionlock_extend = ctx.attrs.dnf_versionlock_extend,
            flavor_info = flavor_info,
            build_appliance = build_appliance,
            target_arch = ctx.attrs._selected_target_arch,
            rootless = ctx.attrs._rootless,
            working_format = ctx.attrs._working_format,
            layer_label = ctx.label,
        )

        # Update state for next phase
        layer = result.layer
        facts_db = result.facts_db
        supplements = result.supplements
        previous_phase_plans = result.plans

        phase_contents.append((phase, layer))

        # Add container sub-target if needed
        if layer.subvol_symlink:
            result.phase_sub_targets["container"] = _container_sub_target(
                ctx.attrs._run_container,
                layer,
                mounts = all_mounts(
                    features = features,
                    parent_layer = ctx.attrs.parent_layer[LayerInfo] if ctx.attrs.parent_layer else None,
                ),
                rootless = ctx.attrs._rootless,
                binaries_require_repo = ctx.attrs._binaries_require_repo,
            )

        debug_sub_targets[phase.value] = [
            DefaultInfo(
                sub_targets = result.phase_sub_targets,
            ),
        ]

    debug_sub_targets["facts"] = [DefaultInfo(facts_db)]

    parent_layer_info = ctx.attrs.parent_layer[LayerInfo] if ctx.attrs.parent_layer else None
    mounts = all_mounts(features = all_features, parent_layer = parent_layer_info)
    # @oss-disable[end= ]: mounts += all_fbpkg_mounts(features = all_features, parent_layer = parent_layer_info)

    sub_targets["debug"] = [DefaultInfo(sub_targets = debug_sub_targets)]

    if layer.subvol_symlink:
        subvol_symlink = layer.subvol_symlink
        sub_targets["container"] = _container_sub_target(
            ctx.attrs._run_container,
            layer,
            mounts,
            ctx.attrs._rootless,
            binaries_require_repo = ctx.attrs._binaries_require_repo,
        )

        sub_targets["subvol_symlink"] = [DefaultInfo(layer.subvol_symlink)]
    else:
        fail("no subvol_symlink, this is impossible when the only supported format is btrfs")

    providers = [
        DefaultInfo(
            subvol_symlink,
            sub_targets = sub_targets,
        ),
        LayerInfo(
            facts_db = facts_db,
            flavor = flavor,
            label = ctx.label,
            mounts = mounts,
            parent = ctx.attrs.parent_layer,
            features = all_features,
            contents = layer,
            phase_contents = phase_contents,
            supplements = supplements,
        ),
    ]

    if ctx.attrs.default_mountpoint:
        providers.append(DefaultMountpointInfo(default_mountpoint = ctx.attrs.default_mountpoint))

    return providers

_layer_attrs = {
    "antlir2": attrs.exec_dep(default = "antlir//antlir/antlir2/antlir2:antlir2"),
    "default_mountpoint": attrs.option(attrs.string(), default = None),
    "dnf_additional_repos": attrs.option(
        attrs.one_of(
            attrs.dep(providers = [RepoSetInfo]),
            attrs.list(
                attrs.one_of(
                    attrs.dep(providers = [RepoInfo]),
                    attrs.string(),
                ),
            ),
        ),
        default = None,
        doc = """
            Make more dnf repos available while building this layer.
        """,
    ),
    "dnf_available_repos": attrs.option(
        attrs.one_of(
            attrs.list(attrs.dep(providers = [RepoInfo])),
            attrs.dep(providers = [RepoSetInfo]),
        ),
        default = None,
        doc = """
            Restrict the available dnf repos while building this layer to this
            repo_set and anything in dnf_additional_repos
        """,
    ),
    "dnf_exclude_repos": attrs.list(
        attrs.string(doc = "RepoInfo logical_id to exclude from the otherwise available repos"),
        default = [],
        doc = """
            Hide some repos from dnf resolution
        """,
    ),
    "dnf_excluded_rpms": attrs.option(
        attrs.list(attrs.string()),
        default = None,
    ),
    "dnf_versionlock": attrs.option(
        attrs.source(),
        default = None,
    ),
    "dnf_versionlock_extend": attrs.dict(
        attrs.string(doc = "rpm name"),
        attrs.string(doc = "rpm evra"),
        default = {},
    ),
    "labels": attrs.list(attrs.string(), default = []),
    "parent_layer": attrs.option(
        attrs.dep(providers = [LayerInfo]),
        default = None,
    ),
    "_analyze_feature": attrs.exec_dep(default = "antlir//antlir/antlir2/antlir2_depgraph_if:analyze"),
    "_binaries_require_repo": binaries_require_repo.optional_attr,
    "_dnf_auto_additional_repos": attrs.list(
        attrs.one_of(
            attrs.dep(providers = [RepoInfo]),
            attrs.dep(providers = [RepoSetInfo]),
        ),
        # the true default is populated at the macro level
        default = [],
        doc = """
            Equivalent to 'dnf_additional_repos' but selected only by internal
            configurations (like systemd-cd).
        """,
    ),
    "_plugins": attrs.list(
        attrs.dep(providers = [FeaturePluginInfo]),
        default = [],
        doc = "Used as a way to pass plugins to anon layer targets",
    ),
    "_run_container": attrs.option(attrs.exec_dep(), default = None),
    "_selected_target_arch": attrs.default_only(attrs.string(
        default = arch_select(aarch64 = "aarch64", x86_64 = "x86_64"),
        doc = "CPU arch that this layer is being built for.",
    )),
    "_working_format": attrs.default_only(attrs.string(
        default = select({
            "DEFAULT": "btrfs",
            "antlir//antlir/antlir2/cfg:working_format[btrfs]": "btrfs",
        }),
    )),
}

_layer_attrs.update(cfg_attrs())
_layer_attrs.update(attrs_selected_by_cfg())

_layer_attrs.update(
    {
        "_feature_" + key: val
        for key, val in shared_features_attrs.items()
    },
)

layer_rule = rule(
    impl = _impl,
    attrs = _layer_attrs,
    cfg = layer_cfg,
    uses_plugins = [FeaturePluginPluginKind],
)

def layer(
        *,
        name: str,
        # Features does not have a direct type hint, but it is still validated
        # by a type hint inside feature.bzl. Feature targets or
        # InlineFeatureInfo providers are accepted, at any level of nesting
        features = [],
        parent_layer: str | Select | None = None,
        default_os: str | None = None,
        default_rou: str | None = None,
        rootless: bool | None = None,
        compatible_with_os: list[str] | Select | None = None,
        visibility: list[str] | None = None,
        compatible_with = None,
        # mark whether or not this was an implicit layer that must inherit its
        # parent flavor configuration
        implicit_layer_reason: str | None = None,
        **kwargs):
    """
    Create a new image layer

    Build a new image layer from the given `features` and `parent_layer`.
    """

    if "flavor" in kwargs:
        fail("flavor cannot be manually set on layer targets")

    # Some layers must inherit their parent flavor and not the package setting,
    # but this should be a narrow use case mainly limited to antlir-owned macros.
    if implicit_layer_reason:
        kwargs["labels"] = kwargs.pop("labels", []) + ["antlir2-implicit-layer=" + implicit_layer_reason]
        if default_os or default_rou:
            fail("implicit layers must not set default_os or default_rou")
        kwargs["flavor"] = selects.apply(
            expect_non_none(parent_layer, msg = "parent_layer required for implicit layers"),
            lambda parent_layer: parent_layer + "[flavor]",
        )

    force_flavor = kwargs.pop("force_flavor", None)
    if force_flavor:
        kwargs["flavor"] = force_flavor
        if default_os or default_rou:
            fail("force_flavor layers must not set default_os or default_rou")

    default_os = default_os or get_default_os_for_package()

    kwargs.update({"_feature_" + key: val for key, val in feature_attrs(features).items()})

    if is_facebook:
        # Set this as a default in the macro layer so that it doesn't need to be
        # set for anon layers
        kwargs.setdefault(
            "_dnf_auto_additional_repos",
            fb_defaults["_dnf_auto_additional_repos"],
        )

    kwargs["default_target_platform"] = config.get_platform_for_current_buildfile().target_platform

    # If the user didn't configure the rootless setting, default to True
    # NOTE: this only affects builds that `buck2 build` the layer directly, it
    # does *NOT* affect builds that use this layer as a dependency (packages,
    # container tests, vm tests).
    #
    # Why does it matter then?
    # Automated bisect services will `buck2 build` layer targets directly when
    # they are broken, and that does not support `sudo`.
    if not antlir2_rootless_config_set() and rootless == None:
        rootless = True
    elif rootless == None:
        rootless = get_antlir2_rootless()

    additional_labels = []
    # @oss-disable[end= ]: additional_labels += ci.labels(ci.remove(ci.windows()))
    if not rootless:
        additional_labels += ["uses_sudo"]
    kwargs["labels"] = selects.apply(
        kwargs.pop("labels", []),
        lambda labels: additional_labels + labels,
    )

    # Annoyingly, we can only accept target_compatible_with because we need to
    # indicate a *HARD* requirement for `os:linux`, which is impossible with
    # compatible_with, and the two cannot be mixed
    if compatible_with:
        fail("compatible_with cannot be used on image layers, use target_compatible_with instead")
    target_compatible_with = selects.apply(
        selects.join(
            user = kwargs.pop("target_compatible_with", []),
            os = select({
                "DEFAULT": ["ovr_config//os:linux"],
                "ovr_config//os:macos": ["ovr_config//os:macos"],
            }),
        ),
        lambda sels: (sels.user or []) + sels.os,
    )
    if compatible_with_os:
        target_compatible_with = selects.apply(
            selects.join(
                tcw = target_compatible_with,
                oses = select({
                    "//antlir/antlir2/os:" + os: ["//antlir/antlir2/os:" + os]
                    for os in compatible_with_os
                }),
            ),
            lambda sels: sels.tcw,
        )

    return layer_rule(
        name = name,
        parent_layer = parent_layer,
        default_os = default_os,
        # @oss-disable[end= ]: default_rou = default_rou or get_default_rou_for_package(),
        rootless = rootless,
        visibility = get_visibility(visibility),
        target_compatible_with = target_compatible_with,
        _run_container = "antlir//antlir/antlir2/container_subtarget:run",
        _binaries_require_repo = binaries_require_repo.select_value,
        exec_compatible_with = ["prelude//platforms:may_run_local"] + select({
            # arm images can be built on x86_64 hosts, but the reverse
            # is not true
            "ovr_config//cpu:arm64": ["ovr_config//os:linux"],
            "ovr_config//cpu:x86_64": ["ovr_config//cpu:x86_64", "ovr_config//os:linux"],
        }),
        **kwargs
    )
