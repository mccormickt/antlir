# Wolfi `os` apk-repo snapshot

A vendored, hermetic snapshot of a curated slice of the
[Wolfi](https://wolfi.dev) `os` apk repository.

## What this is

`:os` (a `wolfi_snapshot` target) exposes a curated package closure as an
`ApkRepoInfo`. Consume it from an `image.layer`:

```python
image.layer(
    name = "...",
    default_os = "wolfi",
    # exclusive, hermetic — replaces the flavor's live URL repos:
    apk_repos = ["//antlir/antlir2/package_managers/apk/wolfi-snapshot:os"],
    features = [feature.apks_install(apks = ["busybox", "bc"])],
)
```

Use `apk_additional_repos` instead to add the snapshot *alongside* the
flavor's live repos rather than replacing them.

## What's checked in

Only two small files:

- `x86_64/APKINDEX.tar.gz` — a **minimal** index listing exactly the
  vendored closure and nothing else, so apk resolves only against
  packages we pinned (it cannot pick a provider we didn't ship). It is
  unsigned (we do not hold the `wolfi-signing` key); the apk driver
  reaches it with `--allow-untrusted`.
- `wolfi.lock.bzl` — the download manifest: every package's URL and
  sha256.

The `.apk` **blobs are not committed**. The `wolfi_snapshot` rule fetches
each one at build time with buck2's `download_file`, verified against the
sha256 in the lock and content-addressed in the buck cache. apk
*resolution* is fully hermetic (the minimal index is frozen); the blobs
are checksum-pinned and fetched once, then cached.

## Worked example: an offline Rust container

`:rust-container--layer` is a Wolfi image carrying a working Rust
toolchain (`rust` + `gcc`), resolved against `:os`. The `tests/`
directory's `rust-demo` test compiles and runs a real Rust program
inside it.

## Regenerating

`generate.py` fetches the live Wolfi APKINDEX, resolves the transitive
dependency closure of a curated root set (`ROOTS` in the script), and
rewrites `x86_64/APKINDEX.tar.gz` + `wolfi.lock.bzl`.

```sh
python3 generate.py --measure   # resolve + report, no downloads
python3 generate.py             # re-pin: checksum every package, write the lock
```

To add packages, extend `ROOTS` and re-run. The closure is validated —
generation fails if any dependency is unsatisfiable within it. Downloads
are cached under `.cache/` (git-ignored).

**`packages.wolfi.dev/os` is a rolling repo** — it deletes old package
revisions within weeks, so the pinned `download_file` URLs will
eventually 404. Re-run `generate.py` to re-pin when that happens. (To
freeze the snapshot against this churn instead, vendor the `.apk` files —
drop the `/x86_64/*.apk` line from `.gitignore` and have the rule consume
them as sources.)

## Scope

The curated root set covers the Wolfi build-appliance packages, the
packages the apk-feature tests install, and the Rust + C toolchain — a
curated closure (79 packages), not a full mirror.
