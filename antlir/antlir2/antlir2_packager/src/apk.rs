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

use crate::BuildAppliance;
use crate::PackageFormat;
use crate::run_cmd;

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Apk {
    build_appliance: BuildAppliance,
    apk_name: String,
    #[serde(default = "default_version")]
    version: String,
    #[serde(default)]
    epoch: i32,
    #[serde(default = "default_arch")]
    arch: String,
    #[serde(default)]
    description: String,
    #[serde(default = "default_license")]
    license: String,
    #[serde(default)]
    depends: Vec<String>,
    #[serde(default)]
    provides: Vec<String>,
    signing_key: Option<PathBuf>,
    /// Repositories that melange's build environment fetches from.
    /// Defaults to upstream Wolfi when empty.
    #[serde(default)]
    melange_repositories: Vec<String>,
    /// Keyring URLs/paths used to verify packages from
    /// `melange_repositories`. Defaults to the wolfi-signing public key.
    #[serde(default)]
    melange_keyring: Vec<String>,
    #[serde(rename = "_melange")]
    melange: Vec<String>,
}

fn default_version() -> String {
    "1.0.0".to_string()
}

fn default_arch() -> String {
    "x86_64".to_string()
}

fn default_license() -> String {
    "MIT".to_string()
}

fn default_melange_repositories() -> Vec<String> {
    vec!["https://packages.wolfi.dev/os".to_string()]
}

fn default_melange_keyring() -> Vec<String> {
    vec!["https://packages.wolfi.dev/os/wolfi-signing.rsa.pub".to_string()]
}

fn yaml_escape(s: &str) -> String {
    if s.contains([':', '#', '"', '\'', '\n', '\t', '{', '}', '[', ']', ',', '&', '*', '?', '|', '>', '<', '=', '!', '%', '@', '`']) {
        format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        s.to_string()
    }
}

impl PackageFormat for Apk {
    fn build(&self, out: &Path, layer: &Path) -> Result<()> {
        let tmp = tempfile::tempdir().context("while creating temp dir for apk build")?;
        let workdir = tmp.path();

        // Generate melange.yaml
        let name = yaml_escape(&self.apk_name);
        let version = yaml_escape(&self.version);
        let description = yaml_escape(&self.description);
        let license = yaml_escape(&self.license);
        let mut yaml = format!(
            r#"package:
  name: {name}
  version: {version}
  epoch: {epoch}
  description: {description}
  copyright:
    - license: {license}
  dependencies:
"#,
            epoch = self.epoch,
        );

        if self.depends.is_empty() {
            yaml.push_str("    runtime: []\n");
        } else {
            yaml.push_str("    runtime:\n");
            for dep in &self.depends {
                yaml.push_str(&format!("      - {}\n", yaml_escape(dep)));
            }
        }

        if !self.provides.is_empty() {
            yaml.push_str("  provides:\n");
            for p in &self.provides {
                yaml.push_str(&format!("    - {}\n", yaml_escape(p)));
            }
        }

        // The melange environment section defines the build container in
        // which the package pipeline runs. Repositories and keyring come
        // from the rule attrs (with sensible Wolfi defaults), so callers
        // packaging for non-Wolfi APK distros can override them without
        // editing the packager.
        let repos = if self.melange_repositories.is_empty() {
            default_melange_repositories()
        } else {
            self.melange_repositories.clone()
        };
        let keyring = if self.melange_keyring.is_empty() {
            default_melange_keyring()
        } else {
            self.melange_keyring.clone()
        };
        yaml.push_str("\nenvironment:\n  contents:\n    repositories:\n");
        for repo in &repos {
            yaml.push_str(&format!("      - {}\n", yaml_escape(repo)));
        }
        yaml.push_str("    keyring:\n");
        for key in &keyring {
            yaml.push_str(&format!("      - {}\n", yaml_escape(key)));
        }
        yaml.push_str(&format!(
            "\npipeline:\n  - runs: |\n      cp -a {layer_dir}/. \"${{{{targets.destdir}}}}/\"\n",
            layer_dir = layer.display(),
        ));

        let melange_yaml = workdir.join("melange.yaml");
        std::fs::write(&melange_yaml, yaml.as_bytes())
            .context("while writing melange.yaml")?;

        let melange_cmd = self.melange.first()
            .context("melange command is empty")?;

        // Generate a signing key if none provided
        let signing_key = if let Some(key) = &self.signing_key {
            key.clone()
        } else {
            let key_path = workdir.join("melange.rsa");
            run_cmd(
                Command::new(melange_cmd)
                    .args(&self.melange[1..])
                    .arg("keygen")
                    .arg(&key_path),
            )
            .context("failed to generate melange signing key")?;
            key_path
        };

        let out_dir = workdir.join("packages");
        std::fs::create_dir_all(&out_dir)
            .context("while creating output directory")?;

        // Build the APK
        run_cmd(
            Command::new(melange_cmd)
                .args(&self.melange[1..])
                .arg("build")
                .arg(&melange_yaml)
                .arg("--out-dir")
                .arg(&out_dir)
                .arg("--arch")
                .arg(&self.arch)
                .arg("--signing-key")
                .arg(&signing_key),
        )
        .context("failed to build APK with melange")?;

        // Find the output .apk file
        let arch_dir = out_dir.join(&self.arch);
        let entries: Vec<_> = std::fs::read_dir(&arch_dir)
            .context("while reading melange output dir")?
            .collect::<std::io::Result<Vec<_>>>()
            .context("while reading entries from melange output dir")?;
        let mut apk_files: Vec<_> = entries
            .into_iter()
            .filter(|e| {
                e.path()
                    .extension()
                    .map(|ext| ext == "apk")
                    .unwrap_or(false)
            })
            .collect();

        anyhow::ensure!(
            apk_files.len() == 1,
            "expected exactly one output .apk file, got: {:?}",
            apk_files.iter().map(|e| e.path()).collect::<Vec<_>>()
        );

        let apk_file = apk_files.remove(0);
        std::fs::copy(apk_file.path(), out)
            .context("while copying output .apk to final location")?;

        Ok(())
    }
}
