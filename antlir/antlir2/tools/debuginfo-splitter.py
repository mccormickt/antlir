#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--objcopy", required=True)
parser.add_argument("--binary", required=True, type=Path)
parser.add_argument("--binary-dwp", type=Path)
parser.add_argument("--stripped", required=True, type=Path)
parser.add_argument("--debuginfo", required=True, type=Path)
parser.add_argument("--dwp", required=True, type=Path)
parser.add_argument("--metadata", required=True, type=Path)
parser.add_argument("--objcopy-tmp", required=True, type=Path)

args = parser.parse_args()

# ensure this exists or buck2 will get mad
args.objcopy_tmp.touch()

if args.binary.is_dir():
    is_elf = False
else:
    with open(args.binary, mode="rb") as src_f:
        first_4 = src_f.read(4)
        is_elf = first_4 == b"\x7fELF"

if args.binary_dwp:
    shutil.copy2(args.binary_dwp, args.dwp)
else:
    with open(args.dwp, "w"):
        pass

# If this is not an ELF binary, it can't be stripped so just copy the original
if not is_elf:
    if args.binary.is_dir():
        shutil.copytree(args.binary, args.stripped, symlinks=True)
    else:
        shutil.copy2(args.binary, args.stripped)
    with open(args.debuginfo, "w"):
        pass
    with open(args.metadata, "w") as f:
        json.dump({}, f)
    sys.exit(0)

# Save debug symbols to a separate debuginfo file
proc = subprocess.run(
    [
        args.objcopy,
        "--only-keep-debug",
        args.binary,
        args.debuginfo,
    ],
    capture_output=True,
)
if proc.returncode != 0:
    raise RuntimeError(
        "Failed to extract debug symbols for {}:\\n{}\\n{}".format(
            args.binary,
            proc.stdout.decode("utf-8", errors="surrogateescape"),
            proc.stderr.decode("utf-8", errors="surrogateescape"),
        )
    )

# Remove the debug symbols from the stripped binary
proc = subprocess.run(
    [
        args.objcopy,
        "--strip-debug",
        "--keep-file-symbols",
        "--remove-section=.pseudo_probe",
        "--remove-section=.pseudo_probe_desc",
        args.binary,
        args.stripped,
    ],
    capture_output=True,
)
if proc.returncode != 0:
    raise RuntimeError(
        "Failed to extract debug symbols for {}:\\n{}\\n{}".format(
            args.binary,
            proc.stdout.decode("utf-8", errors="surrogateescape"),
            proc.stderr.decode("utf-8", errors="surrogateescape"),
        )
    )

# Find the BuildID of the binary. This determines where it should go for gdb to
# look it up under /usr/lib/debug
# https://sourceware.org/gdb/onlinedocs/gdb/Separate-Debug-Files.html
buildid_proc = subprocess.run(
    [
        args.objcopy,
        "--dump-section",
        ".note.gnu.build-id=/dev/stdout",
        args.binary,
        args.objcopy_tmp,
    ],
    capture_output=True,
)
if buildid_proc.returncode != 0:
    raise RuntimeError(
        "Failed to get build-id for {}:\\n{}\\n{}".format(
            args.binary,
            buildid_proc.stdout.decode("utf-8", errors="surrogateescape"),
            buildid_proc.stderr.decode("utf-8", errors="surrogateescape"),
        )
    )
buildid = buildid_proc.stdout

# Prefer to install the debug info by BuildID since it does not require another
# objcopy invocation and is more standard
with open(args.metadata, "w") as f:
    if buildid := buildid[len(buildid) - 20 :].hex():
        json.dump({"buildid": buildid}, f)
    else:
        # Can't setup debuglink here as we don't reliably know the location the binary
        # will end up being placed under, which debuglink relies on, so opt to no-op
        # here and linking will ultimately be handled in the install feature.
        json.dump({}, f)
