#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict

import importlib.resources
import json
import subprocess
import time
from pathlib import Path

import click
import createrepo_c as cr


# pyre-fixme[5]: Global expression must be annotated.
_COMPRESSION_MODES = {
    "none": ("", cr.NO_COMPRESSION),
    "gzip": (".gz", cr.GZ_COMPRESSION),
}


@click.command()
@click.option(
    "--repo-id",
    type=str,
    required=True,
)
@click.option(
    "--xml-dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    required=True,
)
@click.option(
    "--out",
    type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=Path),
    required=True,
)
@click.option(
    "--timestamp",
    type=int,
    default=int(time.time()),
)
@click.option(
    "--compress",
    type=click.Choice(list(_COMPRESSION_MODES.keys())),
    default="gzip",
)
@click.option(
    "--solv",
    help="pre-build dnf .solv files",
    type=click.Choice(["no", "yes", "try"]),
    default="yes",
)
def main(
    repo_id: str,
    xml_dir: Path,
    out: Path,
    timestamp: int,
    compress: str,
    solv: str,
) -> int:
    out.mkdir()
    ext = _COMPRESSION_MODES[compress][0]
    paths = {
        "primary": out / f"primary.xml{ext}",
        "filelists": out / f"filelists.xml{ext}",
        "other": out / f"other.xml{ext}",
    }
    compress = _COMPRESSION_MODES[compress][1]
    files = {
        "primary": cr.PrimaryXmlFile(str(paths["primary"]), compress),
        "filelists": cr.FilelistsXmlFile(str(paths["filelists"]), compress),
        "other": cr.OtherXmlFile(str(paths["other"]), compress),
    }

    xml_paths = list(xml_dir.iterdir())
    # pyre-fixme[16]: Item `FilelistsXmlFile` of `Union[FilelistsXmlFile,
    #  OtherXmlFile, PrimaryXmlFile]` has no attribute `set_num_of_pkgs`.
    files["primary"].set_num_of_pkgs(len(xml_paths))
    # pyre-fixme[16]: Item `FilelistsXmlFile` of `Union[FilelistsXmlFile,
    #  OtherXmlFile, PrimaryXmlFile]` has no attribute `set_num_of_pkgs`.
    files["filelists"].set_num_of_pkgs(len(xml_paths))
    # pyre-fixme[16]: Item `FilelistsXmlFile` of `Union[FilelistsXmlFile,
    #  OtherXmlFile, PrimaryXmlFile]` has no attribute `set_num_of_pkgs`.
    files["other"].set_num_of_pkgs(len(xml_paths))
    for path in xml_paths:
        with open(path) as f:
            chunks = json.load(f)
            for name, chunk in chunks.items():
                # pyre-fixme[16]: Item `FilelistsXmlFile` of
                #  `Union[FilelistsXmlFile, OtherXmlFile, PrimaryXmlFile]` has no
                #  attribute `add_chunk`.
                files[name].add_chunk(chunk)

    for file in files.values():
        # pyre-fixme[16]: Item `FilelistsXmlFile` of `Union[FilelistsXmlFile,
        #  OtherXmlFile, PrimaryXmlFile]` has no attribute `close`.
        file.close()

    repomd = cr.Repomd()
    for (name, path) in paths.items():
        record = cr.RepomdRecord(name, str(path))
        # pyre-fixme[16]: `RepomdRecord` has no attribute `set_timestamp`.
        record.set_timestamp(timestamp)
        # pyre-fixme[16]: `RepomdRecord` has no attribute `fill`.
        # pyre-fixme[16]: Module `createrepo_c` has no attribute `SHA256`.
        record.fill(cr.SHA256)
        # pyre-fixme[16]: `Repomd` has no attribute `set_record`.
        repomd.set_record(record)
    # pyre-fixme[16]: `Repomd` has no attribute `set_revision`.
    repomd.set_revision(str(timestamp))
    with open(out / "repomd.xml", "w") as f:
        # pyre-fixme[16]: `Repomd` has no attribute `xml_dump`.
        f.write(repomd.xml_dump())
    if solv in {"yes", "try"}:
        with importlib.resources.path(__package__, "build-solv.py") as build_solv:
            subprocess.run([build_solv, repo_id, out], check=solv == "yes")
    return 0


if __name__ == "__main__":
    main()