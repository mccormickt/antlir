# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""A vendored, hermetic snapshot of the Wolfi `os` apk repository.

`wolfi_snapshot` assembles a minimal `APKINDEX.tar.gz` (committed under
`<arch>/`) plus the closure's `.apk` files — fetched at build time by
buck2's checksum-verified `download_file` from the URLs pinned in
`wolfi.lock.bzl` — into a repo directory, and exposes it as an
`ApkRepoInfo` (the provider an `image.layer`'s `apk_repos` /
`apk_additional_repos` consume).

The index lists exactly the vendored closure, so apk resolves only
against packages we pinned. It is unsigned (we do not hold the
wolfi-signing key); the apk driver reaches it with `--allow-untrusted`.
The `.apk` blobs are content-addressed by sha256 and not committed.

Regenerate `wolfi.lock.bzl` + the index with `generate.py`.
"""

load("//antlir/antlir2/bzl:platform.bzl", "rule_with_default_target_platform")
load("//antlir/antlir2/package_managers/apk/rules:repo.bzl", "ApkRepoInfo")

def _wolfi_snapshot_impl(ctx: AnalysisContext) -> list[Provider]:
    arch = ctx.attrs.arch

    # apk expects a repo laid out as `<repo>/<arch>/{APKINDEX.tar.gz,*.apk}`.
    entries = {
        "{}/{}".format(arch, ctx.attrs.apkindex.basename): ctx.attrs.apkindex,
    }
    for filename, url, sha256 in ctx.attrs.packages:
        # buck2 downloads each `.apk` once, verifies it against `sha256`,
        # and content-addresses it in the cache — the blobs are never
        # committed to the repo.
        apk = ctx.actions.declare_output(filename)
        ctx.actions.download_file(apk, url, sha256 = sha256)
        entries["{}/{}".format(arch, filename)] = apk

    # `copied_dir`, not `symlinked_dir`: antlir2 bind-mounts this repo into
    # the build-appliance isolation, where symlinks pointing back at the
    # source tree (or the buck-out cache) would not resolve.
    repo_dir = ctx.actions.copied_dir("repo", entries)

    return [
        # `all_apks` is empty on purpose: `layer.bzl` only reads `repo_dir`.
        ApkRepoInfo(repo_dir = repo_dir, all_apks = []),
        DefaultInfo(repo_dir),
    ]

_wolfi_snapshot = rule(
    impl = _wolfi_snapshot_impl,
    attrs = {
        "apkindex": attrs.source(doc = "minimal APKINDEX.tar.gz for the vendored closure"),
        "arch": attrs.string(default = "x86_64"),
        "packages": attrs.list(
            attrs.tuple(attrs.string(), attrs.string(), attrs.string()),
            doc = "(filename, url, sha256) for every .apk in the closure",
        ),
    },
)

_wolfi_snapshot_rule = rule_with_default_target_platform(_wolfi_snapshot)

def wolfi_snapshot(name, snapshot, visibility = ["PUBLIC"]):
    """Assemble the vendored Wolfi snapshot into an `ApkRepoInfo` target.

    `snapshot` is the `WOLFI_SNAPSHOT` struct loaded from `wolfi.lock.bzl`.
    """
    _wolfi_snapshot_rule(
        name = name,
        apkindex = "{}/APKINDEX.tar.gz".format(snapshot.arch),
        arch = snapshot.arch,
        packages = [(p.filename, p.url, p.sha256) for p in snapshot.packages],
        visibility = visibility,
    )
