# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""
Feature rules in buck2
======================

Image features in buck2 are coalesced into a single rule for each image that
provides a `FeatureInfo`. This single concrete rule can be constructed with a
combination of inline or standalone features.
Inline features are simply instances of the `ParseTimeFeature` record, while
standalone features are concrete targets that provide `FeatureInfo` themselves.

Usage
=====
The only way for an image build user to construct features is using the inline
feature macros (defined in `.bzl` files in this directory). These inline
features are given to a `feature` rule (or directly to `layer`).

The `layer` rule always creates a single `feature` rule internally, combining
all the inline and standalone features into a single input for the compiler.

Feature implementations
=======================
Features are implemented via macros that take user input and transform it to be
usable with a `feature` rule.
Since rule attribute coercion only happens at the time a real rule is called,
not on inline feature construction, the internal structure of inline rules is a
bit complicated.

Inline feature macros must return an `ParseTimeFeature` record, that is then
used to reconstruct compiler-JSON on the other end.
The `ParseTimeFeature` contains:
    - feature_type: type disambiguation for internal macros and compiler
    - deps_or_sources: map of key -> source for
        `attrs.one_of(attrs.dep(), attrs.source())` dependencies needed by the
        feature. The feature is always able to get the "artifact", and will be
        able to get provider details on "dependency" deps
    - deps: map of key -> dep for `attrs.dep()` dependencies needed by the
        feature.
    - kwargs: map of all non-dependency inputs
For `deps_and_sources` and `deps`, the user input to the inline feature input
will just be a simple string that is a label (or path for plain source files),
but by including it in the special maps in `ParseTimeFeature`, the `feature`
rule is able to coerce those labels to concrete artifacts.

