# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

"""\
Serialize a btrfs subvolume built by an `image_layer` target into a
portable format (either a file, or a directory with a few files).

At the moment, this only outputs "full" packages -- that is, we do not
support emitting an incremental package relative to a prior `image_layer`.

## How to add support for incremental packages

There is a specific setting, where it is possible to support safe
incremental packaging.  First, read on to understand why the general case of
incremental packaging is intrinsically unsafe.

### The incremental package consistency problem

It is technically simple to create incremental outputs:
  - `btrfs send -p`
  - `tar --create --listed-incremental`

The problem is that it is hard to guarantee consistency between parts of the
incremental stack.

It is reasonable for an end-user to expect this to work correctly, so long
as they build both parts from excatly the same source control version:
 - first, they build package A;
 - later (perhaps on a different host or repo checkout), they build an
   incremental package B that stacks on top of A.

Indeed, this generally works for programming artifacts, because programming
languages define a clear interface for their build artifacts, and the same
source code + build toolchain is GUARANTEED to always produce artifacts that
are interface-compatible with other outputs from the same inputs.

In contrast, a filesystem output of an image build does NOT define such an
interface, which makes it impossible to guarantee consistency.  Let's make
this concrete with an example.

Imagine these Buck targets:
 - `:parent_subvol`
 - `:child_subvol`, with `parent_layer = ":parent_subvol"`

Let's say that `:parent_subvol` contains, among other things, a multi-file
relational DB which stores a table per file, and uses RANDOM keys
internally. The first time we build it, we might get this:

```
$ jq . table_names
{
    "randKeyA3": {"name": "cat"},
    "randKeyA1": {"name": "dog"},
    "randKeyA8": {"name": "gibbon"}
}
$ jq . table_friends
{
    "randKeyA3": ["randKeyA1"]
}
```

This database just says that we have 3 animals, and 1 directed friendship
among them (cat -> dog).

You can imagine a second build of `:parent_subvol` which has the same
semantic content:

```
$ jq . table_names
{
    "randKeyA6": {"name": "cat"},
    "randKeyA5": {"name": "dog"},
    "randKeyA1": {"name": "gibbon"}
}
$ jq . table_friends
{
    "randKeyA6": ["randKeyA5"]
}
```

Since the random keys are internal to the DB, and not part of its public
API, this is permissible build entropy -- just like "build info" sections in
binary objects, and just like build timestamps.

So from the point of view of Buck, everything is fine.

Now, let's say that in `:child_subvol` we add another friendship to the DB
(gibbon -> dog).  Depending on the version of `:parent_subvol` you start
with, building `:child_subvol` will cause you to produce an incremental
package replaceing JUST the file `table_friends` with one of these versions:

```
# `:child_subvol` from the first `:parent_subvol` build
$ jq . table_friends
{
    "randKeyA3": ["randKeyA1"],
    "randKeyA8": ["randKeyA1"]
}
# `:child_subvol` from the second `:parent_subvol` build
$ jq . table_friends
{
    "randKeyA6": ["randKeyA5"],
    "randKeyA1": ["randKeyA5"],
}
```

Omitting `table_names` from the incremental update is completely fine from
the point of view of the filesystem -- that file hasn't changed in either
build.  However, we now have two INCOMPATIBLE build artifacts from the same
source version.

Now, we may end up combining the first version of `:parent_subvol` with the
second version of `:child_subvol`. The incremental update would apply fine,
but the resulting DB would be corrupted.

Worst of all, this could occur quite naturally, e.g.
  - An innocent (but not stupid!) user may assume that since builds are
    hermetic, build artifacts from the same version are compatible.
  - Target-level distributed caching in Buck may cache artifacts from two
    different build runs.  On the Buck side, T35569915 documents the
    intention to make ALL cache retrievals be based only on input keys,
    which could actually guarantee the consistency we need, but this is
    probably not happening before late 2019, early 2020.

To sum up:

 - In practice, builds are almost never bitwise-reproducible. The resulting
   filesystem contents of two builds of the same repo state may differ.
   When we say a build environment is hermetic we just mean that at runtime,
   all of its artifacts work the same way, so long as they were built from
   the same repo state.

 - Filesystems lack a standard semantic interface, which could guarantee
   interoperability between filesystem artifacts from two differen builds of
   the same "hermetic" environment.  Therefore, any kind of "incremental"
   package has to be applied against EXACTLY the same filesystem contents,
   against which it was built, or the result may be incorrect.

 - In a distributed build setting, it's hard to guarantee that incremental
   build artifacts will NOT get composed incorrectly.

 - So, we choose NOT to support incremental packaging in the general case.
   We may revise this decision once Buck's cache handling changes
   (T35569915), or if the need for incremental packaging is strong enough to
   justify shipping solutions with razor-sharp edges.

### When can we safely build incremental packages?

Before getting to the practically useful solution, let me mention a
less-useful one in passing.  It is simple to define a rule type that outputs
a STACK of known-compatible incremental packages.  The current code has
commented-out breadcrumbs (see `get_subvolume_on_disk_stack`), while
P60233442 adds ~20 lines of code to materializing an incremental send-stream
stack.  This solves the consistency problem, but it's unclear what value
this type of rule provides over a "full" package.

The main use-case for incremental builds is this:
 - pieces of widely-used infrastructure are packaged up into a few
   common base images,
 - custom container images are distributed as incremental add-ons to these
   common bases.

In this case, we can side-step the above correctness issues by requiring
that any base `image_layer` for an incremental package must have a "release"
property.  This is an assertion that can be verified at build-time, stating
that a content hash of the base layer has been checked into the source
control repo.  While the production version of this might look a little
different, this demonstrates the right semantics:

```
$ cat TARGETS
buck_genrule(
    name='parent.sendstream',
    out='parent.sendstream',
    bash='... fetch the sendstream from some blob store ...',
)

image_layer_from_package(
    name='parent',
    format='sendstream',
    source=':parent.sendstream',
    # The presence of this hash assures us that the filesystem contents are
    # fixed, which makes it safe to build incremental snapshots against it.
    sendstream_hash={
        'sha256':
            '4449df7d6848198f310aaffa7f7860b6022017e1913b94b6af86bb618e999480',
    },
)

image_layer(
    name='child',
    parent_layer=':parent',
    ...
)

package_new(
    name='child_from_parent.sendstream',
    layer=':child',
    # If `:parent` lacked `sendstream_hash`, we would not know it is a
    # "release" image, and this `package_new` would fail to build.
    incremental_to=':parent',
)
```

Besides tweaks to naming, the main difference I would expect in a production
system is a more automatable way of specifying content hashes for previously
released base images.

Requiring base images to be released adds some conceptual complexity. However,
it is quite reasonable to have post-CI release processes for commonly used
base images. Specific advantages to this include:
 - more rigorous testing than is feasible in at-code-review-time CI/CD system
 - the ability to pre-warm caches, thus ensuring nearly instant availability
   of the base images.
"""

