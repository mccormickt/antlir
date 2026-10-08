# Buck2 → Melange → APK → apko

This standalone Buck project builds signed APK packages and OCI images without
loading Antlir's Rust build graph. It is a working path for package and image
development, not a replacement for Antlir layers, VM appliances, or RPM support.

```
Online snapshot export                    Offline Buck actions
Wolfi signed indexes + APK payloads ───┬─→ Melange recipe → signed APK repository
                                      └────────────────┬────────────┘
                                                       ↓
                                                  apko image
                                                       ↓
                                             OCI layout + SPDX SBOM
```

## Run the example

Use Linux with native x86_64 or aarch64 workers, Python 3.11+, bubblewrap,
OpenSSL, util-linux (`unshare`), and DotSlash for the repository's `buck2`
launcher. The acceptance test also needs Python's `tarfile` extraction-filter
support (included in current Python 3.11 patch releases).
Unprivileged user and network namespaces, including nested user namespaces,
must work. Docker, a registry, and privileged host containers are not required.

Run the following from this directory. The tool archives need network access
on their first download; `tools.bzl` pins their versions and SHA-256 hashes.

```sh
BUCK2="$(git rev-parse --show-toplevel)/buck2"
APKO="$($BUCK2 build //:apko --show-full-json-output | python3 -c \
  'import json,sys; print(next(iter(json.load(sys.stdin).values())))')"
mkdir -p examples/state.local
test -f examples/state.local/test.rsa || \
  (umask 077; openssl genrsa -out examples/state.local/test.rsa 2048)

python3 snapshot.py \
  --apko "$APKO" --arch x86_64 \
  --repository https://packages.wolfi.dev/os \
  --key examples/wolfi-signing.rsa.pub \
  --package wolfi-baselayout --package busybox \
  --package gcc --package glibc-dev \
  --output examples/state.local/snapshot

"$BUCK2" build //examples:image --show-full-output
python3 -m unittest -v test_build
python3 verify.py
```

For a native aarch64 worker, export with `--arch aarch64` and set
`arch = aarch64` in an `[apk]` section of `.buckconfig.local`. This configuration
also applies to `verify.py`. Cross-compilation and QEMU execution are not set up.
Only x86_64 has been exercised here.

The example compiles `hello.c` through a shared Melange pipeline, emits a main
package and a documentation subpackage, and requests their exact versions in
the image. The image output contains `oci/`, `sbom/`, and `image.lock.json`.
The recipe output contains `packages/<arch>/`, a signed `APKINDEX.tar.gz`, its
public key, and `repository.json`. No private key is an output.

`verify.py` builds through Buck, runs two additional uncached package/image
builds, and compares all output file hashes. It then runs the binary from the
OCI filesystem, checks the subpackage, lock and SBOM, and tests rejection of
an incorrect key, a changed payload, an unavailable version, host network
access, and a source omitted from declared inputs.

## Retain the package world

`snapshot.py` is an explicit **network-enabled export step**, not a Buck build
action. It asks apko to resolve the requested root packages and retains:

- The original signed indexes, without generating an unsigned replacement.
- Every APK in the selected transitive closure, including its original signature.
- Reviewed public keys, the input package requests, and resolution provenance.
- A SHA-256 manifest of every retained file.

The exporter verifies the compressed APK streams against the resolved lock.
It then resolves again against the retained indexes with a fresh cache and no
network. If the selected versions/checksums changed, export fails. The output
appears only after verification; an existing output is never overwritten.

The example public key comes from
<https://packages.wolfi.dev/os/wolfi-signing.rsa.pub> and has SHA-256
`f0031424cf46f7db780ce63a45f0fd6aa6f85f601e6bb3b7a91fe3d4d5b7d2cc`.
Review key rotations explicitly. Keys are not fetched implicitly during builds.

**A lockfile or an upstream URL is not retention.** Keep the entire snapshot
in durable storage and pin its manifest or archive digest in reviewed source.
`examples/state.local` is ignored local development state, not that storage.
For shared builds, restore a reviewed snapshot before invoking Buck, or supply
a checksum-pinned archive target as `apk_snapshot.directory`. Do not regenerate
the snapshot on every build: rolling Wolfi repositories can remove old APKs.
Record the exported manifest hash alongside the retained archive.

