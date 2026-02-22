# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl/feature:defs.bzl", "feature")
load("//antlir/antlir2/bzl/image:defs.bzl", "image")
load("//antlir/antlir2/bzl/package:defs.bzl", "package")
load("//antlir/antlir2/package_managers/apk/rules:apk.bzl", "apk")
load("//antlir/antlir2/package_managers/apk/rules:repo.bzl", "apk_repo")
load("//antlir/antlir2/testing:image_test.bzl", "image_sh_test")
load("//antlir/bzl:types.bzl", "types")

types.lint_noop()

expected_t = record(
    installed = field(list[str], default = []),
    not_installed = field(list[str], default = []),
    world_entries = field(list[str], default = []),
)

def test_apks(
        name: str,
        expected: expected_t,
        features: list[typing.Any] = [],
        parent_layer: str | None = None,
        apk_additional_repos: list[str] | None = None,
        labels: list[str] | None = None):
    """Build a wolfi layer with `features` and verify `expected` inside it.

    The verifier runs INSIDE the layer and uses only POSIX shell + `apk`,
    guaranteed by wolfi-baselayout + apk-tools. No Python interpreter is
    required in the layer under test. Expected sets are passed via env
    vars so a single shared script handles every invocation.
    """
    layer_kwargs = {}
    if apk_additional_repos != None:
        layer_kwargs["apk_additional_repos"] = apk_additional_repos
    image.layer(
        name = name + "--layer",
        parent_layer = parent_layer,
        features = features,
        default_os = "wolfi",
        **layer_kwargs
    )
    image_sh_test(
        name = name,
        layer = ":{}--layer".format(name),
        test = "check_apks.sh",
        env = {
            "APK_INSTALLED": " ".join(expected.installed),
            "APK_NOT_INSTALLED": " ".join(expected.not_installed),
            "APK_WORLD": " ".join(expected.world_entries),
        },
        labels = labels,
    )

    return ":{}--layer".format(name)

def build_test_app_apk(
        *,
        name: str,
        apk_name: str,
        version: str = "1.0.0",
        features: list[typing.Any] = [],
        depends: list[str] = []):
    """Build an .apk from a fresh layer and wrap it for repo consumption.

    Emits three targets:
      - `<name>--layer`: image.layer with `features` on top of wolfi base
      - `<name>--package`: package.apk over that layer
      - `<name>`: the apk wrapper (validates pkgname/pkgver against the
        rule's attrs) suitable for use in `apk_repo(apks=...)`.
    """
    image.layer(
        name = name + "--layer",
        features = features,
        default_os = "wolfi",
        rootless = True,
    )
    package.apk(
        name = name + "--package",
        layer = ":" + name + "--layer",
        apk_name = apk_name,
        version = version,
        depends = depends,
    )
    apk(
        name = name,
        apk = ":" + name + "--package",
        apk_name = apk_name,
        version = version,
    )
    return ":" + name

def roundtrip_test_apks(
        name: str,
        app_name: str,
        app_features: list[typing.Any],
        app_version: str = "1.0.0",
        app_depends: list[str] = [],
        install: list[str] | None = None,
        extra_apks: list[str] = [],
        extra_install: list[str] = [],
        labels: list[str] | None = None):
    """End-to-end APK round trip: layer → .apk → repo → install → verify.

    1. Build `app_features` into an image.layer.
    2. Wrap that layer as a `.apk` (`package.apk`).
    3. Wrap as the validated `apk` rule (asserts pkgver matches).
    4. Index it (with any `extra_apks`) into a local `apk_repo`.
    5. Build a child wolfi layer that adds the local repo via
       `apk_additional_repos` and installs `install` (defaults to
       `[app_name]`) via `feature.apks_install`.
    6. Run `check_apks.sh` inside the child layer to assert `install`
       (plus `extra_install`) are present.

    `extra_apks` lets a caller pull additional pre-built apk targets
    (e.g. dependencies built by another `build_test_app_apk` call) into
    the same repo, which is how transitive-dep cases are exercised.
    """
    install = install if install != None else [app_name]
    app_target = build_test_app_apk(
        name = name + "--app",
        apk_name = app_name,
        version = app_version,
        features = app_features,
        depends = app_depends,
    )
    apk_repo(
        name = name + "--repo",
        apks = [app_target] + list(extra_apks),
    )

    # The child layer mirrors what a user would write to consume the
    # locally-built apk: declare the repo as additional and request
    # installation by package name.
    test_apks(
        name = name,
        expected = expected_t(
            installed = [_install_subject_to_name(s) for s in install] + extra_install,
            world_entries = [_install_subject_to_name(s) for s in install],
        ),
        features = [
            feature.apks_install(apks = install),
        ],
        apk_additional_repos = [":" + name + "--repo"],
        labels = labels,
    )

    return ":{}--layer".format(name)

def _install_subject_to_name(spec: str) -> str:
    """Strip apk version-pin/tag syntax to bare package name.

    Mirrors the `_bare_pkg_name` helper in driver.py so test expectations
    can be derived from the same install spec the layer feeds into apk.
    """
    if spec.startswith("@"):
        slash = spec.find("/")
        if slash != -1:
            spec = spec[slash + 1:]
    for op in ("=", "<", ">", "~"):
        idx = spec.find(op)
        if idx != -1:
            return spec[:idx]
    return spec
