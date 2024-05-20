/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::marker::PhantomData;
use std::path::PathBuf;

use super::layer::AntlirLayer;
use super::path::VerifiedPath;

pub struct AntlirSubvolume<L: AntlirLayer> {
    pub relative_path: PathBuf,
    layer: PhantomData<L>,
}

impl<L: AntlirLayer> AntlirSubvolume<L> {
    pub fn new_unchecked(relative_path: PathBuf) -> Self {
        Self {
            relative_path,
            layer: Default::default(),
        }
    }

    pub fn mount_unchecked(&self, target: VerifiedPath) -> L {
        L::new_unchecked(target)
    }
}

/// A marker trait indicating that this struct is actually
/// generated by the macro below.
pub trait AntlirSubvolumes: super::AntlirPackaged {
    fn new_unchecked() -> Self;
}

#[macro_export]
macro_rules! generate_subvolumes {
    ($name:ident { $($subvol_name:ident ($subvol_type:ty, $path:tt)),* $(,)* }) => {
        pub struct $name {}

        impl $crate::antlir_image::AntlirPackaged for $name {}
        impl $crate::antlir_image::subvolume::AntlirSubvolumes for $name {
            fn new_unchecked() -> Self {
                Self { }
            }
        }

        impl $name {
            $(
                #[allow(dead_code)]
                pub fn $subvol_name(&self) -> $subvol_type {
                    $crate::antlir_image::subvolume::AntlirSubvolume::new_unchecked($path.into())
                }
            )*
        }
    }
}
