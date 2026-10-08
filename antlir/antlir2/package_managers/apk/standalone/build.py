#!/usr/bin/env python3
"""Execute Melange and apko with declared inputs and no network access."""

import datetime
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def member(root, name):
    path = root / name
    if (
        Path(name).is_absolute()
        or ".." in Path(name).parts
        or not path.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError(f"path escapes repository: {name}")
    return path


def write_manifest(root, arch, repositories, keys):
    files = {
        str(p.relative_to(root)): digest(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and p != root / "repository.json"
    }
    (root / "repository.json").write_text(
        json.dumps(
            {
                "architecture": arch,
                "repositories": repositories,
                "keys": keys,
                "files": files,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def repository_inputs(roots, arch):
    repositories, keys = [], []
    for root in roots:
        root = Path(root)
        manifest = json.loads((root / "repository.json").read_text())
        if manifest["architecture"] != arch:
            raise ValueError(f"wrong repository architecture: {root}")
        actual_files = {
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and p != root / "repository.json"
        }
        if actual_files != set(manifest["files"]):
            raise ValueError(f"repository files do not match manifest: {root}")
        for name, expected in manifest["files"].items():
            if digest(member(root, name)) != expected:
                raise ValueError(f"checksum mismatch: {root / name}")
        for name in manifest["repositories"]:
            index = f"{name}/{arch}/APKINDEX.tar.gz"
            if index not in manifest["files"]:
                raise ValueError(f"index missing from manifest: {index}")
            repositories.append(str(member(root, name)))
        for name in manifest["keys"]:
            if name not in manifest["files"]:
                raise ValueError(f"key missing from manifest: {name}")
            keys.append(str(member(root, name)))
    return repositories, keys


def run(*args):
    subprocess.run([str(a) for a in args], check=True)


def sandbox(spec_path, output):
    spec = json.loads(Path(spec_path).read_text())
    if spec["kind"] == "recipe" and spec["arch"] != platform.machine():
        raise ValueError(
            "Melange recipes require a native worker for the requested architecture"
        )
    output = Path(output).absolute()
    with tempfile.TemporaryDirectory(prefix="apk-build-") as tmp:
        work = Path(tmp)
        (work / "tmp").mkdir()
        command = [
            "bwrap",
            "--unshare-user",
            "--uid",
            "0",
            "--gid",
            "0",
            # Melange starts nested containers. These capabilities apply only
            # inside this user namespace, not to the host.
            "--cap-add",
            "CAP_SETUID",
            "--cap-add",
            "CAP_SETGID",
            "--cap-add",
            "CAP_SETFCAP",
            "--cap-add",
            "CAP_SYS_ADMIN",
            "--unshare-net",
            "--unshare-pid",
            "--unshare-uts",
            "--hostname",
            "apk-build",
            "--die-with-parent",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "TZ",
            "UTC",
            "--setenv",
            "TMPDIR",
            "/build/tmp",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "SOURCE_DATE_EPOCH",
            str(spec["epoch"]),
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/sys",
            "--dir",
            "/etc",
            "--ro-bind",
            "/etc/passwd",
            "/etc/passwd",
            "--ro-bind",
            "/etc/group",
            "/etc/group",
            "--ro-bind",
            "/dev/null",
            "/etc/resolv.conf",
            "--bind",
            str(work),
            "/build",
            "--chdir",
            "/build",
        ]
        for key, destination in [
            ("source", "/source"),
            ("apko", "/tools/apko"),
            ("melange", "/tools/melange"),
            ("driver", "/build.py"),
            ("signing_key", "/signing-key"),
            ("base", "/base"),
            ("regctl", "/tools/regctl"),
            ("oci_driver", "/oci.py"),
        ]:
            if spec.get(key):
                command += ["--ro-bind", str(Path(spec[key]).resolve()), destination]
                spec[key] = destination
        for i, repo in enumerate(spec.get("repositories", [])):
            destination = f"/repos/{i}"
            command += ["--ro-bind", str(Path(repo).resolve()), destination]
            spec["repositories"][i] = destination
        (work / "spec.json").write_text(json.dumps(spec))
        run(*command, "/usr/bin/python3", "/build.py", "action", "/build/spec.json")
        shutil.copytree(work / "output", output)


def action(spec):
    if spec["kind"] == "oci":
        from oci import compose

        compose(spec)
        return
    repositories, keys = repository_inputs(spec["repositories"], spec["arch"])
    flags = []
    for repo in repositories:
        flags += ["--repository-append", repo]
    for key in keys:
        flags += ["--keyring-append", key]
    out = Path("/build/output")
    out.mkdir()
    date = (
        datetime.datetime.fromtimestamp(spec["epoch"], datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    if spec["kind"] == "recipe":
        public = subprocess.check_output(
            ["openssl", "rsa", "-in", spec["signing_key"], "-pubout"],
            stderr=subprocess.DEVNULL,
        )
        key_name = "apk-" + hashlib.sha256(public).hexdigest() + ".rsa"
        private = Path("/build") / key_name
        shutil.copyfile(spec["signing_key"], private)
        (out / "keys").mkdir()
        (out / "keys" / (key_name + ".pub")).write_bytes(public)
        run(
            spec["melange"],
            "build",
            "/source/config.yaml",
            "--arch",
            spec["arch"],
            "--runner",
            "bubblewrap",
            "--source-dir",
            "/source",
            "--pipeline-dir",
            "/source/pipelines",
            "--workspace-dir",
            "/build/workspace",
            "--out-dir",
            out / "packages",
            "--signing-key",
            private,
            "--apk-cache-dir",
            "/build/cache",
            "--cache-dir",
            "/build/source-cache",
            "--build-date",
            date,
            "--git-repo-url",
            "",
            "--git-commit",
            "",
            *flags,
        )
        write_manifest(out, spec["arch"], ["packages"], ["keys/" + key_name + ".pub"])
    else:
        lock = out / "image.lock.json"
        run(
            spec["apko"],
            "lock",
            "/source/config.yaml",
            "--arch",
            spec["arch"],
            "--cache-dir",
            "/build/cache",
            "--output",
            lock,
            *flags,
        )
        (out / "oci").mkdir()
        (out / "sbom").mkdir()
        run(
            spec["apko"],
            "build",
            "/source/config.yaml",
            "image:local",
            out / "oci",
            "--arch",
            spec["arch"],
            "--lockfile",
            lock,
            "--offline",
            "--vcs=false",
            "--build-date",
            date,
            "--cache-dir",
            "/build/cache",
            "--sbom-path",
            out / "sbom",
            *flags,
        )


if __name__ == "__main__":
    if sys.argv[1] == "sandbox":
        sandbox(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "action":
        action(json.loads(Path(sys.argv[2]).read_text()))
    else:
        raise SystemExit("expected sandbox or action")