Image features must also provide a function to convert the kwargs, sources and
deps into a JSON struct readable by the compiler. This function must then be
added to the `_analyze_feature` map in this file.
"""

load("@bazel_skylib//lib:types.bzl", "types")
load("//antlir/antlir2/bzl:types.bzl", "FeatureInfo")
# @oss-disable
# @oss-disable
load("//antlir/bzl:flatten.bzl", "flatten")
load("//antlir/bzl/build_defs.bzl", "config")
load(":clone.bzl", "clone_analyze")
load(":ensure_dirs_exist.bzl", "ensure_dir_exists_analyze")
load(":extract.bzl", "extract_analyze")
load(":genrule.bzl", "genrule_analyze")
load(":install.bzl", "install_analyze")
load(":mount.bzl", "mount_analyze")
load(":remove.bzl", "remove_analyze")
load(":requires.bzl", "requires_analyze")
load(":rpms.bzl", "rpms_analyze")
load(":symlink.bzl", "symlink_analyze")
load(":tarball.bzl", "tarball_analyze")
load(":usergroup.bzl", "group_analyze", "user_analyze", "usermod_analyze")

feature_record = record(
    feature_type = str.type,
    label = "target_label",
    data = "",
    requires_planning = bool.type,
    required_artifacts = ["artifact"],
    required_layers = ["LayerInfo"],
)

def _features_require_planning(children: [bool.type], feat: [feature_record.type, None]) -> bool.type:
    if feat and feat.requires_planning:
        return True

    return any(children)

def _feature_as_json(feat: feature_record.type) -> "struct":
    return struct(
        feature_type = feat.feature_type,
        label = feat.label,
        data = feat.data,
    )

Features = transitive_set(
    reductions = {"requires_planning": _features_require_planning},
    json_projections = {"features_json": _feature_as_json},
)

def _project_as_hidden_artifact(hidden: "artifact") -> "cmd_args":
    return cmd_args().hidden(hidden)

# Values of this tset are a single "artifact", with possible children tsets also
# containing single "artifact"s as their values
FeatureArtifacts = transitive_set(
    args_projections = {
        "hidden_artifacts": _project_as_hidden_artifact,
    },
)

def _project_as_hidden_run_info(hidden: "RunInfo") -> "cmd_args":
    return cmd_args().hidden(hidden)

# Values of this tset are a single "RunInfo", with possible children tsets also
# containing single "RunInfo"s as their values
FeatureRunInfos = transitive_set(
    args_projections = {
        "hidden_run_infos": _project_as_hidden_run_info,
    },
)

# Transitive Set projections do not really operate as sets, so we could end up
# with the same layer passed many times into cmd_args, which will often trigger
# very wasteful behavior.
def _reduce_to_unique_layer_deps(children: [["LayerInfo"]], info: ["LayerInfo", None]):
    unique = []
    for child in flatten.flatten(children):
        if child not in unique:
            unique.append(child)
    if info and info not in unique:
        unique.append(info)
    return unique

LayerDependencies = transitive_set(
    reductions = {
        "unique": _reduce_to_unique_layer_deps,
    },
)

_analyze_feature = {
    "clone": clone_analyze,
    "ensure_dir_exists": ensure_dir_exists_analyze,
    "ensure_dir_symlink": symlink_analyze,
    "ensure_file_symlink": symlink_analyze,
    "extract": extract_analyze,
    # @oss-disable
    # @oss-disable
    "genrule": genrule_analyze,
    "group": group_analyze,
    "install": install_analyze,
    "mount": mount_analyze,
    "remove": remove_analyze,
    "requires": requires_analyze,
    "rpm": rpms_analyze,
    "tarball": tarball_analyze,
    "user": user_analyze,
    "user_mod": usermod_analyze,
}

def _impl(ctx: "context") -> ["provider"]:
    # Merge inline features into a single JSON file
    inline_features = []
    inline_artifacts = []
    inline_run_infos = []
    inline_layer_deps = []
    for key, inline in ctx.attrs.inline_features.items():
        feature_deps = ctx.attrs.inline_features_deps.get(key, None)
        feature_deps_or_sources = ctx.attrs.inline_features_deps_or_sources.get(key, None)
        feature_unnamed_deps_or_sources = ctx.attrs.inline_features_unnamed_deps_or_sources.get(key, None)
        analyze_kwargs = inline["kwargs"]
        if feature_deps != None:
            analyze_kwargs["deps"] = feature_deps
        if feature_deps_or_sources != None:
            analyze_kwargs["deps_or_sources"] = feature_deps_or_sources
        if feature_unnamed_deps_or_sources != None:
            analyze_kwargs["unnamed_deps_or_sources"] = feature_unnamed_deps_or_sources

        analysis = _analyze_feature[inline["feature_type"]](**analyze_kwargs)
        inline_artifacts.extend(analysis.required_artifacts)
        inline_run_infos.extend(analysis.required_run_infos)
        inline_layer_deps.extend(analysis.required_layers)

        feat = feature_record(
            feature_type = analysis.feature_type or inline["feature_type"],
            label = ctx.label.raw_target(),
            data = analysis.data,
            requires_planning = analysis.requires_planning,
            required_artifacts = analysis.required_artifacts,
            required_layers = analysis.required_layers,
        )
        inline_features.append(feat)

    # Track the JSON outputs and deps of other feature targets with transitive
    # sets. Note that we cannot produce a single JSON file with all the
    # transitive features, because we need to support "genrule" features where a
    # command outside of buck can be used to produce much more dynamic feature
    # JSON (for example, extract.bzl requires Rust logic to produce its feature
    # output)
    features = ctx.actions.tset(
        Features,
        children = [ctx.actions.tset(Features, value = feat) for feat in inline_features] +
                   [f[FeatureInfo].features for f in ctx.attrs.feature_targets],
    )
    required_artifacts = ctx.actions.tset(
        FeatureArtifacts,
        children = [
            ctx.actions.tset(FeatureArtifacts, value = artifact)
            for artifact in inline_artifacts
        ] + [f[FeatureInfo].required_artifacts for f in ctx.attrs.feature_targets],
    )
    required_run_infos = ctx.actions.tset(
        FeatureRunInfos,
        children = [
            ctx.actions.tset(FeatureRunInfos, value = run_info)
            for run_info in inline_run_infos
        ] + [f[FeatureInfo].required_run_infos for f in ctx.attrs.feature_targets],
    )
    required_layers = ctx.actions.tset(
        LayerDependencies,
        children = [
            ctx.actions.tset(LayerDependencies, value = dep)
            for dep in inline_layer_deps
        ] + [f[FeatureInfo].required_layers for f in ctx.attrs.feature_targets],
    )

    features_json = features.project_as_json("features_json")
    json_file = ctx.actions.write_json("features.json", features_json)

    return [
        FeatureInfo(
            features = features,
            required_artifacts = required_artifacts,
            required_run_infos = required_run_infos,
            required_layers = required_layers,
        ),
        DefaultInfo(json_file),
    ]

# This horrible set of pseudo-exhaustive `one_of` calls is because there
# currently is nothing like `attrs.json()` that will force things like `select`
# to be coerced to real concrete values.
# This nesting _can_ be extended if features grow more complicated kwargs, but
# that's unlikely, so I'm stopping here for now
# https://fb.workplace.com/groups/347532827186692/posts/632399858699986
_primitive = attrs.option(attrs.one_of(attrs.string(), attrs.int(), attrs.bool()))
_value = attrs.one_of(
    _primitive,
    attrs.dict(_primitive, _primitive),
    attrs.list(_primitive),
)
_nestable_value = attrs.one_of(
    _value,
    attrs.dict(_primitive, _value),
    attrs.dict(_primitive, attrs.dict(_primitive, _value)),
    attrs.dict(_primitive, attrs.list(_value)),
    attrs.list(_value),
    attrs.list(attrs.dict(_primitive, _value)),
    attrs.list(attrs.list(_value)),
)

_feature = rule(
    impl = _impl,
    attrs = {
        # feature targets are instances of `_feature` rules that are merged into
        # the output of this rule
        "feature_targets": attrs.list(
            attrs.dep(providers = [FeatureInfo]),
        ),
        # inline features are direct calls to a feature macro inside a layer()
        # or feature() rule instance
        "inline_features": attrs.dict(
            # Unique key for this feature (see _hash_key below)
            attrs.string(),
            attrs.dict(
                # top level kwargs
                attrs.string(),  # kwarg name
                _nestable_value,
            ),
        ),
        # Features need a way to coerce strings to sources or dependencies.
        # Map "feature key" -> "feature deps"
        "inline_features_deps": attrs.dict(attrs.string(), attrs.option(attrs.dict(attrs.string(), attrs.dep()))),
        # Map "feature key" -> "feature dep/source"
        "inline_features_deps_or_sources": attrs.dict(
            attrs.string(),
            attrs.dict(
                attrs.string(),
                attrs.one_of(attrs.dep(), attrs.source()),
            ),
        ),
        # Map "feature key" -> "feature dep/source"
        "inline_features_unnamed_deps_or_sources": attrs.dict(
            attrs.string(),
            attrs.list(
                attrs.one_of(attrs.dep(), attrs.source()),
            ),
        ),
    },
)

def feature(
        name: str.type,
        # No type hint here, but it is validated by flatten_features
        features,
        visibility = None):
    """
    Create a target representing a collection of one or more image features.

    `features` is a list that can contain either:
        - inline (aka unnamed) features created with macros like `install()`
        - labels referring to other `feature` targets
    """
    features = flatten.flatten(features, item_type = ["ParseTimeFeature", str.type, "selector"])
    inline_features = {}
    feature_targets = []
    inline_features_deps = {}
    inline_features_deps_or_sources = {}
    inline_features_unnamed_deps_or_sources = {}
    for feat in features:
        if types.is_string(feat):
            feature_targets.append(feat)
        elif type(feat) == "selector":
            # select() only works to choose between feature targets, not inline features
            feature_targets.append(feat)
        else:
            # type(feat) will show 'record' but we can assume its a ParseTimeFeature
            feature_key = _hash_key(feat)

            inline_features[feature_key] = {"feature_type": feat.feature_type, "kwargs": feat.kwargs}

            if feat.deps:
                # TODO: record providers for later checking
                inline_features_deps[feature_key] = {k: d.dep for k, d in feat.deps.items()}
            if feat.deps_or_sources:
                inline_features_deps_or_sources[feature_key] = feat.deps_or_sources
            if feat.unnamed_deps_or_sources:
                inline_features_unnamed_deps_or_sources[feature_key] = feat.unnamed_deps_or_sources

    return _feature(
        name = name,
        feature_targets = feature_targets,
        inline_features = inline_features,
        inline_features_deps = inline_features_deps,
        inline_features_deps_or_sources = inline_features_deps_or_sources,
        inline_features_unnamed_deps_or_sources = inline_features_unnamed_deps_or_sources,
        visibility = visibility,
        default_target_platform = config.get_platform_for_current_buildfile().target_platform,
    )

# We need a way to disambiguate inline features so that deps/sources can be
# passed back to them to convert to compiler json. This isn't persisted anywhere
# and does not end up in any target labels, so it does not need to be stable,
# just unique for a single evaluation of the target graph.
def _hash_key(x) -> str.type:
    return sha256(repr(x))

# Real, proper buck2 code can use this instead of the macro that shims some
# shitty buck1 conventions
feature_rule = _feature
