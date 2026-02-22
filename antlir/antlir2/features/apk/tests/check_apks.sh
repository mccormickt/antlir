#!/bin/sh
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# Verify presence/absence of APKs and world entries inside an image layer.
# Reads expected sets from environment variables so the script body is the
# same across every test invocation:
#
#   APK_INSTALLED:      space-separated package names that must be installed
#   APK_NOT_INSTALLED:  space-separated package names that must NOT be installed
#   APK_WORLD:          space-separated package names that must be in /etc/apk/world
#
# Uses only POSIX shell + apk, both guaranteed by wolfi-baselayout +
# apk-tools, so no Python interpreter is needed in the layer under test.

set -eu

# Path to the apk-tools "installed" database. apk's stanzas are separated
# by blank lines, with a `P:<name>` line per package.
DB=/lib/apk/db/installed

fail() {
    echo "FAIL: $*" >&2
    if [ -r "$DB" ]; then
        echo >&2
        echo "all installed packages:" >&2
        grep '^P:' "$DB" | sed 's/^P://' | sort >&2
    fi
    exit 1
}

# Returns 0 iff $1 is installed.
#
# Walk forward from `P:$1` until the next blank line and require a `V:`
# (version) record inside that stanza. A bare `P:` line with no `V:`
# would be a partial-install hazard: apk treats the package as known
# but it's missing the version record any consumer would parse.
is_installed() {
    [ -r "$DB" ] || return 1
    awk -v pkg="P:$1" '
        $0 == pkg { in_pkg = 1; v_seen = 0; next }
        in_pkg && /^V:/ { v_seen = 1; next }
        in_pkg && /^$/ {
            if (v_seen) { exit 0 }
            in_pkg = 0
        }
        END { exit (in_pkg && v_seen) ? 0 : 1 }
    ' "$DB"
}

for pkg in ${APK_INSTALLED:-}; do
    is_installed "$pkg" || fail "'$pkg' is not installed"
done

for pkg in ${APK_NOT_INSTALLED:-}; do
    if is_installed "$pkg"; then
        fail "'$pkg' is installed but should not be"
    fi
done

for pkg in ${APK_WORLD:-}; do
    grep -Fxq "$pkg" /etc/apk/world \
        || fail "'$pkg' not found in /etc/apk/world"
done
