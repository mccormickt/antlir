/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::BTreeMap;
use std::fs::Permissions;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::path::PathBuf;

use anyhow::Context;
use anyhow::Result;
use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE;
use serde::Deserialize;
use walkdir::WalkDir;

/// Check if a filename contains characters that Buck2 doesn't allow.
/// Buck2 disallows forward slash '/' and backslash '\' in filenames.
fn needs_escaping(component: &std::ffi::OsStr) -> bool {
    component.as_bytes().contains(&b'\\')
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UnprivilegedDir {
    base64_encoded_filenames: Option<PathBuf>,
}

impl UnprivilegedDir {
    pub(crate) fn build(
        &self,
        out: &Path,
        layer: &Path,
        root_guard: Option<antlir2_rootless::EscalationGuard>,
    ) -> Result<()> {
        let layer = layer.canonicalize()?;

        // Track escaped paths: escaped_relative_path -> original_relative_path
        // such that they can be reconstructed by consumers of the dir
        let mut escaped_paths: BTreeMap<PathBuf, PathBuf> = BTreeMap::new();

        std::fs::create_dir(out).context("while creating root")?;

        std::os::unix::fs::lchown(
            out,
            root_guard
                .as_ref()
                .and_then(|r| r.unprivileged_uid())
                .map(|i| i.as_raw()),
            root_guard
                .as_ref()
                .and_then(|r| r.unprivileged_gid())
                .map(|i| i.as_raw()),
        )
        .context("while chowning root")?;
        for entry in WalkDir::new(&layer) {
            let entry = entry?;
            let relpath = entry.path().strip_prefix(&layer)?;
            if relpath == Path::new("") {
                continue;
            }
            let dst = if self.base64_encoded_filenames.is_some() {
                let encoded_path = relpath
                    .components()
                    .map(|component| {
                        let component_os = component.as_os_str();
                        if needs_escaping(component_os) {
                            PathBuf::from(URL_SAFE.encode(component_os.as_bytes()))
                        } else {
                            PathBuf::from(component_os)
                        }
                    })
                    .collect::<PathBuf>();
                if encoded_path != relpath {
                    // Ensure the mapping always contains absolute paths
                    escaped_paths.insert(
                        PathBuf::from("/").join(&encoded_path),
                        PathBuf::from("/").join(relpath),
                    );
                }
                out.join(encoded_path)
            } else {
                out.join(relpath)
            };
            if entry.file_type().is_dir() {
                std::fs::create_dir(&dst)
                    .with_context(|| format!("while creating directory '{}'", dst.display()))?;
                std::fs::set_permissions(&dst, Permissions::from_mode(0o755))?;
            } else if entry.file_type().is_symlink() {
                let target = std::fs::read_link(entry.path())?;
                std::os::unix::fs::symlink(target, &dst)
                    .with_context(|| format!("while creating symlink '{}'", dst.display()))?;
            } else if entry.file_type().is_file() {
                std::fs::copy(entry.path(), &dst).with_context(|| {
                    format!(
                        "while copying file '{}' -> '{}'",
                        entry.path().display(),
                        dst.display()
                    )
                })?;
                let mut mode = entry.metadata()?.mode();
                // preserve executable bit
                if (mode & 0o111) != 0 {
                    mode |= 0o111;
                }
                // always allow read
                mode |= 0o444;
                // remove write bits
                mode &= !0o222;
                std::fs::set_permissions(&dst, Permissions::from_mode(mode))?;
            }
            std::os::unix::fs::lchown(
                &dst,
                root_guard
                    .as_ref()
                    .and_then(|r| r.unprivileged_uid())
                    .map(|i| i.as_raw()),
                root_guard
                    .as_ref()
                    .and_then(|r| r.unprivileged_gid())
                    .map(|i| i.as_raw()),
            )
            .with_context(|| format!("while chowning '{}'", dst.display()))?;
        }

        if let Some(base64_encoded_filenames) = &self.base64_encoded_filenames {
            std::fs::write(
                base64_encoded_filenames,
                serde_json::to_string_pretty(&escaped_paths)
                    .context("while serializing escaped paths mapping")?
                    .as_bytes(),
            )
            .context("while writing escaped paths mapping")?;
        }

        Ok(())
    }
}
