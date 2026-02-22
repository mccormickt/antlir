# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load(
    "//antlir/antlir2/bzl:types.bzl",
    "BuildApplianceInfo",  # @unused Used as type
    "LayerContents",  # @unused Used as type
)
load(
    "//antlir/antlir2/features:feature_info.bzl",
    "PlanInfo",
    "Planner",
    "feature_record",  # @unused Used as type
)

def _plan_fn(
        *,
        ctx: AnalysisContext,
        identifier: str,
        feature: feature_record | typing.Any,
        apk_repo_urls: list[str],
        apk_local_repos: list[Artifact],
        driver_cmd: RunInfo,
        **kwargs) -> list[PlanInfo]:
    items = ctx.actions.declare_output(identifier, "apk/items.json", has_content_based_path = False)
    items = ctx.actions.write_json(items, feature.analysis.data.items, with_inputs = True)
    res = plan(
        ctx = ctx,
        identifier = identifier,
        items = items,
        apk_repo_urls = apk_repo_urls,
        apk_local_repos = apk_local_repos,
        driver_cmd = driver_cmd,
        **kwargs
    )
    return [plan_info(res)]

def plan_info(res: struct) -> PlanInfo:
    return PlanInfo(
        id = "apk",
        output = res.plan_json,
        hidden = res.hidden,
        sub_artifacts = {
            "tx": res.tx_file,
        },
    )

def plan(
        *,
        ctx: AnalysisContext,
        identifier: str,
        rootless: bool,
        items: Artifact | typing.Any,
        label: Label,
        build_appliance: BuildApplianceInfo | typing.Any,
        parent_layer_contents: LayerContents | None,
        apk_repo_urls: list[str],
        apk_local_repos: list[Artifact],
        target_arch: str,
        driver_cmd: RunInfo,
        plan: Dependency) -> struct:
    tx = ctx.actions.declare_output(identifier, "apk/transaction.json", has_content_based_path = False)

    # Run without root if either explicitly configured or there is no parent
    rootless = rootless or not parent_layer_contents

    # Build a unified repos directory that contains:
    #   - `urls.list` — one URL per line (always present, possibly empty)
    #   - `local_<n>` — symlinks to each ApkRepoInfo.repo_dir artifact
    # The driver's `setup_apk_config` directory-style branch reads each
    # file's contents as repo URL lines and treats each subdirectory as a
    # bare filesystem path, so this layout is consumed natively without
    # any driver-side change.
    urls_payload = "\n".join(apk_repo_urls) + ("\n" if apk_repo_urls else "")
    urls_file = ctx.actions.write(
        "{}/apk/urls.list".format(identifier),
        urls_payload,
    )
    repos_entries = {"urls.list": urls_file}
    for i, repo_dir in enumerate(apk_local_repos):
        repos_entries["local_{}".format(i)] = repo_dir

    repos_dir = ctx.actions.declare_output(identifier, "apk/repos", dir = True)
    ctx.actions.symlinked_dir(repos_dir, repos_entries)

    ctx.actions.run(
        cmd_args(
            "sudo" if not rootless else cmd_args(),
            plan[RunInfo],
            cmd_args(label, format = "--label={}"),
            "--rootless" if rootless else cmd_args(),
            cmd_args(parent_layer_contents.subvol_symlink, format = "--parent-subvol-symlink={}") if parent_layer_contents and parent_layer_contents.subvol_symlink else cmd_args(),
            cmd_args(build_appliance.dir, format = "--build-appliance={}"),
            cmd_args(repos_dir, format = "--repos={}"),
            cmd_args(target_arch, format = "--target-arch={}"),
            cmd_args(items, format = "--items={}"),
            cmd_args(driver_cmd, format = "--driver-cmd={}"),
            cmd_args(tx.as_output(), format = "--out={}"),
            cmd_args(["--has-url-repos"] if apk_repo_urls else []),
        ),
        category = "apk_plan",
        identifier = identifier,
        local_only = bool(parent_layer_contents and parent_layer_contents.subvol_symlink),
    )

    plan_json = ctx.actions.declare_output(identifier, "apk/plan.json", has_content_based_path = False)
    out = ctx.actions.write_json(
        plan_json,
        struct(
            build_appliance = build_appliance.dir,
            repos = repos_dir,
            has_url_repos = bool(apk_repo_urls),
            resolved = tx,
        ),
        with_inputs = True,
    )

    return struct(
        plan_json = plan_json,
        hidden = [out],
        tx_file = tx,
    )

def apk_planner(*, plan: Dependency, driver_cmd: RunInfo) -> Planner:
    return Planner(
        fn = _plan_fn,
        parent_layer_contents = True,
        build_appliance = True,
        apk = True,
        label = True,
        target_arch = True,
        kwargs = {
            "plan": plan,
            "driver_cmd": driver_cmd,
        },
    )
