# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is dual-licensed under either the MIT license found in the
# LICENSE-MIT file in the root directory of this source tree or the Apache
# License, Version 2.0 found in the LICENSE-APACHE file in the root directory
# of this source tree. You may select, at your option, one of the
# above-listed licenses.

# pyre-strict

from buck2.tests.e2e_util.api.buck import Buck


async def get_cfg(buck: Buck, *args: str) -> str:
    result = await buck.ctargets(*args)

    # Assuming ctargets output is `target (cfg)`
    cfg = result.stdout.split()[1].strip("()")

    result = await buck.audit_configurations(cfg)
    return result.stdout