This is a closure for selected roots, not a complete Wolfi mirror. The retained
index advertises other packages whose bytes might not be present. A new recipe
that requires them must use a new exported snapshot. There is no online fallback.
Use one snapshot for all build and runtime roots needed by a target graph, or
separate snapshots where repository version selection does not conflict.
Do not combine independent snapshots of the same rolling repository without
reviewing duplicate names and versions.

`resolution.json` records provenance; it is not an apko lockfile. The image's
lock uses stable sandbox paths and is intended for the declared build inputs,
not as a portable reference to the original online repository.

## Declare recipes and images

Load `apk_snapshot`, `apk_recipe`, and `apk_image` from `//:rules.bzl`.
`examples/BUCK` contains a complete graph. A recipe or image declares its config,
architecture, repositories, optional named `srcs`, and `source_date_epoch`.
Recipes also declare a development signing key.

Configurations are staged as `/source/config.yaml`. Declare all included config
files, source trees, patches, and shared pipeline directories in `srcs`, using
the paths the configuration expects. A `pipelines` directory is passed to
Melange. Directories and Buck-produced artifacts are supported. Recipe outputs
can be repositories for other recipes or images; Buck orders those dependencies.
`[spec]` subtargets expose action inputs for inspection and acceptance tests.

Keep repository and key lists out of the config; the rules append the declared
local inputs. Fetch source archives in separate checksum-pinned Buck download
targets, then declare them in `srcs`. Online Melange fetch steps cannot work
inside these actions. Paths outside declared source inputs are unavailable,
apart from the documented host-tool mounts below.

## Trust and execution limits

- Package/image actions run locally in a fresh bubblewrap user namespace with
  no network, no inherited environment, stable paths, and fresh temporary caches.
  The driver verifies manifests before invoking Melange or apko. Neither command
  is given a global `allow-untrusted` or `ignore-signatures` option.
- Public keys in a reviewed snapshot or declared recipe are trust roots. A
  manifest detects changed files; it does not establish who authorized the
  manifest or the keys. Store and review their external digest pins.
- The host kernel, `/usr`, Python, OpenSSL, bubblewrap, and passwd/group files
  remain host inputs, not pinned Buck artifacts. A controlled worker image is
  needed for reproducibility across machines. Recipe code must be trusted;
  this is not a service for executing hostile recipes with production secrets.
- Nested Melange containers need SETUID, SETGID, SETFCAP, and SYS_ADMIN inside
  the outer user namespace. No host-root privileges are granted by these flags.
- **Use development signing keys only.** A signing key is a Buck source input,
  and the recipe action can access it. Local-only execution does not make this
  a production key-management system. Keep release signing outside cached build
  actions, with a separate authorization and key-storage design.
- The fixed build epoch defaults to zero. Recipes can still introduce their own
  nondeterminism. Equality in this fixture is not a guarantee for every recipe.
- These outputs are OCI layouts. They are not loaded, pushed, signed as images,
  or deployed. Registry publication is a separate operation.

## Paths toward Antlir integration

1. **Package/image delivery:** retain versioned binary snapshots, pin worker
   images, add native aarch64 CI, and separate release signing. Import additional
   Melange recipes with declared offline source inputs. A full Wolfi source
   rebuild is not required to start building application packages.
2. **Antlir consumers:** restore the main repository's standalone Buck/Rust
   graph, then add a supported bridge from the OCI output into Antlir layers or
   VM appliances. Prove filesystem ownership, capabilities, package metadata,
   and script behavior at that boundary before using it for VM roots.
3. **Native APK features:** if Antlir must incrementally install APKs into parent
   layers, repair the original transaction/constraint handling and parent package
   state, replace the approximate resolver, and enforce signed repository trust.
   This is more work than the standalone path and has different semantics.

The adjacent native APK prototype is unchanged by these rules. Its unresolved
transaction, parent-state, trust, and package-script issues still apply. The
standalone project does not claim to repair them or the main Rust build graph.
