#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""Generate the vendored Wolfi `os` apk-repo snapshot.

Fetches the live Wolfi APKINDEX, resolves the transitive dependency
closure of a curated root package set, and writes a small, checked-in
manifest alongside this script:

  - x86_64/APKINDEX.tar.gz  a minimal, unsigned index listing exactly the
                            vendored closure and nothing else, so apk
                            resolves only against packages we pinned.
  - wolfi.lock.bzl          the download manifest — every package's URL
                            and sha256. The `.apk` blobs are NOT checked
                            in; the `wolfi_snapshot` rule fetches them at
                            build time with buck2's checksum-verified
                            `download_file`.

Modes:
  --measure   parse the index, resolve the closure, print package count
              and total `.apk` size. No downloads of package files.
  (default)   do --measure, then download every `.apk` (cached under
              `.cache/`) to checksum it, and write the lock + index.

Re-run (default mode) to re-pin against the current Wolfi repo. To add
packages, extend ROOTS and re-run.

Note: `packages.wolfi.dev/os` is a rolling repo — it deletes old package
revisions within weeks, so the pinned `download_file` URLs will
eventually 404. Re-run this script to re-pin when that happens.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import urllib.request
from datetime import datetime, timezone

WOLFI_REPO = "https://packages.wolfi.dev/os"
ARCH = "x86_64"

# Curated root set. Closure of these is what gets vendored.
#   - the Wolfi build-appliance packages (//flavor/wolfi:wolfi-base.apko.yaml)
#   - the apk-feature tests' install set (busybox is already a BA package)
#   - the Rust toolchain (`rust`) plus a C toolchain (`gcc`): rustc shells
#     out to `cc` to link, so a usable offline Rust container needs both.
ROOTS = [
    "wolfi-baselayout",
    "ca-certificates-bundle",
    "busybox",
    "apk-tools",
    "python-3",
    "util-linux",
    "util-linux-misc",
    "mount",
    "bc",
    "rust",
    "gcc",
]

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(REPO_DIR, ".cache")

# --------------------------------------------------------------------------
# apk version comparison
#
# A faithful-enough port of apk-tools' version ordering for the version
# shapes Wolfi actually ships: NUM(.NUM)* LETTER? (_SUFFIX NUM?)* (~HASH)?
# (-rNUM)?. Pre-release suffixes sort below a bare version; post-release
# suffixes sort above it.
# --------------------------------------------------------------------------

_SUFFIX_RANK = {
    "alpha": 0, "beta": 1, "pre": 2, "rc": 3,
    "": 4,  # a bare version sorts between rc and the post-release suffixes
    "cvs": 5, "svn": 6, "git": 7, "hg": 8, "p": 9,
}


def _version_key(v):
    """Return a tuple that orders apk versions when compared natively."""
    rel = 0
    m = re.search(r"-r(\d+)$", v)
    if m:
        rel = int(m.group(1))
        v = v[: m.start()]

    commit = ""
    if "~" in v:
        v, commit = v.split("~", 1)

    suffixes = []
    while True:
        m = re.search(r"_([a-z]+)(\d*)$", v)
        if not m:
            break
        suffixes.insert(0, (_SUFFIX_RANK.get(m.group(1), 4), int(m.group(2) or 0)))
        v = v[: m.start()]
    if not suffixes:
        suffixes = [(_SUFFIX_RANK[""], 0)]

    letter = ""
    m = re.match(r"^(.*\d)([a-z])$", v)
    if m:
        letter, v = m.group(2), m.group(1)

    nums = [int(p) if p.isdigit() else 0 for p in v.split(".")]
    return (nums, letter, suffixes, rel, commit)


# --------------------------------------------------------------------------
# APKINDEX parsing
# --------------------------------------------------------------------------

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "rules_apk-snapshot/1"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - retry any transient failure
            last = e
    raise RuntimeError(f"failed to fetch {url}: {last}")


def parse_apkindex(raw_targz):
    """Parse APKINDEX.tar.gz -> list of stanza dicts.

    Repeated keys (D, p) become lists; single keys stay scalars. Each
    stanza also carries `_raw`: its verbatim block text, so the minimal
    index can be re-emitted from Wolfi's own stanzas byte-for-byte.
    """
    with tarfile.open(fileobj=io.BytesIO(raw_targz), mode="r:gz") as tf:
        member = tf.extractfile("APKINDEX")
        text = member.read().decode("utf-8")

    stanzas = []
    cur = {}
    raw_lines = []
    for line in text.split("\n"):
        if not line:
            if cur:
                cur["_raw"] = "\n".join(raw_lines)
                stanzas.append(cur)
                cur = {}
                raw_lines = []
            continue
        if len(line) < 2 or line[1] != ":":
            continue
        raw_lines.append(line)
        key, val = line[0], line[2:]
        if key in ("D", "p"):
            cur[key] = val.split()
        else:
            cur[key] = val
    if cur:
        cur["_raw"] = "\n".join(raw_lines)
        stanzas.append(cur)
    return stanzas


