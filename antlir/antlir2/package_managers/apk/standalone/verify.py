#!/usr/bin/env python3
"""Acceptance checks; first prepare examples/state.local as described in README.md."""

import copy
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile

from build import digest, write_manifest


def hashes(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}


def execute(spec, directory, failure=None):
    directory.mkdir()
    path = directory / "spec.json"
    path.write_text(json.dumps(spec))
    result = subprocess.run(
        [sys.executable, "build.py", "sandbox", str(path), str(directory / "output")],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if failure is None:
        if result.returncode:
            raise AssertionError(result.stdout)
    elif result.returncode == 0 or failure.lower() not in result.stdout.lower():
        raise AssertionError(
            f"expected failure containing {failure!r}:\n{result.stdout}"
        )
    return directory / "output"


def inspect_image(output, rootfs, arch):
    oci = output / "oci"

    def blob(descriptor):
        algorithm, value = descriptor["digest"].split(":")
        assert algorithm == "sha256"
        path = oci / "blobs" / algorithm / value
        assert digest(path) == value
        return path

    index = json.loads((oci / "index.json").read_text())
    manifest = json.loads(blob(index["manifests"][0]).read_text())
    config = json.loads(blob(manifest["config"]).read_text())
    assert config["config"]["Entrypoint"] == ["/usr/bin/antlir-hello"]
    rootfs.mkdir()
    for layer in manifest["layers"]:
        with tarfile.open(blob(layer)) as archive:
            # Device nodes are provided by bubblewrap, not the extracted layer.
            archive.extractall(
                rootfs, members=[m for m in archive if not m.isdev()], filter="tar"
            )
    result = subprocess.check_output(
        [
            "bwrap",
            "--unshare-user",
            "--uid",
            "0",
            "--gid",
            "0",
            "--unshare-net",
            "--ro-bind",
            str(rootfs),
            "/",
            "--dev",
            "/dev",
            "--chdir",
            "/",
            "/usr/bin/antlir-hello",
        ],
        text=True,
    )
    assert result == "hello from a Buck2 APK\n", result
    assert (
        rootfs / "usr/share/doc/antlir-hello/README"
    ).read_text() == "Built without network access.\n"
    expected = {("antlir-hello", "1.0.0-r0"), ("antlir-hello-doc", "1.0.0-r0")}
    sbom = json.loads((output / "sbom" / f"sbom-{arch}.spdx.json").read_text())
    assert expected <= {(p["name"], p.get("versionInfo")) for p in sbom["packages"]}
    lock = json.loads((output / "image.lock.json").read_text())
    assert expected <= {(p["name"], p["version"]) for p in lock["contents"]["packages"]}


def main():
    here = Path(__file__).resolve().parent
    os.chdir(here)
    buck = here.parents[4] / "buck2"
    outputs = json.loads(
        subprocess.check_output(
            [
                str(buck),
                "build",
                "//examples:hello[spec]",
                "//examples:image[spec]",
                "//examples:image",
                "--show-full-json-output",
            ],
            text=True,
        )
    )
    recipe = json.loads(Path(outputs["root//examples:hello[spec]"]).read_text())
    image = json.loads(Path(outputs["root//examples:image[spec]"]).read_text())
    with tempfile.TemporaryDirectory(prefix="apk-verify-") as tmp:
        tmp = Path(tmp)
        images, recipes = [], []
        for i in range(2):
            packages = execute(recipe, tmp / f"recipe-{i}")
            spec = copy.deepcopy(image)
            spec["repositories"][-1] = str(packages)
            images.append(execute(spec, tmp / f"image-{i}"))
            recipes.append(packages)
        assert hashes(recipes[0]) == hashes(recipes[1]), "package outputs differ"
        assert hashes(images[0]) == hashes(images[1]), "image outputs differ"
        assert hashes(images[0]) == hashes(
            Path(outputs["root//examples:image"])
        ), "Buck output differs"
        print(
            "PASS: two uncached builds match (APKs, signed index, OCI, lock, SBOM)",
            flush=True,
        )
        inspect_image(images[0], tmp / "rootfs", image["arch"])
        print("PASS: OCI binary, subpackage, package lock and SPDX SBOM", flush=True)

        bad_repo = tmp / "bad-repo"
        shutil.copytree(recipes[0], bad_repo)
        manifest = json.loads((bad_repo / "repository.json").read_text())
        # Keep the signing-key filename, but use a different public key and
        # refresh the manifest so rejection must come from signature checking.
        shutil.copyfile(
            here / "examples/wolfi-signing.rsa.pub", bad_repo / manifest["keys"][0]
        )
        write_manifest(
            bad_repo, image["arch"], manifest["repositories"], manifest["keys"]
        )
        spec = copy.deepcopy(image)
        spec["repositories"][-1] = str(bad_repo)
        execute(spec, tmp / "bad-signature", failure="rsa: verification error")
        print(
            "PASS: incorrect signing key rejected after manifest verification",
            flush=True,
        )

        shutil.copyfile(
            recipes[0] / manifest["keys"][0], bad_repo / manifest["keys"][0]
        )
        write_manifest(
            bad_repo, image["arch"], manifest["repositories"], manifest["keys"]
        )
        payload = next(bad_repo.glob("packages/*/antlir-hello-1*.apk"))
        payload.write_bytes(payload.read_bytes() + b"corrupt")
        execute(spec, tmp / "bad-payload", failure="checksum mismatch")
        print("PASS: corrupted package rejected", flush=True)

        source = tmp / "source"
        shutil.copytree(image["source"], source)
        config = source / "config.yaml"
        original = config.read_text()
        spec = copy.deepcopy(image)
        spec["source"] = str(source)
        config.write_text(
            original.replace("antlir-hello=1.0.0-r0", "antlir-hello=9.0.0-r0")
        )
        execute(spec, tmp / "missing-version", failure="antlir-hello=9.0.0-r0")
        print("PASS: unavailable exact version rejected", flush=True)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            address = listener.getsockname()
            with socket.create_connection(address, timeout=2):
                pass
            probe = tmp / "network-probe.py"
            probe.write_text(
                "import pathlib, socket\n"
                "with socket.socket() as client:\n"
                "    client.settimeout(2)\n"
                f"    assert client.connect_ex({address!r}) != 0, 'host network reachable'\n"
                "pathlib.Path('/build/output').mkdir()\n"
            )
            spec = copy.deepcopy(image)
            spec["driver"] = str(probe)
            execute(spec, tmp / "network")
        print("PASS: sandbox cannot reach host network", flush=True)

        source = tmp / "recipe-source"
        shutil.copytree(recipe["source"], source)
        (source / "hello.c").unlink()
        spec = copy.deepcopy(recipe)
        spec["source"] = str(source)
        execute(spec, tmp / "missing-source", failure="hello.c: No such file")
        print("PASS: undeclared source cannot be read from checkout", flush=True)


if __name__ == "__main__":
    main()
