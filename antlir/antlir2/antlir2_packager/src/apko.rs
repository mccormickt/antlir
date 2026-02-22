/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::path::Path;
use std::path::PathBuf;
use std::process::Command;

use anyhow::Context;
use anyhow::Result;
use serde::Deserialize;

use crate::run_cmd;

/// `package.apko` driver. Takes an apko YAML config and emits an OCI archive
/// by shelling out to the vendored apko binary. Unlike other packager formats
/// this one does not consume a layer — apko resolves and downloads everything
/// itself from the repos listed in the config.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Apko {
    config: PathBuf,
    #[serde(default = "default_arch")]
    arch: String,
    #[serde(default = "default_tag")]
    tag: String,
    #[serde(rename = "_apko")]
    apko: Vec<String>,
}

fn default_arch() -> String {
    // Matches the rule default in bzl/package/apko.bzl.
    "amd64".to_string()
}

fn default_tag() -> String {
    "image:latest".to_string()
}

impl Apko {
    pub fn build(&self, out: &Path) -> Result<()> {
        let apko_cmd = self
            .apko
            .first()
            .context("apko command is empty")?;

        run_cmd(
            Command::new(apko_cmd)
                .args(&self.apko[1..])
                .arg("build")
                .arg("--arch")
                .arg(&self.arch)
                .arg(&self.config)
                .arg(&self.tag)
                .arg(out),
        )
        .context("failed to build OCI archive with apko")?;
        Ok(())
    }
}