def extract_description(raw_targz):
    """Return the DESCRIPTION member of an APKINDEX.tar.gz, or None."""
    with tarfile.open(fileobj=io.BytesIO(raw_targz), mode="r:gz") as tf:
        try:
            member = tf.extractfile("DESCRIPTION")
        except KeyError:
            return None
        return member.read() if member is not None else None


def build_minimal_index(chosen, description):
    """Build an unsigned APKINDEX.tar.gz holding only the closure's stanzas.

    The snapshot pins apk to exactly this package set: every dependency
    token is satisfied by precisely the providers we resolved, so apk
    cannot select — and then fail to find — a package we didn't pin.

    The result is a plain gzipped tar of `APKINDEX` + `DESCRIPTION`, with
    no `.SIGN.*` member: it is unsigned, and consumed via the apk driver's
    `--allow-untrusted`. Built deterministically (fixed mtimes) so an
    unchanged closure reproduces a byte-identical file.
    """
    blocks = [chosen[name]["_raw"] for name in sorted(chosen)]
    apkindex = ("\n\n".join(blocks) + "\n").encode("utf-8")

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for member_name, payload in (
            ("APKINDEX", apkindex),
            ("DESCRIPTION", description),
        ):
            info = tarfile.TarInfo(member_name)
            info.size = len(payload)
            info.mtime = 0
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(payload))
    return gzip.compress(tar_buf.getvalue(), mtime=0)


def build_indexes(stanzas):
    """Return (by_name, providers).

    by_name:   package name -> highest-version stanza
    providers: provided token -> list of stanzas providing it
    """
    by_name = {}
    providers = {}
    for st in stanzas:
        name = st.get("P")
        if not name:
            continue
        prev = by_name.get(name)
        if prev is None or _version_key(st["V"]) > _version_key(prev["V"]):
            by_name[name] = st
        providers.setdefault(name, []).append(st)
        for tok in st.get("p", []):
            pname = tok.split("=", 1)[0]
            providers.setdefault(pname, []).append(st)
    return by_name, providers


def _dep_name(tok):
    """Bare provided-name a `D:` token depends on, or None for conflicts."""
    tok = tok.strip()
    if not tok or tok.startswith("!"):
        return None
    for i, ch in enumerate(tok):
        if ch in "<>=~":
            return tok[:i]
    return tok


def _best_provider(token, candidates):
    """Pick the provider apk would most likely choose for `token`."""
    # A real package (P: == token) always wins over a virtual provide.
    real = [c for c in candidates if c.get("P") == token]
    pool = real or candidates

    def rank(st):
        return (
            int(st.get("k", 0)),               # provider_priority
            _version_key(st.get("V", "0")),
        )

    return max(pool, key=rank)


def resolve_closure(by_name, providers):
    """BFS the dependency closure of ROOTS. Return (chosen, unresolved)."""
    chosen = {}
    unresolved = set()

    def resolve(tok):
        st = by_name.get(tok)
        if st is not None:
            return st
        cand = providers.get(tok)
        if cand:
            return _best_provider(tok, cand)
        return None

    queue = []
    for root in ROOTS:
        st = resolve(root)
        if st is None:
            raise SystemExit(f"ERROR: root package '{root}' not found in the Wolfi index")
        queue.append(st)

    while queue:
        st = queue.pop()
        name = st["P"]
        if name in chosen:
            continue
        chosen[name] = st
        for dep in st.get("D", []):
            dn = _dep_name(dep)
            if dn is None:
                continue
            dep_st = resolve(dn)
            if dep_st is None:
                unresolved.add(dn)
            elif dep_st["P"] not in chosen:
                queue.append(dep_st)

    return chosen, unresolved


def validate_closure(chosen):
    """Every non-conflict `D:` token must resolve within the closure."""
    provided = set(chosen)
    for st in chosen.values():
        for tok in st.get("p", []):
            provided.add(tok.split("=", 1)[0])
    missing = []
    for name, st in sorted(chosen.items()):
        for dep in st.get("D", []):
            dn = _dep_name(dep)
            if dn is not None and dn not in provided:
                missing.append((name, dep))
    return missing