import collections
import subprocess
from typing import Dict, NamedTuple, Optional

from antlir import btrfsutil
from antlir.bzl.image.package.btrfs import btrfs_opts_t
from antlir.cli import init_cli, normalize_buck_path
from antlir.common import get_logger, pipe
from antlir.errors import UserError
from antlir.find_built_subvol import find_built_subvol
from antlir.fs_utils import Path
from antlir.loopback import (
    BtrfsLoopbackVolume,
    MIN_CREATE_BYTES,
    MIN_FREE_BYTES,
)
from antlir.subvol_utils import Subvol
from antlir.unshare import Namespace, Unshare

log = get_logger()
MiB = 2**20


class _FoundSubvolOpts(NamedTuple):
    subvol: Subvol
    writable: bool


class BtrfsImage:
    """
    Packages the subvolume as a btrfs-formatted disk image, usage:
      mount -t btrfs image.btrfs dest/ -o loop
    """

    _OUT_OF_SPACE_SUFFIX = b": No space left on device\n"

    def _mark_subvol_readonly(
        self, ns: Unshare, path: Path, readonly: bool
    ) -> None:
        btrfsutil.set_subvolume_read_only(path, readonly, in_namespace=ns)

    def package(
        self,
        output_path: Path,
        subvols: Dict[Path, _FoundSubvolOpts],
        *,
        default_subvol: Optional[Path] = None,
        label: Optional[str] = None,
        compression_level: int = 0,
        seed_device: bool = False,
        size_mb: int = 0,
    ) -> None:

        # Sanity check to make sure that the requested default_subvol
        # is actually defined in the list of subvols to package
        if default_subvol:
            if not default_subvol.startswith(b"/"):
                raise UserError(
                    f"Requested default: '{default_subvol}' must be an "
                    "absolute path."
                )

            if default_subvol not in subvols:
                raise UserError(
                    f"Requested default: '{default_subvol}' is not a subvol "
                    f"being packaged:  {subvols.keys()}"
                )

        # Sanity check the subvol names are abs paths
        for subvol in subvols.keys():
            if not subvol.startswith(b"/"):
                raise UserError(
                    f"Requested subvol name must be an absolute path: {subvol}"
                )

        # First estimate how much space the subvolume requires.
        # Todo: this should/could be ported to use something like btdu to
        # get a more accurate estimate: https://github.com/CyberShadow/btdu
        estimated_fs_bytes = 0
        for (subvol, _) in subvols.values():
            estimated_fs_bytes += subvol.estimate_content_bytes()

        estimated_min_required_bytes = estimated_fs_bytes + MIN_FREE_BYTES

        fs_bytes = (
            estimated_min_required_bytes
            if estimated_min_required_bytes >= MIN_CREATE_BYTES
            else MIN_CREATE_BYTES
        )

        if size_mb:
            requested_fs_bytes = size_mb * MiB
            if requested_fs_bytes < fs_bytes:
                raise UserError(
                    f"Unable to package subvol of {fs_bytes} bytes into "
                    f"requested loopback size of {requested_fs_bytes} bytes"
                )

            fs_bytes = requested_fs_bytes

        # Sort the subvols by their desired paths so that we can ensure the
        # hierarchy is created in order.  We do this here so that we can
        # walk backwards through the subvol paths after receiving them all
        # and mark them read-only in reverse order.
        subvols = collections.OrderedDict(
            sorted(
                subvols.items(),
                key=lambda elem: (elem[0].dirname(), elem[0].basename()),
            )
        )

        open(output_path, "wb").close()
        with Unshare(
            [Namespace.MOUNT, Namespace.PID]
        ) as ns, BtrfsLoopbackVolume(
            unshare=ns,
            image_path=output_path,
            size_bytes=fs_bytes,
            label=label,
            compression_level=compression_level,
        ) as loop_vol:
            for subvol_name, (subvol, _) in subvols.items():
                log.info(
                    f"Receiving {subvol.path()} -> "
                    f"{loop_vol._image_path}/{subvol_name}"
                )
                with pipe() as (
                    r_send,
                    w_send,
                ), subvol.mark_readonly_and_write_sendstream_to_file(w_send):
                    # This end is now fully owned by `btrfs send`
                    w_send.close()
                    with r_send:
                        recv_ret = loop_vol.receive(r_send)
                        if recv_ret.returncode != 0:
                            err = recv_ret.stderr.decode(
                                errors="surrogateescape"
                            )
                            if recv_ret.stderr.endswith(
                                self._OUT_OF_SPACE_SUFFIX
                            ):
                                err = (
                                    f"Receive failed. Subvol of "
                                    f"{estimated_fs_bytes} bytes did not fit "
                                    f"into loopback of {fs_bytes} bytes: {err}"
                                )

                            raise UserError(err)

                # Mark as read-write for potential future operations.
                subvol_path_src = loop_vol.dir() / subvol.path().basename()
                self._mark_subvol_readonly(
                    ns=ns,
                    path=subvol_path_src,
                    readonly=False,
                )

                # Optionally change the subvolume name, stripping the
                # / first
                subvol_path_dst = loop_vol.dir() / Path(subvol_name[1:])
                if subvol_path_src != subvol_path_dst:
                    log.info(f"Renaming {subvol_path_src} -> {subvol_path_dst}")
                    # If we have any parent paths that don't exist yet, make
                    # them here.  Note these are regular directories, not
                    # subvols.
                    log.info(
                        f"Making parent paths: {subvol_path_dst.dirname()}"
                    )
                    subprocess.run(
                        ns.nsenter_as_root(
                            "mkdir",
                            "-p",
                            subvol_path_dst.dirname(),
                        ),
                        check=True,
                    )

                    subprocess.run(
                        ns.nsenter_as_root(
                            "mv",
                            str(subvol_path_src),
                            str(subvol_path_dst),
                        ),
                        check=True,
                    )

            # Iterate through the subvol list in reverse and mark
            # all subvols as read-only unless explicitly told otherwise
            for subvol_name, (_, writable) in reversed(subvols.items()):
                subvol_path = loop_vol.dir() / Path(subvol_name[1:])

                if not writable:
                    log.info(f"Marking {subvol_path} as read-only")
                    self._mark_subvol_readonly(
                        ns=ns, path=subvol_path, readonly=True
                    )

            # Mark a subvol as default
            if default_subvol:
                # Get the subvolume ID by just listing the specific
                # subvol and getting the 2nd element.
                # The output of this command looks like:
                #
                # b'ID 256 gen 7 top level 5 path volume\n'
                subvol_id = btrfsutil.subvolume_id(
                    loop_vol.dir() / default_subvol[1:], in_namespace=ns
                )
                log.debug(f"subvol_id to set as default: {subvol_id}")
                # Actually set the default
                btrfsutil.set_default_subvolume(
                    loop_vol.dir(), subvol_id, in_namespace=ns
                )

            if not size_mb:
                loop_vol.minimize_size()

        # This can only be done when the loopback is unmounted
        if seed_device:
            subprocess.run(
                ["btrfstune", "-S", "1", output_path],
                check=True,
            )


