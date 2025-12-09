/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::HashMap;
use std::collections::HashSet;
use std::ffi::OsString;
use std::fs::File;
use std::io::BufWriter;
use std::io::Seek;
use std::path::Path;
use std::path::PathBuf;

use antlir2_change_stream::Iter;
use antlir2_change_stream::Operation;
use anyhow::Context;
use anyhow::Result;
use anyhow::bail;
use anyhow::ensure;
use clap::Parser;
use nix::sys::stat::SFlag;
use nix::sys::stat::major;
use nix::sys::stat::minor;
use tar::Builder;
use tar::EntryType;
use tar::Header;

/// Fixed mtime for reproducible tar archives.
/// Timestamps make things non-deterministic even if everything else is 100% equal.
/// To get around this (and to preempt any bugs from tools that don't tolerate
/// 0 timestamps very well), we use February 4, 2004 - the initial launch of thefacebook.com.
const FIXED_MTIME: u64 = 1075852800;

#[derive(Parser, Debug)]
struct Args {
    #[clap(long)]
    parent: Option<PathBuf>,
    #[clap(long)]
    child: PathBuf,
    #[clap(long)]
    out: PathBuf,
    #[clap(long)]
    rootless: bool,
}

struct Entry {
    header: Header,
    contents: Contents,
    extensions: Vec<(String, Vec<u8>)>,
}

impl Default for Entry {
    fn default() -> Self {
        let mut header = Header::new_ustar();
        header.set_mtime(FIXED_MTIME);
        Self {
            header,
            contents: Contents::Unset,
            extensions: Vec::new(),
        }
    }
}

enum Contents {
    Unset,
    Link(PathBuf),
    File(File),
}

struct Entries {
    entries: HashMap<PathBuf, Entry>,
    finished_paths: HashSet<PathBuf>,
}

impl Entries {
    fn new() -> Self {
        Self {
            entries: HashMap::new(),
            finished_paths: HashSet::new(),
        }
    }

    fn entry(&mut self, path: PathBuf) -> Result<&mut Entry> {
        if self.finished_paths.contains(&path) {
            Err(anyhow::anyhow!("{} was already closed", path.display()))
        } else {
            Ok(self.entries.entry(path).or_default())
        }
    }

    fn remove(&mut self, path: PathBuf) -> Option<Entry> {
        let entry = self.entries.remove(&path);
        self.finished_paths.insert(path);
        entry
    }

    fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn keys(&self) -> impl Iterator<Item = &PathBuf> {
        self.entries.keys()
    }
}

