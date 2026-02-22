# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/antlir2/bzl:platform.bzl", "rule_with_default_target_platform")
load("//antlir/buck2/bzl:ensure_single_output.bzl", "ensure_single_output")

ApkInfo = provider(fields = [
    "name",
    "version",
    "arch",
    "raw_apk",
])

_VALIDATE_SCRIPT = """\
#!/bin/sh
set -eu
SRC="$1"
DST="$2"
EXPECTED_NAME="$3"
EXPECTED_VERSION="$4"

# .apk is a gzipped tar (multi-stream); extract the .PKGINFO record.
PKGINFO="$(gunzip -c "$SRC" 2>/dev/null | tar -xO .PKGINFO 2>/dev/null || true)"
if [ -z "$PKGINFO" ]; then
    echo "ERROR: could not read .PKGINFO from $SRC" >&2
    exit 1
fi

ACTUAL_NAME="$(printf '%s\\n' "$PKGINFO" | sed -n 's/^pkgname = //p' | head -1)"
ACTUAL_VERSION="$(printf '%s\\n' "$PKGINFO" | sed -n 's/^pkgver = //p' | head -1)"

if [ "$ACTUAL_NAME" != "$EXPECTED_NAME" ]; then
    echo "ERROR: apk name mismatch in $SRC: rule declares '$EXPECTED_NAME' but .PKGINFO has 'pkgname = $ACTUAL_NAME'" >&2
    exit 1
fi

# .apk versions are conventionally `<version>-r<rel>`. Allow the rule's
# `version` attr to omit the `-r0` release tag for the default case.
if [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ] && [ "$ACTUAL_VERSION" != "$EXPECTED_VERSION-r0" ]; then
    echo "ERROR: apk version mismatch in $SRC: rule declares '$EXPECTED_VERSION' but .PKGINFO has 'pkgver = $ACTUAL_VERSION'" >&2
    exit 1
fi

cp "$SRC" "$DST"
"""

def _impl(ctx: AnalysisContext) -> list[Provider]:
    raw_apk = ensure_single_output(ctx.attrs.apk)

    # Validate the rule's `apk_name` and `version` attrs against the
    # actual .PKGINFO inside the .apk. The validation action's output is
    # a byte-identical copy of the input, so any consumer that depends on
    # this rule's output is guaranteed to have seen a successful check.
    validate_script = ctx.actions.write(
        "validate-apk.sh",
        _VALIDATE_SCRIPT,
        is_executable = True,
    )
    validated = ctx.actions.declare_output("validated", ctx.attrs.apk_name + ".apk")
    ctx.actions.run(
        cmd_args(
            "/bin/sh",
            validate_script,
            raw_apk,
            validated.as_output(),
            ctx.attrs.apk_name,
            ctx.attrs.version,
        ),
        category = "apk_validate",
    )

    return [
        ApkInfo(
            name = ctx.attrs.apk_name,
            version = ctx.attrs.version,
            arch = ctx.attrs.arch,
            raw_apk = validated,
        ),
        DefaultInfo(validated),
    ]

_apk = rule(
    impl = _impl,
    attrs = {
        "apk": attrs.dep(),
        "apk_name": attrs.string(),
        "arch": attrs.string(default = "x86_64"),
        "version": attrs.string(),
    },
)

apk = rule_with_default_target_platform(_apk)