def package_btrfs(args) -> None:
    with init_cli(description=__doc__, argv=args) as cli:
        cli.parser.add_argument(
            "--subvolumes-dir",
            required=True,
            type=Path.from_argparse,
            help="A directory on a btrfs volume, where all the subvolume "
            "wrapper directories reside.",
        )
        cli.parser.add_argument(
            "--output-path",
            required=True,
            type=normalize_buck_path,
            help="Write the image package file(s) to this path. This "
            "path must not already exist.",
        )
        cli.parser.add_argument(
            "--opts",
            type=btrfs_opts_t.load,
            required=True,
            help="Inline serialized loopback_opts_t instance containing "
            "configuration options for loopback formats",
        )

    # Map the subvols into actual _found_ subvols on disk
    subvols = {}
    for subvol_name, subvol_opts in cli.args.opts.subvols.items():
        log.info(f"subvol_name: {subvol_name}, subvol_opts: {subvol_opts}")
        subvols[Path(subvol_name)] = _FoundSubvolOpts(
            subvol=find_built_subvol(
                subvol_opts.layer.path,
                subvolumes_dir=cli.args.subvolumes_dir,
            ),
            writable=subvol_opts.writable,
        )

    # Build it
    BtrfsImage().package(
        cli.args.output_path,
        subvols,
        compression_level=cli.args.opts.compression_level,
        default_subvol=cli.args.opts.default_subvol,
        seed_device=cli.args.opts.seed_device,
        label=cli.args.opts.loopback_opts.label
        if cli.args.opts.loopback_opts
        else None,
        size_mb=cli.args.opts.loopback_opts.size_mb
        if cli.args.opts.loopback_opts
        else None,
    )


if __name__ == "__main__":  # pragma: no cover
    package_btrfs(None)