def write_lock_bzl(path, *, source, arch, generated, index_sha256, packages):
    """Write `wolfi.lock.bzl`, the Starlark download manifest `BUCK` loads.

    Emitted as `.bzl` (not JSON) so `BUCK` can `load()` it directly. The
    `.apk` blobs are never checked in — this records each one's URL and
    sha256 so the `wolfi_snapshot` rule can fetch them with buck2's
    checksum-verified `download_file`.
    """
    lines = [
        # split so this generator is not itself flagged as generated.
        "# " + "@" + "generated by generate.py -- do not edit by hand.",
        "# Download manifest for the vendored Wolfi `os` apk-repo snapshot;",
        "# `wolfi_snapshot` fetches each .apk with a checksum-verified",
        "# `download_file`. Regenerate with `generate.py` (see README.md).",
        "",
        "WOLFI_SNAPSHOT = struct(",
        "    source = {},".format(json.dumps(source)),
        "    arch = {},".format(json.dumps(arch)),
        "    generated = {},".format(json.dumps(generated)),
        "    apkindex_sha256 = {},".format(json.dumps(index_sha256)),
        "    packages = [",
    ]
    for p in packages:
        lines += [
            "        struct(",
            "            name = {},".format(json.dumps(p["name"])),
            "            version = {},".format(json.dumps(p["version"])),
            "            filename = {},".format(json.dumps(p["filename"])),
            "            url = {},".format(json.dumps(p["url"])),
            "            sha256 = {},".format(json.dumps(p["sha256"])),
            "        ),",
        ]
    lines += ["    ],", ")", ""]
    with open(path, "w") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--measure", action="store_true",
                    help="resolve and report the closure; do not download packages")
    args = ap.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    index_url = f"{WOLFI_REPO}/{ARCH}/APKINDEX.tar.gz"
    print(f"fetching {index_url}", file=sys.stderr)
    index_raw = fetch(index_url)

    stanzas = parse_apkindex(index_raw)
    by_name, providers = build_indexes(stanzas)
    print(f"index: {len(stanzas)} stanzas, {len(by_name)} unique packages", file=sys.stderr)

    chosen, unresolved = resolve_closure(by_name, providers)
    missing = validate_closure(chosen)

    total = sum(int(st.get("S", 0)) for st in chosen.values())
    print(f"\nclosure: {len(chosen)} packages, {total / 1024 / 1024:.1f} MiB of .apk files")
    print(f"roots: {' '.join(ROOTS)}")
    if unresolved:
        print(f"\nWARNING: {len(unresolved)} unresolved dep tokens: {sorted(unresolved)}")
    if missing:
        print(f"\nWARNING: {len(missing)} unsatisfied deps inside the closure:")
        for pkg, dep in missing[:40]:
            print(f"  {pkg} -> {dep}")

    biggest = sorted(chosen.values(), key=lambda s: int(s.get("S", 0)), reverse=True)[:15]
    print("\nlargest packages:")
    for st in biggest:
        print(f"  {int(st['S']) / 1024:8.0f} KiB  {st['P']}-{st['V']}")

    if args.measure:
        return

    # The minimal index names only the closure; a package whose deps are
    # not also in the closure would make apk fail at install time. Refuse
    # to vendor a snapshot that is not self-contained.
    if missing or unresolved:
        raise SystemExit(
            "ERROR: dependency closure is not self-contained (see warnings "
            "above); fix ROOTS or the resolver before vendoring"
        )

    # --- generate mode: write the minimal index + the download manifest ---
    # Only the index is committed; the `.apk` blobs are fetched at build
    # time by the `wolfi_snapshot` rule. Clear x86_64/ first so a re-run
    # drops any stale `.apk`s an earlier (vendoring) revision left behind.
    arch_dir = os.path.join(REPO_DIR, ARCH)
    if os.path.isdir(arch_dir):
        shutil.rmtree(arch_dir)
    os.makedirs(arch_dir)

    description = extract_description(index_raw) or b"wolfi os apk-repo snapshot\n"
    minimal_index = build_minimal_index(chosen, description)
    with open(os.path.join(arch_dir, "APKINDEX.tar.gz"), "wb") as f:
        f.write(minimal_index)

    # Download every package (cached under .cache/) only to checksum it —
    # the blobs are not vendored, just recorded in the lock by sha256.
    packages = []
    for i, (name, st) in enumerate(sorted(chosen.items()), 1):
        filename = f"{name}-{st['V']}.apk"
        url = f"{WOLFI_REPO}/{ARCH}/{filename}"
        cache = os.path.join(CACHE_DIR, filename)
        if os.path.exists(cache):
            data = open(cache, "rb").read()
        else:
            print(f"  [{i}/{len(chosen)}] {filename}", file=sys.stderr)
            data = fetch(url)
            with open(cache, "wb") as f:
                f.write(data)
        packages.append({
            "name": name,
            "version": st["V"],
            "filename": filename,
            "url": url,
            "sha256": hashlib.sha256(data).hexdigest(),
        })

    write_lock_bzl(
        os.path.join(REPO_DIR, "wolfi.lock.bzl"),
        source=WOLFI_REPO,
        arch=ARCH,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        index_sha256=hashlib.sha256(minimal_index).hexdigest(),
        packages=packages,
    )
    print(f"\nwrote wolfi.lock.bzl ({len(packages)} packages) + minimal index to {REPO_DIR}")


if __name__ == "__main__":
    main()