fn main() -> Result<()> {
    let args = Args::parse();

    if args.rootless {
        antlir2_rootless::unshare_new_userns().context("while setting up userns")?;
    }

    let stream: Iter<File> = match &args.parent {
        Some(parent) => Iter::diff(parent, &args.child)?,
        None => Iter::from_empty(&args.child)?,
    };

    let mut builder = Builder::new(BufWriter::new(File::create(&args.out)?));

    let mut entries = Entries::new();
    // separately track which paths had times set, so we can see if *only* the
    // times were updated and skip those entries
    let mut had_set_times: HashSet<PathBuf> = HashSet::new();
    // Track pending whiteout markers - only write them at the end if the file wasn't recreated
    let mut pending_whiteouts: HashSet<PathBuf> = HashSet::new();

    for change in stream {
        let change = change?;
        let path = change.path().to_owned();
        match change.into_operation() {
            Operation::Create { mode } => {
                // File is being created - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let header = &mut entries.entry(path)?.header;
                header.set_mode(mode);
                header.set_entry_type(EntryType::Regular);
            }
            Operation::Mkdir { mode } => {
                // Directory is being created - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let header = &mut entries.entry(path)?.header;
                header.set_mode(mode);
                header.set_entry_type(EntryType::Directory);
            }
            Operation::Mkfifo { mode } => {
                // FIFO is being created - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let header = &mut entries.entry(path)?.header;
                header.set_mode(mode);
                header.set_entry_type(EntryType::Fifo);
            }
            Operation::Mknod { rdev, mode } => {
                // Device node is being created - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let header = &mut entries.entry(path)?.header;
                header.set_mode(mode);
                let sflag = SFlag::from_bits_truncate(mode);
                header.set_entry_type(if sflag.contains(SFlag::S_IFBLK) {
                    EntryType::Block
                } else {
                    EntryType::Char
                });
                header.set_device_major(major(rdev) as u32)?;
                header.set_device_minor(minor(rdev) as u32)?;
            }
            Operation::Chmod { mode } => {
                // Permissions are being modified - file still exists, remove from pending whiteouts
                pending_whiteouts.remove(&path);
                let header = &mut entries.entry(path)?.header;
                header.set_mode(mode);
            }
            Operation::Chown { uid, gid } => {
                // Ownership is being modified - file still exists, remove from pending whiteouts
                pending_whiteouts.remove(&path);
                let header = &mut entries.entry(path)?.header;
                header.set_uid(uid as u64);
                header.set_gid(gid as u64);
            }
            Operation::SetTimes { atime: _, mtime: _ } => {
                // timestamps make things very non-reproducible
                had_set_times.insert(path.clone());
            }
            Operation::HardLink { target } => {
                // Link is being created - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let entry = entries.entry(path)?;
                entry.header.set_entry_type(EntryType::Link);
                entry.contents = Contents::Link(target.to_owned());
            }
            Operation::Symlink { target } => {
                // Symlink is being created - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let entry = entries.entry(path)?;
                entry.header.set_entry_type(EntryType::Symlink);
                entry.contents = Contents::Link(target.to_owned());
            }
            Operation::Rename { to: _ } => {
                // File is being renamed (recreated) - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                // just ensure an entry exists, which will end up sending the
                // full contents, since there is no way to represent a rename in
                // the layer tar
                entries.entry(path)?;
            }
            Operation::Contents { contents } => {
                // File contents are being set - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let entry = entries.entry(path)?;
                entry.contents = Contents::File(contents);
            }
            Operation::RemoveXattr { .. } => {
                // Xattr is being modified - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                // just ensure an entry exists, which will end up sending the
                // full contents
                entries.entry(path)?;
            }
            Operation::SetXattr { name, value } => {
                // Xattr is being set - remove from pending whiteouts if present
                pending_whiteouts.remove(&path);
                let entry = entries.entry(path)?;
                let mut key = "SCHILY.xattr.".to_owned();
                key.push_str(
                    name.to_str()
                        .with_context(|| format!("xattr name '{name:?}' is not valid UTF-8"))?,
                );
                entry.extensions.push((key, value))
            }
            // Removals are represented with special whiteout marker files
            // We defer writing them until the end to handle the case where
            // a file is deleted and then recreated in the same layer
            Operation::Unlink | Operation::Rmdir => {
                pending_whiteouts.insert(path.clone());
            }
            Operation::Close => {
                // we're done with an entry file, it can go into the tar now
                let mut entry = match entries.remove(path.clone()) {
                    Some(entry) => entry,
                    None => {
                        if had_set_times.contains(&path) {
                            // if the only thing that changed was the times, we
                            // can and should skip it
                            continue;
                        }
                        bail!("{path:?} was closed but never opened")
                    }
                };

                // If this file was marked for deletion (whiteout) but is now being
                // recreated, remove it from pending whiteouts
                pending_whiteouts.remove(&path);

                if path == Path::new("") {
                    // empty path (root) can't go into the tar
                    continue;
                }

                // PAX extensions go ahead of the full entry header
                entry.extensions.sort();
                builder.append_pax_extensions(
                    entry
                        .extensions
                        .iter()
                        .map(|(k, v)| (k.as_str(), v.as_slice())),
                )?;
                match entry.contents {
                    Contents::Link(target) => {
                        builder.append_link(&mut entry.header, path, target)?;
                    }
                    Contents::File(mut f) => {
                        // Stream file contents instead of loading into memory to handle
                        // large files. We manually set entry type to Regular (not Sparse)
                        // to avoid GNU sparse headers (type 'S' = 83) which some container
                        // runtimes (podman/skopeo) cannot handle.
                        // Use the accumulated entry.header which contains metadata from
                        // change stream operations (Create, Chmod, Chown, etc.)
                        // Seek to beginning in case file handle is not at start
                        f.rewind()?;
                        let metadata = f.metadata()?;
                        entry.header.set_size(metadata.len());
                        entry.header.set_entry_type(EntryType::Regular);
                        builder.append_data(&mut entry.header, path, &mut f)?;
                        drop(f);
                    }
                    Contents::Unset => {
                        // Metadata only change, but the OCI spec says that any change
                        // must send the entire contents, so open it up from the child
                        // layer.
                        let meta = std::fs::symlink_metadata(args.child.join(&path))?;
                        if meta.is_file() {
                            // Stream file contents instead of loading into memory to handle
                            // large files. We manually set entry type to Regular (not Sparse)
                            // to avoid GNU sparse headers (type 'S' = 83) which some container
                            // runtimes (podman/skopeo) cannot handle.
                            // Use entry.header which contains metadata from change stream
                            // operations (Chmod, Chown, etc.) and only set the size.
                            let mut f = File::open(args.child.join(&path))?;
                            let f_meta = f.metadata()?;
                            entry.header.set_size(f_meta.len());
                            entry.header.set_entry_type(EntryType::Regular);
                            builder.append_data(&mut entry.header, path, &mut f)?;
                        } else if meta.is_dir() {
                            // For metadata-only directory changes, ensure entry type is set
                            entry.header.set_entry_type(EntryType::Directory);
                            builder.append_data(&mut entry.header, path, std::io::empty())?;
                        } else if meta.is_symlink() {
                            // For metadata-only symlink changes, ensure entry type is set
                            entry.header.set_entry_type(EntryType::Symlink);
                            let target = std::fs::read_link(args.child.join(&path))?;
                            builder.append_link(&mut entry.header, path, target)?;
                        } else {
                            bail!(
                                "not sure what to do with unset contents on filetype {:?}",
                                meta.file_type(),
                            );
                        }
                    }
                }
            }
        }
    }

    // Write all pending whiteout markers for files that were deleted and not recreated.
    // Skip redundant nested whiteouts - if a parent directory is being deleted,
    // we don't need whiteout markers for its children.
    for wh_path in &pending_whiteouts {
        // Check if any ancestor of this path is also being deleted
        let has_deleted_ancestor = wh_path
            .ancestors()
            .skip(1) // Skip the path itself
            .any(|ancestor| pending_whiteouts.contains(ancestor));

        if has_deleted_ancestor {
            // Parent directory is being deleted, so this child whiteout is redundant
            continue;
        }

        let mut wh_name = OsString::from(".wh.");
        wh_name.push(wh_path.file_name().expect("root dir cannot be deleted"));
        let wh_full_path = wh_path.parent().unwrap_or(Path::new("")).join(wh_name);
        let mut header = Header::new_ustar();
        header.set_mtime(FIXED_MTIME);
        header.set_mode(0o644);
        header.set_entry_type(EntryType::Regular);
        builder.append_data(&mut header, wh_full_path, std::io::empty())?;
    }

    ensure!(
        entries.is_empty(),
        "not all entries were closed: {}",
        entries
            .keys()
            .map(|p| p.to_string_lossy())
            .collect::<Vec<_>>()
            .join(", ")
    );
    Ok(())
}
