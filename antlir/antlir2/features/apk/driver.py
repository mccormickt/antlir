#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# Driver script for APK package manager operations inside the build appliance.
# Unlike the RPM/DNF driver which uses Python bindings, this uses the apk CLI
# directly via subprocess since APK has a simpler interface.

import json
import os
import re
import subprocess
import sys

_PKG_PIN_RE = re.compile(r"[<>=~]")


def _bare_pkg_name(spec):
    """Extract the bare package name from an apk install spec.

    apk's install syntax accepts `name`, `name=version`, `name>=version`,
    `name<version`, `name~version`, and `@tag/name`. The apk db stores
    only the bare name, so callers comparing requested vs installed need
    to normalize first.
    """
    if spec.startswith("@"):
        slash = spec.find("/")
        if slash != -1:
            spec = spec[slash + 1:]
    m = _PKG_PIN_RE.search(spec)
    if m:
        return spec[:m.start()]
    return spec


def _iter_installed_packages(install_root):
    """Yield (name, version, fields) tuples from `lib/apk/db/installed`.

    `fields` is a dict mapping single-letter keys to the list of values
    seen in the package's stanza (some keys repeat, e.g. `F:` for each
    folder). The caller can use it to inspect file records (`R:`),
    triggers (`t:`), and any other metadata without re-parsing.
    """
    db_path = os.path.join(install_root, "lib/apk/db/installed")
    if not os.path.isfile(db_path):
        return
    name = None
    version = None
    fields = {}
    with open(db_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                if name is not None:
                    yield name, version, fields
                name = None
                version = None
                fields = {}
                continue
            if len(line) < 2 or line[1] != ":":
                continue
            key = line[0]
            value = line[2:]
            if key == "P":
                name = value
            elif key == "V":
                version = value
            fields.setdefault(key, []).append(value)
    if name is not None:
        yield name, version, fields


def _missing_extracted_files(install_root, package_names):
    """Walk the apk db and lstat every file/dir owned by `package_names`.

    Returns a list of `(pkg_name, relative_path)` tuples for entries that
    the db says belong to the package but which are not present on disk.
    A truncated extraction (interrupted unpack, EIO during install) leaves
    the db record intact, so verifying via lstat is the only way to catch
    the inconsistency.
    """
    package_names = set(package_names)
    missing = []
    for name, _version, fields in _iter_installed_packages(install_root):
        if name not in package_names:
            continue
        current_dir = ""
        # Walk lines in their original order so each `R:` resolves against
        # the most recent `F:` directory. We re-parse for ordering since
        # `_iter_installed_packages` returned a flattened dict.
        db_path = os.path.join(install_root, "lib/apk/db/installed")
        in_pkg = False
        with open(db_path) as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    in_pkg = False
                    current_dir = ""
                    continue
                if len(line) < 2 or line[1] != ":":
                    continue
                key, value = line[0], line[2:]
                if key == "P":
                    in_pkg = (value == name)
                    current_dir = ""
                elif in_pkg and key == "F":
                    current_dir = value
                elif in_pkg and key == "R":
                    rel = os.path.join(current_dir, value) if current_dir else value
                    abs_path = os.path.join(install_root, rel)
                    try:
                        os.lstat(abs_path)
                    except OSError:
                        missing.append((name, rel))
        # The outer loop's iteration is just to filter package_names; we
        # already walked everything for this package above, so break out.
        package_names.discard(name)
        if not package_names:
            break
    return missing


def _busybox_in_db(install_root):
    """Return True if the busybox package is recorded as installed."""
    for name, _version, _fields in _iter_installed_packages(install_root):
        if name == "busybox":
            return True
    return False


def _installed_provide_names(install_root):
    """Every name the installed packages satisfy.

    Returns each installed package's own name plus every virtual name it
    provides. apk writes provides as a single `p:` stanza line holding a
    space-separated list of `name=version` tokens (e.g. `p:rust=1.95.0-r0
    cmd:cargo=1.95.0-r0 so:librustc_driver-...so=0`); the bare name
    (before `=`) is what a dependency — or an `apk add` install spec —
    resolves against. An install spec like `rust` is satisfied by the
    `rust-1.95` package via its `rust=...` provide, so a name-only check
    would miss it.
    """
    names = set()
    for name, _version, fields in _iter_installed_packages(install_root):
        names.add(name)
        for line_val in fields.get("p", []):
            for tok in line_val.split():
                names.add(tok.split("=", 1)[0])
    return names


def _emit_warning(message):
    json.dump({"warning": message}, sys.stdout)
    sys.stdout.write("\n")


def _synthesize_ca_certificates_bundle(install_root):
    """Recreate /etc/ssl/certs/ca-certificates.crt from individual cert files.

    The `ca-certificates-bundle` (and `ca-certificates`) package's
    post-install trigger concatenates every PEM in `/etc/ssl/certs/`
    into a single bundle. With `--no-scripts` the bundle is missing,
    breaking any process that uses the default OpenSSL ca-bundle path
    (curl, wget, python's `requests`, ...).
    """
    certs_dir = os.path.join(install_root, "etc/ssl/certs")
    if not os.path.isdir(certs_dir):
        return
    bundle_path = os.path.join(certs_dir, "ca-certificates.crt")
    pieces = []
    for fname in sorted(os.listdir(certs_dir)):
        if fname == "ca-certificates.crt":
            continue
        if not (fname.endswith(".pem") or fname.endswith(".crt")):
            continue
        src = os.path.join(certs_dir, fname)
        if os.path.isfile(src):
            with open(src) as f:
                pieces.append(f.read())
    if pieces:
        with open(bundle_path, "w") as f:
            for p in pieces:
                if not p.endswith("\n"):
                    p = p + "\n"
                f.write(p)


def _synthesize_ldconfig(install_root):
    """Run ldconfig against `install_root`.

    Both `glibc` and `musl` packages ship a post-install trigger that
    runs ldconfig, populating `/etc/ld.so.cache` so the dynamic linker
    can find shared objects without scanning every directory in
    LD_LIBRARY_PATH. Without it, dynamically-linked binaries inside the
    layer can fail to resolve their dependencies. `ldconfig -r <root>`
    treats the argument as the new root for cache generation and library
    discovery, so this works without chrooting.
    """
    result = subprocess.run(
        ["ldconfig", "-r", install_root],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Fall back to an empty cache so ld.so doesn't fail on a missing
        # file. ld.so tolerates an empty cache and will fall back to
        # directory scanning.
        cache = os.path.join(install_root, "etc/ld.so.cache")
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "wb") as f:
            f.write(b"")
        _emit_warning(
            f"ldconfig -r {install_root} exited {result.returncode}; wrote empty "
            f"ld.so.cache. stderr: {result.stderr.strip()}"
        )


_SYNTHESIZED_TRIGGERS = {
    "ca-certificates-bundle": _synthesize_ca_certificates_bundle,
    "ca-certificates": _synthesize_ca_certificates_bundle,
    "glibc": _synthesize_ldconfig,
    "musl": _synthesize_ldconfig,
}


def synthesize_post_install_triggers(install_root):
    """Re-run the post-install actions of well-known packages.

    `apk add --no-scripts` skips every package's post-install trigger
    because rootless mode can't chroot into the install root to run
    arbitrary scripts. For a small set of well-known packages we
    synthesize the trigger's effect directly. Any other package that
    declares a trigger (`t:` lines in the apk db) emits a warning event
    so the build log surfaces the gap.
    """
    handled = set()
    triggered_unhandled = []
    for name, _version, fields in _iter_installed_packages(install_root):
        if name in _SYNTHESIZED_TRIGGERS and name not in handled:
            try:
                _SYNTHESIZED_TRIGGERS[name](install_root)
            except OSError as e:
                _emit_warning(
                    f"synthesize trigger for {name} failed: {e}"
                )
            handled.add(name)
        if name not in _SYNTHESIZED_TRIGGERS and "t" in fields:
            triggered_unhandled.append(name)
    for name in triggered_unhandled:
        _emit_warning(
            f"package {name} declares a post-install trigger that the apk "
            f"driver does not synthesize; --no-scripts skipped it"
        )


def setup_apk_config(install_root, spec):
    """Configure APK repositories and signing keys in the install root.

    The repos spec value can be:
    - A path to a JSON file containing a list of repo URL strings
      (produced by plan.bzl's write_json of apk_repos)
    - A path to a directory containing repo URL files and an optional
      keys/ subdirectory with signing keys
    - None (no repos configured)
    """
    apk_dir = os.path.join(install_root, "etc/apk")
    os.makedirs(apk_dir, exist_ok=True)

    # apk refuses to operate on a root that has no database state. Create an
    # empty `world` file (list of explicitly-installed packages) and the
    # `installed` database file so `apk add --simulate` has something to open.
    world_path = os.path.join(apk_dir, "world")
    if not os.path.exists(world_path):
        open(world_path, "a").close()
    db_dir = os.path.join(install_root, "lib/apk/db")
    os.makedirs(db_dir, exist_ok=True)
    installed_path = os.path.join(db_dir, "installed")
    if not os.path.exists(installed_path):
        open(installed_path, "a").close()
    # apk also consults /var/cache/apk, which must exist for --no-cache to be
    # a no-op rather than a failure on some versions.
    os.makedirs(os.path.join(install_root, "var/cache/apk"), exist_ok=True)

    # Copy any signing keys from the build appliance into the install root.
    # apk verifies APKINDEX signatures against keys in /etc/apk/keys, which
    # must be present before any fetch.
    ba_keys = "/etc/apk/keys"
    if os.path.isdir(ba_keys):
        dst_keys = os.path.join(apk_dir, "keys")
        os.makedirs(dst_keys, exist_ok=True)
        for key in os.listdir(ba_keys):
            src = os.path.join(ba_keys, key)
            dst = os.path.join(dst_keys, key)
            if os.path.isfile(src) and not os.path.exists(dst):
                with open(src, "rb") as sf, open(dst, "wb") as df:
                    df.write(sf.read())

    repos_path = spec.get("repos")
    if repos_path is None:
        return

    if os.path.isfile(repos_path):
        # repos is a JSON file of URL strings (from plan.bzl write_json)
        with open(repos_path) as f:
            urls = json.load(f)
        if not isinstance(urls, list):
            raise RuntimeError(
                f"repos JSON file should contain a list of URLs, got: {type(urls).__name__}"
            )
        repos_file = os.path.join(apk_dir, "repositories")
        with open(repos_file, "w") as f:
            for url in urls:
                url = str(url).strip()
                if url:
                    f.write(url + "\n")
    elif os.path.isdir(repos_path):
        # repos is a directory containing repo URL files and optional keys/
        repos_file = os.path.join(apk_dir, "repositories")
        with open(repos_file, "w") as f:
            for entry in sorted(os.listdir(repos_path)):
                if entry == "keys":
                    continue
                repo_entry = os.path.join(repos_path, entry)
                if os.path.isfile(repo_entry):
                    with open(repo_entry) as rf:
                        for line in rf:
                            line = line.strip()
                            if line:
                                f.write(line + "\n")
                elif os.path.isdir(repo_entry):
                    # apk reads /etc/apk/repositories from many cwd contexts
                    # (driver, fetch, install). Resolve to an absolute path
                    # that's valid inside the build appliance so the entry
                    # works regardless of which subprocess inherits which
                    # working directory.
                    f.write(os.path.realpath(repo_entry) + "\n")

        # Install signing keys if present
        keys_src = os.path.join(repos_path, "keys")
        if os.path.isdir(keys_src):
            keys_dst = os.path.join(apk_dir, "keys")
            os.makedirs(keys_dst, exist_ok=True)
            for key in os.listdir(keys_src):
                src = os.path.join(keys_src, key)
                dst = os.path.join(keys_dst, key)
                if os.path.isfile(src) and not os.path.exists(dst):
                    with open(src, "rb") as sf:
                        with open(dst, "wb") as df:
                            df.write(sf.read())
    else:
        raise RuntimeError(f"repos path does not exist: {repos_path}")


def setup_mounts(install_root):
    """Create and recursively bind-mount /tmp, /proc, and /dev into the install root."""
    for path, mode in [("/tmp", 0o1777), ("/proc", 0o555), ("/dev", 0o755)]:
        dst = os.path.join(install_root, path.lstrip("/"))
        existed = os.path.exists(dst)
        os.makedirs(dst, exist_ok=True, mode=mode)
        if not existed:
            os.chmod(dst, mode)
        result = subprocess.run(
            ["mount", "--rbind", path, dst],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to bind-mount {path} -> {dst}: {result.stderr}")


def run_apk(install_root, args, check=True):
    """Run an apk command with the given install root.

    If check is True (default), raises RuntimeError on non-zero exit.
    """
    # `--allow-untrusted`: locally-vendored apk repositories (the wolfi
    # snapshot, and any `apk_repo` built without a `signing_key`) ship an
    # unsigned APKINDEX, which apk would otherwise refuse to install from.
    # Signed URL repos keep working unchanged — the flag only relaxes
    # signature *enforcement*, it does not weaken anything that is signed.
    cmd = ["apk", "--root", install_root, "--no-progress", "--allow-untrusted"] + args
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf8",
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"apk command failed (exit {result.returncode}): "
            f"{' '.join(cmd)}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result


def resolve(spec):
    """Resolve mode: simulate the transaction and report what would change."""
    install_root = spec["install_root"]
    items = spec["items"]

    setup_apk_config(install_root, spec)

    install_pkgs = []
    remove_pkgs = []
    for item in items:
        action = item["action"]
        apk_spec = item["apk"]
        if action == "install":
            if "subject" in apk_spec and apk_spec["subject"]:
                install_pkgs.append(apk_spec["subject"])
            elif "src" in apk_spec and apk_spec["src"]:
                install_pkgs.append(os.path.realpath(apk_spec["src"]))
            else:
                raise RuntimeError(
                    f"install item has neither subject nor src: {item}"
                )
        elif action in ("remove", "remove_if_exists"):
            if "subject" in apk_spec and apk_spec["subject"]:
                remove_pkgs.append(apk_spec["subject"])
            else:
                raise RuntimeError(
                    f"remove item has no subject: {item}"
                )

    resolved_install = []
    resolved_remove = []

    if install_pkgs:
        result = run_apk(
            install_root,
            ["add", "--simulate", "--no-cache"] + install_pkgs,
            check=False,
        )
        # Parse simulation output to extract package names.
        # APK simulate output lines look like:
        #   (1/3) Installing musl (1.2.4-r0)
        # Package replacements may also produce:
        #   (2/2) Purging old-pkg (0.9-r0)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("(") and "Installing" in line:
                parts = line.split("Installing", 1)
                if len(parts) == 2:
                    pkg_info = parts[1].strip()
                    if "(" in pkg_info and pkg_info.endswith(")"):
                        name = pkg_info[: pkg_info.rindex("(")].strip()
                        resolved_install.append(name)
            elif line.startswith("(") and "Purging" in line:
                parts = line.split("Purging", 1)
                if len(parts) == 2:
                    pkg_info = parts[1].strip()
                    if "(" in pkg_info and pkg_info.endswith(")"):
                        name = pkg_info[: pkg_info.rindex("(")].strip()
                        resolved_remove.append(name)

        if result.returncode != 0:
            raise RuntimeError(
                f"apk simulate failed (exit {result.returncode}): {result.stderr}"
                + (f"\nPartially resolved: {resolved_install}" if resolved_install else "")
            )

    if remove_pkgs:
        # `--no-cache` makes apk fetch the index fresh instead of failing
        # with "opening from cache: No such file or directory" when the
        # parent layer's cache directory isn't carried into the resolve.
        result = run_apk(
            install_root,
            ["del", "--simulate", "--no-cache"] + remove_pkgs,
            check=False,
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("(") and "Purging" in line:
                parts = line.split("Purging", 1)
                if len(parts) == 2:
                    pkg_info = parts[1].strip()
                    if "(" in pkg_info and pkg_info.endswith(")"):
                        name = pkg_info[: pkg_info.rindex("(")].strip()
                        resolved_remove.append(name)

        if result.returncode != 0:
            # apk frequently exits non-zero in rootless mode because of
            # ownership/cap setting limitations even when the simulation
            # itself completed correctly. If we parsed Purging lines for
            # every requested hard-remove, treat the failure as cosmetic.
            hard_removes = {
                item["apk"]["subject"]
                for item in items
                if item["action"] == "remove"
            }
            if hard_removes and not hard_removes.issubset(set(resolved_remove)):
                raise RuntimeError(
                    f"apk del --simulate failed (exit {result.returncode}): "
                    f"{result.stderr}\nstdout: {result.stdout}"
                )

    json.dump(
        {
            "transaction_resolved": {
                "install": resolved_install,
                "remove": resolved_remove,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def install_busybox_applets(install_root):
    """Recreate the symlinks that `busybox --install` would have made.

    Wolfi/Alpine ship a busybox package whose post-install trigger does
    `busybox --install -s` to populate /bin, /sbin, /usr/bin, /usr/sbin
    with one symlink per applet. We can't run that trigger rootless, so
    we read the applet lists from `/etc/busybox-paths.d/` (every file in
    the directory is a list of root-relative paths, one per line) and
    create the symlinks directly.

    If busybox is recorded in the apk db but no path lists are present,
    raise — that signals either a Wolfi packaging change we haven't
    accounted for or a partial install that would silently leave the
    layer without `/bin/sh`.
    """
    paths_dir = os.path.join(install_root, "etc/busybox-paths.d")
    bb_relative = "bin/busybox"
    busybox_in_db = _busybox_in_db(install_root)

    if not os.path.isdir(paths_dir):
        if busybox_in_db:
            raise RuntimeError(
                f"busybox is recorded in {install_root}/lib/apk/db/installed "
                f"but {paths_dir} does not exist; cannot synthesize applet "
                f"symlinks (busybox post-install trigger was skipped via "
                f"--no-scripts)."
            )
        return

    paths_files = sorted(
        os.path.join(paths_dir, name)
        for name in os.listdir(paths_dir)
        if os.path.isfile(os.path.join(paths_dir, name))
    )
    if not paths_files:
        if busybox_in_db:
            raise RuntimeError(
                f"busybox is recorded in {install_root}/lib/apk/db/installed "
                f"but {paths_dir} is empty; cannot synthesize applet symlinks."
            )
        return

    if not os.path.isfile(os.path.join(install_root, bb_relative)):
        # The busybox binary itself isn't on disk yet. This happens for
        # transactions that record busybox in the db but extract no
        # files (caught by `_missing_extracted_files` upstream).
        return

    applet_paths = []
    for paths_file in paths_files:
        with open(paths_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    applet_paths.append(line)

    for path in applet_paths:
        # busybox-paths.d entries are root-relative paths like
        # "usr/bin/sh" or "bin/cat" with no leading slash.
        dst = os.path.join(install_root, path)
        if os.path.lexists(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # Compute relative target back to /bin/busybox from the symlink's
        # directory so the link works regardless of mount layout.
        depth = path.count("/")
        target = ("../" * depth) + bb_relative
        try:
            os.symlink(target, dst)
        except OSError:
            pass


def bootstrap_apk_root(install_root):
    """Initialize the apk db and (best-effort) install baselayout.

    Splits the bootstrap into two distinct apk invocations:

      1. `apk add --initdb` — required. Creates `/lib/apk/db/{installed,
         scripts.tar, triggers}` so subsequent `apk add` calls have a
         working state directory. Hard failure here is fatal.

      2. `apk add baselayout` — best effort. The wolfi/alpine baselayout
         packages own top-level symlinks like `/lib -> usr/lib`. With
         `--no-scripts` and rootless mode, apk's atomic-rename of `.apk.*`
         to `lib` collides with the `lib/apk/db` tree just created by
         `--initdb` (rename(2) onto a non-empty directory returns ENOTEMPTY).
         A surface fix would require extracting baselayout's contents
         manually; until that lands, treat the failure as a known hazard
         and emit a warning event so the build log surfaces it.

    The --initdb-succeeded-but-baselayout-failed state still lets later
    `apk add` calls work for packages that don't depend on `/lib` being
    a symlink (e.g. busybox, bc, most static-link binaries).
    """
    bootstrap_marker = os.path.join(install_root, "lib/apk/db/installed")
    if (
        os.path.exists(bootstrap_marker)
        and os.path.getsize(bootstrap_marker) > 0
    ):
        return

    initdb = run_apk(
        install_root,
        ["add", "--no-cache", "--no-scripts", "--initdb"],
        check=False,
    )
    if initdb.returncode != 0:
        raise RuntimeError(
            f"bootstrap_apk_root: `apk add --initdb` failed in {install_root} "
            f"(exit {initdb.returncode}).\n"
            f"stdout: {initdb.stdout}\n"
            f"stderr: {initdb.stderr}"
        )

    attempts = []
    for baselayout in ("wolfi-baselayout", "alpine-baselayout"):
        result = run_apk(
            install_root,
            ["add", "--no-cache", "--no-scripts", baselayout],
            check=False,
        )
        if result.returncode == 0:
            return
        attempts.append((baselayout, result))

    detail = "\n".join(
        f"  - {pkg}: exit {res.returncode}; stderr={res.stderr.strip()!r}"
        for pkg, res in attempts
    )
    _emit_warning(
        f"bootstrap_apk_root: baselayout install failed for both candidates; "
        f"the apk db is initialized but `/lib`, `/sbin`, and other "
        f"baselayout-owned paths may be plain directories rather than "
        f"symlinks. Subsequent `apk add` calls for packages that need "
        f"the symlink farm will fail. Attempts:\n{detail}"
    )


def run(spec):
    """Run mode: actually install/remove packages."""
    install_root = spec["install_root"]
    items = spec["items"]

    setup_apk_config(install_root, spec)
    setup_mounts(install_root)
    bootstrap_apk_root(install_root)

    # Group items by action
    install_pkgs = []
    remove_pkgs = []
    remove_if_exists_pkgs = []

    for item in items:
        action = item["action"]
        apk_spec = item["apk"]
        if action == "install":
            if "subject" in apk_spec and apk_spec["subject"]:
                install_pkgs.append(apk_spec["subject"])
            elif "src" in apk_spec and apk_spec["src"]:
                install_pkgs.append(os.path.realpath(apk_spec["src"]))
            else:
                raise RuntimeError(
                    f"install item has neither subject nor src: {item}"
                )
        elif action == "remove":
            if "subject" in apk_spec and apk_spec["subject"]:
                remove_pkgs.append(apk_spec["subject"])
            else:
                raise RuntimeError(
                    f"remove item has no subject: {item}"
                )
        elif action == "remove_if_exists":
            if "subject" in apk_spec and apk_spec["subject"]:
                remove_if_exists_pkgs.append(apk_spec["subject"])
            else:
                raise RuntimeError(
                    f"remove_if_exists item has no subject: {item}"
                )

    if install_pkgs:
        # `--no-scripts` skips package pre/post-install triggers. Triggers
        # in apk normally chroot into the install root and run binaries
        # there (e.g. busybox's trigger creates the /bin/sh symlink farm
        # via `busybox --install`). In a rootless userns the chroot is
        # not possible, so we let apk extract the package contents and
        # synthesize the most important triggers ourselves below.
        result = run_apk(
            install_root,
            ["add", "--no-cache", "--no-scripts"] + install_pkgs,
            check=False,
        )

        # Replicate busybox's post-install trigger: create symlinks like
        # /bin/sh -> busybox, /bin/cat -> busybox, etc. for every applet
        # the busybox binary advertises. Without this the layer is missing
        # /bin/sh and most of /bin, which breaks any image_sh_test or
        # interactive shell that follows.
        install_busybox_applets(install_root)
        # Synthesize a small set of well-known post-install triggers
        # (ca-certificates bundle, ldconfig). Surface a warning for any
        # other package that declares a trigger we don't recreate.
        synthesize_post_install_triggers(install_root)
        # In rootless mode apk often reports "N error(s)" because it
        # cannot set non-root ownership or capabilities on extracted
        # files. Don't trust the exit code blindly — and don't trust
        # the apk db either, since a truncated extraction can leave a
        # complete db record but missing files. Verify both: each
        # requested package is in the db, and every file the db says
        # belongs to it actually exists on disk.
        if result.returncode != 0:
            bare_names = {_bare_pkg_name(s) for s in install_pkgs}
            missing_files = _missing_extracted_files(install_root, bare_names)
            if missing_files:
                preview = missing_files[:20]
                raise RuntimeError(
                    f"apk add failed (exit {result.returncode}); files "
                    f"recorded in the db are missing from disk "
                    f"(showing first {len(preview)} of {len(missing_files)}): "
                    f"{preview}\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )
            verify = run_apk(
                install_root,
                ["info", "-e"] + install_pkgs,
                check=False,
            )
            installed = set(verify.stdout.split())
            # `apk info -e` matches installed package *names*; an install
            # spec can also be a virtual name some package provides (e.g.
            # `rust` is provided by the `rust-1.95` package via `p:rust=`).
            # Accept either, so a provided-name spec still counts as landed.
            installed |= _installed_provide_names(install_root)
            missing_pkgs = bare_names - installed
            if missing_pkgs:
                raise RuntimeError(
                    f"apk add failed (exit {result.returncode}); these "
                    f"packages did not land in the install root: "
                    f"{sorted(missing_pkgs)}\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("(") and "Installing" in line:
                parts = line.split("Installing", 1)
                if len(parts) == 2:
                    pkg_info = parts[1].strip()
                    if "(" in pkg_info and pkg_info.endswith(")"):
                        name = pkg_info[: pkg_info.rindex("(")].strip()
                        version = pkg_info[
                            pkg_info.rindex("(") + 1 : pkg_info.rindex(")")
                        ]
                        json.dump(
                            {
                                "package_installed": {
                                    "name": name,
                                    "version": version,
                                }
                            },
                            sys.stdout,
                        )
                        sys.stdout.write("\n")

    if remove_pkgs:
        # `--no-cache` avoids "opening from cache: No such file or directory"
        # when the parent layer's cache is not carried in. `--no-scripts`
        # skips post-uninstall hooks that would chroot into the install
        # root and run binaries there (impossible in a rootless userns).
        result = run_apk(
            install_root,
            ["del", "--no-cache", "--no-scripts"] + remove_pkgs,
            check=False,
        )
        # Treat exit-non-zero as cosmetic if the requested packages are
        # actually gone from the apk db (apk frequently returns 1 because
        # of trigger errors on rootless installs).
        if result.returncode != 0:
            verify = run_apk(
                install_root,
                ["info", "-e"] + remove_pkgs,
                check=False,
            )
            still_installed = set(verify.stdout.split()) & set(remove_pkgs)
            if still_installed:
                raise RuntimeError(
                    f"apk del failed (exit {result.returncode}); these "
                    f"packages remain installed: {sorted(still_installed)}\n"
                    f"stdout: {result.stdout}\n"
                    f"stderr: {result.stderr}"
                )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("(") and "Purging" in line:
                parts = line.split("Purging", 1)
                if len(parts) == 2:
                    pkg_info = parts[1].strip()
                    if "(" in pkg_info and pkg_info.endswith(")"):
                        name = pkg_info[: pkg_info.rindex("(")].strip()
                        json.dump(
                            {"package_removed": {"name": name}},
                            sys.stdout,
                        )
                        sys.stdout.write("\n")

    if remove_if_exists_pkgs:
        result = run_apk(
            install_root,
            ["del", "--no-cache", "--no-scripts"] + remove_if_exists_pkgs,
            check=False,
        )
        if result.returncode != 0:
            json.dump(
                {"warning": f"apk del exited {result.returncode} for "
                            f"remove_if_exists: {result.stderr}"},
                sys.stdout,
            )
            sys.stdout.write("\n")
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("(") and "Purging" in line:
                parts = line.split("Purging", 1)
                if len(parts) == 2:
                    pkg_info = parts[1].strip()
                    if "(" in pkg_info and pkg_info.endswith(")"):
                        name = pkg_info[: pkg_info.rindex("(")].strip()
                        json.dump(
                            {"package_removed": {"name": name}},
                            sys.stdout,
                        )
                        sys.stdout.write("\n")


def main():
    spec = json.load(sys.stdin)
    mode = spec["mode"]
    if mode == "resolve":
        resolve(spec)
    elif mode == "run":
        run(spec)
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
