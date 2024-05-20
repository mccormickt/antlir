/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::path::Path;
use std::path::PathBuf;

#[derive(Debug, thiserror::Error)]
pub enum PathError {
    #[error("Provided path {0:?} doesn't exist")]
    NotFound(PathBuf),
    #[error("Failed to create requested path {0:?}: {1:?}")]
    FailedToMkdir(PathBuf, std::io::Error),
}

/// This is a path that is guaranteed to exist. We can know this either:
///  - Because it's in an image
///  - We called path.exists on it
///  - We created it
#[derive(Debug, Clone, PartialEq)]
pub struct VerifiedPath(PathBuf);

impl VerifiedPath {
    /// this is only meant to be used internally but there will likely be times that you know
    /// something that this library can't (sounds like a pull request?) so you may need to use
    /// this. A common example might be tests.
    pub fn new_unchecked(path: PathBuf) -> Self {
        Self(path)
    }

    /// This function should be avoided wherever possible. This should only be used if
    /// you have absolutely no way of validating this path exists statically and you aren't
    /// the one who created it.
    pub fn new_checked(path: PathBuf) -> Result<Self, PathError> {
        if path.exists() {
            Ok(Self::new_unchecked(path))
        } else {
            Err(PathError::NotFound(path))
        }
    }

    pub fn create(path: PathBuf) -> Result<Self, PathError> {
        match std::fs::create_dir_all(&path) {
            Ok(()) => Ok(Self::new_unchecked(path)),
            Err(e) => Err(PathError::FailedToMkdir(path, e)),
        }
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl AsRef<Path> for VerifiedPath {
    fn as_ref(&self) -> &Path {
        self.path()
    }
}

/// A marker trait indicating that this struct is actually
/// generated by the macro below.
pub trait AntlirPaths {}

#[macro_export]
macro_rules! generate_paths {
    ($name:ident { $($path_name:ident ($path_type:ty, $path:tt)),* $(,)* }) => {
        pub struct $name {
            base: $crate::antlir_image::path::VerifiedPath,
        }

        impl $crate::antlir_image::path::AntlirPaths for $name {}

        impl $name {
            #[allow(dead_code)]
            pub fn new_unchecked(base: $crate::antlir_image::path::VerifiedPath) -> Self {
                Self { base }
            }

            $(
                #[allow(dead_code)]
                pub fn $path_name(&self) -> $path_type {
                    <$path_type>::new_unchecked(
                        self.base.path().join($path)
                    )
                }
            )*
        }
    }
}
