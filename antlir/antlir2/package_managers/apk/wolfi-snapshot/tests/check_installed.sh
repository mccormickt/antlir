#!/bin/sh
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# Assert that every package named in $EXPECT_INSTALLED is recorded in
# the apk installed database. Runs inside the layer under test using
# only POSIX shell (guaranteed by wolfi-baselayout + busybox).

set -eu

DB=/lib/apk/db/installed

for pkg in ${EXPECT_INSTALLED:-}; do
    if ! grep -qx "P:$pkg" "$DB"; then
        echo "FAIL: '$pkg' is not installed" >&2
        echo "installed packages:" >&2
        grep '^P:' "$DB" | sed 's/^P://' | sort >&2
        exit 1
    fi
done

echo "ok: all of [${EXPECT_INSTALLED:-}] installed from the snapshot"
