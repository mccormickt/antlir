/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::BTreeMap;
use std::collections::HashMap;
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

        // Track inode -> destination path for hardlink preservation
        // Key: (device, inode), Value: first destination path for this inode
        let mut inode_to_dst: HashMap<(u64, u64), PathBuf> = HashMap::new();

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
                let metadata = entry.metadata()?;
                let nlink = metadata.nlink();

                // Check if this is a hardlink we've already seen
                let inode_key = (metadata.dev(), metadata.ino());
                let existing_hardlink = if nlink > 1 {
                    inode_to_dst.get(&inode_key).cloned()
                } else {
                    None
                };

                if let Some(existing_dst) = existing_hardlink {
                    // This is a hardlink to an already-copied file - create hardlink
                    std::fs::hard_link(&existing_dst, &dst).with_context(|| {
                        format!(
                            "while creating hardlink '{}' -> '{}'",
                            dst.display(),
                            existing_dst.display()
                        )
                    })?;
                } else {
                    // First occurrence of this inode (or not a hardlink) - copy the file
                    std::fs::copy(entry.path(), &dst).with_context(|| {
                        format!(
                            "while copying file '{}' -> '{}'",
                            entry.path().display(),
                            dst.display()
                        )
                    })?;
                    let mut mode = metadata.mode();
                    // preserve executable bit
                    if (mode & 0o111) != 0 {
                        mode |= 0o111;
                    }
                    // always allow read
                    mode |= 0o444;
                    // remove write bits
                    mode &= !0o222;
                    std::fs::set_permissions(&dst, Permissions::from_mode(mode))?;

                    // Track this inode for future hardlinks
                    if nlink > 1 {
                        inode_to_dst.insert(inode_key, dst.clone());
                    }
                }
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

#[cfg(test)]
mod tests {
    use std::ffi::OsStr;
    use std::fs::File;
    use std::io::Write;
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::fs::MetadataExt;
    use std::os::unix::fs::PermissionsExt;

    use tempfile::TempDir;

    use super::*;

    #[test]
    fn build_copies_files_symlinks_and_permissions() -> Result<()> {
        let layer = TempDir::new()?;
        let out = TempDir::new()?;
        let out_path = out.path().join("output");

        File::create(layer.path().join("test.txt"))?.write_all(b"hello world")?;

        File::create(layer.path().join("target.txt"))?.write_all(b"target content")?;
        std::os::unix::fs::symlink("target.txt", layer.path().join("link.txt"))?;

        File::create(layer.path().join("script.sh"))?.write_all(b"#!/bin/bash\necho hello")?;
        std::fs::set_permissions(
            layer.path().join("script.sh"),
            Permissions::from_mode(0o755),
        )?;

        File::create(layer.path().join("writable.txt"))?.write_all(b"content")?;
        std::fs::set_permissions(
            layer.path().join("writable.txt"),
            Permissions::from_mode(0o644),
        )?;

        let unprivileged_dir = UnprivilegedDir {
            base64_encoded_filenames: None,
        };
        unprivileged_dir.build(&out_path, layer.path(), None)?;

        // file contents are preserved
        assert_eq!(
            std::fs::read_to_string(out_path.join("test.txt"))?,
            "hello world"
        );

        // symlinks are preserved
        assert_eq!(
            std::fs::read_link(out_path.join("link.txt"))?,
            PathBuf::from("target.txt")
        );

        assert_eq!(
            std::fs::metadata(out_path.join("script.sh"))?
                .permissions()
                .mode()
                & 0o777,
            0o555
        );

        // writable bits are removed, executable bits are preserved
        let writable_mode = std::fs::metadata(out_path.join("writable.txt"))?
            .permissions()
            .mode();
        assert_eq!(writable_mode & 0o222, 0);
        assert_eq!(writable_mode & 0o444, 0o444);

        Ok(())
    }

    #[test]
    fn build_encodes_filenames_with_backslash() -> Result<()> {
        let layer = TempDir::new()?;
        let out = TempDir::new()?;
        let out_path = out.path().join("output");
        let mapping_file = out.path().join("mapping.json");

        let filename_bytes: &[u8] = b"file\\with\\backslash.txt";
        let filename = OsStr::from_bytes(filename_bytes);
        let file_path = layer.path().join(filename);
        let mut file = File::create(&file_path)?;
        file.write_all(b"content with backslash filename")?;
        drop(file);

        let unprivileged_dir = UnprivilegedDir {
            base64_encoded_filenames: Some(mapping_file.clone()),
        };

        unprivileged_dir.build(&out_path, layer.path(), None)?;

        assert!(mapping_file.exists());

        let mapping: BTreeMap<PathBuf, PathBuf> =
            serde_json::from_str(&std::fs::read_to_string(&mapping_file)?)?;

        assert!(!mapping.is_empty());
        let original_path = PathBuf::from("/").join(filename);
        assert!(mapping.values().any(|v| *v == original_path));

        Ok(())
    }

    #[test]
    fn build_preserves_hardlinks() -> Result<()> {
        let layer = TempDir::new()?;
        let out = TempDir::new()?;
        let out_path = out.path().join("output");

        // Create a file with multiple hardlinks
        let mut file = File::create(layer.path().join("original.txt"))?;
        file.write_all(b"shared by many")?;
        drop(file);
        std::fs::hard_link(
            layer.path().join("original.txt"),
            layer.path().join("link1.txt"),
        )?;
        std::fs::hard_link(
            layer.path().join("original.txt"),
            layer.path().join("link2.txt"),
        )?;
        std::fs::hard_link(
            layer.path().join("original.txt"),
            layer.path().join("link3.txt"),
        )?;

        let unprivileged_dir = UnprivilegedDir {
            base64_encoded_filenames: None,
        };

        unprivileged_dir.build(&out_path, layer.path(), None)?;

        // Verify all files share the same inode (are hardlinks)
        let meta_orig = std::fs::metadata(out_path.join("original.txt"))?;
        for link in ["link1.txt", "link2.txt", "link3.txt"] {
            let link_path = out_path.join(link);
            let meta = std::fs::metadata(&link_path)?;
            assert_eq!(meta.ino(), meta_orig.ino());
        }

        Ok(())
    }
}
