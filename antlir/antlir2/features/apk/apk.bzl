# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl:build_phase.bzl", "BuildPhase")
load(
    "//antlir/antlir2/features:feature_info.bzl",
    "FeatureAnalysis",
    "ParseTimeFeature",
    "Planner",
    "feature_record",
    "new_feature_rule",
)
load("//antlir/buck2/bzl:ensure_single_output.bzl", "ensure_single_output")
load("//antlir/bzl:structs.bzl", "structs")
load(":plan.bzl", "apk_planner")

def _looks_like_label(s: str) -> bool:
    if s.startswith(":"):
        return True
    # A buck label contains //, starts at the root or with a cell name, and has a :target part.
    # Reject URL-like strings (http://..., https://...) by requiring the // to be preceded by
    # either end-of-string (bare //target) or an identifier-only cell name (no slashes before).
    if "//" in s and ":" in s:
        head = s.split("//", 1)[0]
        if "/" not in head and ":" not in head:
            return True
    return False

def _install_common(
        action: str,
        *,
        apks: list[str] = [],
        subjects: list[str | Select] | Select = [],
        deps: list[str | Select] | Select = []):
    """Split apk items into subjects (names) and deps (buck targets)."""
    if apks and (subjects or deps):
        fail("'apks' cannot be mixed with 'subjects' or 'deps'")

    if type(subjects) == "list":
        subjects = list(subjects)
    unnamed_deps_or_srcs = None
    for apk in apks:
        if _looks_like_label(apk):
            if not unnamed_deps_or_srcs:
                unnamed_deps_or_srcs = []
            unnamed_deps_or_srcs.append(apk)
        else:
            subjects.append(apk)
    if unnamed_deps_or_srcs and deps:
        fail("internal error: apks and deps cannot both be set (checked earlier)")
    if not unnamed_deps_or_srcs:
        unnamed_deps_or_srcs = deps

    return ParseTimeFeature(
        feature_type = "apk",
        plugin = "antlir//antlir/antlir2/features/apk:apk",
        unnamed_deps_or_srcs = unnamed_deps_or_srcs,
        kwargs = {
            "action": action,
            "subjects": subjects,
        },
        distro_platform_deps = {
            "driver": "antlir//antlir/antlir2/features/apk:driver",
        },
        exec_deps = {
            "plan": "antlir//antlir/antlir2/features/apk:plan",
        },
    )

def apks_install(
        *,
        apks: list[str] = [],
        subjects: list[str | Select] | Select = [],
        deps: list[str | Select] | Select = []):
    """
    Install APK packages by name or .apk source.

    Elements in `apks` can be a package name like `"curl"`, a versioned spec
    like `"curl=8.5.0-r0"`, or a buck target that produces an `.apk` artifact.

    If you want to `select` packages, use `subjects` (for package names) or
    `deps` (for buck targets).
    """
    return _install_common(
        "install",
        apks = apks,
        subjects = subjects,
        deps = deps,
    )

def apks_remove_if_exists(*, apks: list[str | Select] | Select):
    """
    Remove APK packages if they are installed.

    If the package is not installed, this feature is a no-op.
    """
    return ParseTimeFeature(
        feature_type = "apk",
        plugin = "antlir//antlir/antlir2/features/apk:apk",
        kwargs = {
            "action": "remove_if_exists",
            "subjects": apks,
        },
        distro_platform_deps = {
            "driver": "antlir//antlir/antlir2/features/apk:driver",
        },
        exec_deps = {
            "plan": "antlir//antlir/antlir2/features/apk:plan",
        },
    )

def apks_remove(*, apks: list[str | Select] | Select):
    """
    Remove APK packages. Fails if they are not installed.
    """
    return ParseTimeFeature(
        feature_type = "apk",
        plugin = "antlir//antlir/antlir2/features/apk:apk",
        kwargs = {
            "action": "remove",
            "subjects": apks,
        },
        distro_platform_deps = {
            "driver": "antlir//antlir/antlir2/features/apk:driver",
        },
        exec_deps = {
            "plan": "antlir//antlir/antlir2/features/apk:plan",
        },
    )

action_enum = enum(
    "install",
    "remove",
    "remove_if_exists",
)

apk_source_record = record(
    subject = field([str, None], default = None),
    src = field([Artifact, None], default = None),
)

apk_item_record = record(
    action = action_enum,
    apk = apk_source_record,
    feature_label = TargetLabel,
)

def _impl(ctx: AnalysisContext) -> list[Provider]:
    apks = []
    for apk in ctx.attrs.subjects:
        apks.append(apk_source_record(subject = apk))

    artifacts = []
    for apk in ctx.attrs.unnamed_deps_or_srcs:
        if isinstance(apk, Dependency):
            apk = ensure_single_output(apk)
        apks.append(apk_source_record(src = apk))
        artifacts.append(apk)

    return [
        DefaultInfo(),
        FeatureAnalysis(
            feature_type = "apk",
            data = struct(
                items = [
                    apk_item_record(
                        action = action_enum(ctx.attrs.action),
                        apk = apk,
                        feature_label = ctx.label.raw_target(),
                    )
                    for apk in apks
                ],
                driver_cmd = ctx.attrs.driver[RunInfo],
            ),
            required_artifacts = artifacts,
            build_phase = BuildPhase("package_manager"),
            plugin = ctx.attrs.plugin,
            reduce_fn = _reduce_apk_features,
            planner = apk_planner(
                plan = ctx.attrs.plan,
                driver_cmd = ctx.attrs.driver[RunInfo],
            ),
        ),
    ]

apks_rule = new_feature_rule(
    impl = _impl,
    attrs = {
        "action": attrs.enum(["install", "remove", "remove_if_exists"]),
        "driver": attrs.dep(providers = [RunInfo]),
        "plan": attrs.exec_dep(providers = [RunInfo]),
        "subjects": attrs.list(attrs.string()),
        "unnamed_deps_or_srcs": attrs.list(attrs.one_of(attrs.dep(), attrs.source()), default = []),
    },
)

def _reduce_apk_features(left: feature_record | typing.Any, right: feature_record | typing.Any):
    f = structs.to_dict(left)
    f["analysis"] = structs.to_dict(left.analysis)
    f["analysis"]["data"] = structs.to_dict(f["analysis"]["data"])
    f["analysis"]["data"]["items"] = f["analysis"]["data"]["items"] + right.analysis.data.items
    f["analysis"]["data"] = structs.from_dict(f["analysis"]["data"])
    f["analysis"]["required_artifacts"] = f["analysis"]["required_artifacts"] + right.analysis.required_artifacts
    f["analysis"] = FeatureAnalysis(**f["analysis"])
    return feature_record(**f)
