#!/usr/bin/env python3
"""Resolve and retain a signed binary repository closure. Requires network access."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request

from build import digest, run, write_manifest


def verify_package(data, package):
    """Verify every compressed APK stream against apko's resolved lock."""
    end = 0
    for kind in ("signature", "control", "data"):
        stream = package[kind]
        if kind == "signature" and not stream["range"]:
            continue
        match = re.fullmatch(r"bytes=(\d+)-(\d+)", stream["range"])
        if not match:
            raise ValueError(f"invalid {kind} range")
        start, last = map(int, match.groups())
        if start != end or last < start or last >= len(data):
            raise ValueError(f"invalid {kind} stream bounds")
        algorithm, expected = stream["checksum"].split("-", 1)
        actual = hashlib.new(algorithm, data[start : last + 1]).digest()
        if actual != base64.b64decode(expected, validate=True):
            raise ValueError(f"{kind} checksum mismatch: {package['name']}")
        end = last + 1
    if end != len(data):
        raise ValueError("unverified trailing APK bytes")


def download(url):
    if not url.startswith("https://"):
        raise ValueError(f"snapshot downloads require HTTPS: {url}")
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def package_identity(lock):
    return sorted(
        (p["name"], p["version"], p["architecture"], p["checksum"])
        for p in lock["contents"]["packages"]
    )


def export(args):
    output = args.output.absolute()
    if output.exists():
        raise ValueError(
            "output already exists; export a new snapshot instead of overwriting one"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    apko = str(Path(args.apko).resolve())
    with tempfile.TemporaryDirectory(prefix="apk-snapshot-", dir=output.parent) as tmp:
        tmp = Path(tmp)
        root = tmp / "snapshot"
        (root / "keys").mkdir(parents=True)
        keys = []
        for key in args.key:
            destination = root / "keys" / key.name
            if destination.exists():
                raise ValueError(f"duplicate key filename: {key.name}")
            shutil.copyfile(key, destination)
            keys.append("keys/" + key.name)
        config = {
            "contents": {
                "repositories": args.repository,
                "keyring": [str(root / key) for key in keys],
                "packages": args.package,
            },
            "archs": [args.arch],
        }
        config_path = tmp / "config.json"
        config_path.write_text(json.dumps(config))
        lock_path = tmp / "lock.json"
        run(
            apko,
            "lock",
            config_path,
            "--arch",
            args.arch,
            "--cache-dir",
            tmp / "cache",
            "--output",
            lock_path,
        )
        lock = json.loads(lock_path.read_text())
        repositories = [f"repos/{i}" for i in range(len(args.repository))]
        for url, name in zip(args.repository, repositories):
            directory = root / name / args.arch
            directory.mkdir(parents=True)
            (directory / "APKINDEX.tar.gz").write_bytes(
                download(f"{url}/{args.arch}/APKINDEX.tar.gz")
            )

        def fetch_package(package):
            matches = [
                i
                for i, repo in enumerate(args.repository)
                if package["url"].startswith(repo + "/" + args.arch + "/")
            ]
            if len(matches) != 1 or package["architecture"] != args.arch:
                raise ValueError(
                    f"package outside configured repositories: {package['url']}"
                )
            filename = package["url"].rsplit("/", 1)[1]
            if not re.fullmatch(r"[A-Za-z0-9_.+~-]+\.apk", filename):
                raise ValueError(f"invalid package filename: {filename}")
            data = download(package["url"])
            verify_package(data, package)
            (root / repositories[matches[0]] / args.arch / filename).write_bytes(data)

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch_package, lock["contents"]["packages"]))

        # Verify that retained signed indexes still resolve to the same closure.
        # The fresh cache and network namespace prevent a remote fallback.
        config["contents"]["repositories"] = [str(root / name) for name in repositories]
        config_path.write_text(json.dumps(config))
        local_lock = tmp / "local.lock.json"
        run(
            "unshare",
            "--user",
            "--map-root-user",
            "--net",
            apko,
            "lock",
            config_path,
            "--arch",
            args.arch,
            "--cache-dir",
            tmp / "offline-cache",
            "--output",
            local_lock,
        )
        if package_identity(lock) != package_identity(
            json.loads(local_lock.read_text())
        ):
            raise ValueError("repository changed during export; retry the snapshot")
        # This is provenance, not an apko lockfile with a config checksum.
        resolution = {
            "packages": lock["contents"]["packages"],
            "repositories": args.repository,
        }
        (root / "resolution.json").write_text(
            json.dumps(resolution, indent=2, sort_keys=True) + "\n"
        )
        config["contents"]["repositories"] = args.repository
        config["contents"]["keyring"] = keys
        (root / "snapshot-input.json").write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n"
        )
        write_manifest(root, args.arch, repositories, keys)
        root.rename(output)
    print(f"Retained {len(lock['contents']['packages'])} packages in {output}")
    print(f"repository.json sha256: {digest(output / 'repository.json')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apko", required=True)
    parser.add_argument("--arch", choices=["x86_64", "aarch64"], required=True)
    parser.add_argument("--repository", action="append", required=True)
    parser.add_argument(
        "--key",
        action="append",
        type=Path,
        required=True,
        help="reviewed local public key; never auto-discovered",
    )
    parser.add_argument("--package", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args())
