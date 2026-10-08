"""Deterministic file layers and offline OCI composition with regctl."""

import datetime
import json
from pathlib import Path
import re
import tarfile

from build import digest, member, run


def file_layer(source, files, modes, epoch, output):
    records = {}
    if set(modes) - set(files):
        raise ValueError("file_modes must refer to declared files")
    with tarfile.open(output, "w", format=tarfile.PAX_FORMAT) as archive:
        for destination in sorted(files):
            parts = destination.split("/")
            if not destination.startswith("/") or any(
                p in ("", ".", "..") or p.startswith(".wh.") for p in parts[1:]
            ):
                raise ValueError(f"invalid file destination: {destination}")
            if any(
                parent in files
                for parent in (str(p) for p in Path(destination).parents)
            ):
                raise ValueError(f"file destination has a file parent: {destination}")
            path = member(source, destination[1:])
            if not path.is_file():
                raise ValueError(f"expected a regular file: {destination}")
            mode = modes.get(
                destination, 0o755 if path.stat().st_mode & 0o111 else 0o644
            )
            if not 0 <= mode <= 0o777:
                raise ValueError(f"invalid file mode: {destination}")
            info = tarfile.TarInfo(destination[1:])
            info.mode = mode
            info.mtime = epoch
            info.size = path.stat().st_size
            with path.open("rb") as stream:
                archive.addfile(info, stream)
            records[destination] = {
                "sha256": digest(path),
                "mode": mode,
                "uid": 0,
                "gid": 0,
            }
    return records


def read_blob(layout, descriptor):
    value = descriptor["digest"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("expected a SHA-256 OCI descriptor")
    path = layout / "blobs/sha256" / value.split(":")[1]
    if digest(path) != value.split(":")[1] or path.stat().st_size != descriptor["size"]:
        raise ValueError("OCI descriptor does not match blob")
    return json.loads(path.read_text())


def compose(spec):
    base = Path(spec["base"]) / "oci"
    index = json.loads((base / "index.json").read_text())
    if len(index["manifests"]) != 1:
        raise ValueError("oci_image requires a single-platform base")
    descriptor = index["manifests"][0]
    manifest = read_blob(base, descriptor)
    config = read_blob(base, manifest["config"])
    arch = {"x86_64": "amd64", "aarch64": "arm64"}[spec["arch"]]
    if config["os"] != "linux" or config["architecture"] != arch:
        raise ValueError("base image architecture does not match provider")
    out = Path("/build/output")
    out.mkdir()
    flags = []
    records = {}
    if spec["files"]:
        records = file_layer(
            Path(spec["source"]),
            spec["files"],
            spec["file_modes"],
            spec["epoch"],
            Path("/build/layer.tar"),
        )
        flags += ["--layer-add", "tar=/build/layer.tar"]
    if spec["entrypoint"] is not None:
        flags += ["--config-entrypoint", json.dumps(spec["entrypoint"])]
        # A new executable must not receive the base executable's default args.
        if spec["cmd"] is None:
            flags += ["--config-cmd", ""]
    if spec["cmd"] is not None:
        flags += ["--config-cmd", json.dumps(spec["cmd"])]
    for name, value in sorted(spec["environment"].items()):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"invalid environment variable name: {name}")
        if value == "":
            raise ValueError(
                "oci_image cannot set empty environment values; set them on apk_image"
            )
        flags += ["--env", name + "=" + value]
    date = (
        datetime.datetime.fromtimestamp(spec["epoch"], datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    run(
        spec["regctl"],
        "image",
        "mod",
        f"ocidir://{base}@{descriptor['digest']}",
        "--create",
        "ocidir:///build/composed:intermediate",
        "--to-oci",
        *flags,
    )
    # regctl creates new history after config modifiers run. Normalize it in a
    # second pass into a fresh layout so wall-clock blobs are not published.
    run(
        spec["regctl"],
        "image",
        "mod",
        "ocidir:///build/composed:intermediate",
        "--create",
        "ocidir:///build/output/oci:latest",
        "--config-time",
        f"set={date},base-layers={len(manifest['layers'])}",
    )
    (out / "composition.json").write_text(
        json.dumps(
            {
                "base_manifest": descriptor["digest"],
                "files": records,
                "entrypoint": spec["entrypoint"],
                "cmd": spec["cmd"],
                "environment": spec["environment"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
