/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is dual-licensed under either the MIT license found in the
 * LICENSE-MIT file in the root directory of this source tree or the Apache
 * License, Version 2.0 found in the LICENSE-APACHE file in the root directory
 * of this source tree. You may select, at your option, one of the
 * above-listed licenses.
 */

use buck2_util::late_binding::LateBinding;

pub static FLUSH_DEP_FILES: LateBinding<fn()> = LateBinding::new("FLUSH_DEP_FILES");
pub static FLUSH_NON_LOCAL_DEP_FILES: LateBinding<fn()> =
    LateBinding::new("FLUSH_NON_LOCAL_DEP_FILES");

/// Forget about all dep files. This isn't really meant to be commonly used, but if an invalid dep
/// file was produced and the user wants unblocking, this will provide it.
pub fn flush_dep_files() {
    (FLUSH_DEP_FILES.get().unwrap())();
}

/// Forget about all dep files that were not produced locally.
pub fn flush_non_local_dep_files() {
    (FLUSH_NON_LOCAL_DEP_FILES.get().unwrap())();
}
