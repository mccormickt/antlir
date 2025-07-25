/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is dual-licensed under either the MIT license found in the
 * LICENSE-MIT file in the root directory of this source tree or the Apache
 * License, Version 2.0 found in the LICENSE-APACHE file in the root directory
 * of this source tree. You may select, at your option, one of the
 * above-listed licenses.
 */

//! Conversion impls for different error types to 'buck2_error::Error'

pub mod clap;
pub mod dice;
pub mod eden;
pub mod hex;
pub mod http;
pub mod hyper;
pub mod nix;
pub mod other;
pub mod prost;
pub mod regex;
pub mod relative_path;
pub mod report;
pub mod rusqlite;
pub mod serde_json;
pub mod stds;
pub mod superconsole;
pub mod tokio;
pub mod tonic;
pub mod uuid;
pub mod watchman;

use buck2_data::error::ErrorTag;

use crate::any::recover_crate_error;

// Helper function that can be explicited called to convert `std::error::Error` into `buck2_error`.
// Common types should have a proper From implemented in this file, but this function is useful for
// one-off error types in the codebase
#[cold]
#[track_caller]
pub fn from_any_with_tag<T>(e: T, tag: ErrorTag) -> crate::Error
where
    T: Into<anyhow::Error>,
    // This bound prevent this function from being called on an error that's
    // already a `buck2_error` which prevents unnecessary conversions
    Result<(), T>: anyhow::Context<(), T>,
{
    let anyhow: anyhow::Error = e.into();
    let source_location =
        crate::source_location::SourceLocation::new(std::panic::Location::caller().file());
    recover_crate_error(anyhow.as_ref(), source_location, tag)
}
