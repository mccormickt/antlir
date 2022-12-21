/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use buck_label::Label;
use serde::Deserialize;
use serde::Serialize;

pub mod apt;
pub mod clone;
pub mod ensure_dirs_exist;
pub mod install;
pub mod meta_kv;
pub mod mount;
pub mod remove;
pub mod requires;
pub mod rpms;
pub mod stat;
pub mod symlink;
pub mod tarball;
pub mod types;
pub mod usergroup;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub struct Feature<'a> {
    #[serde(borrow, rename = "__label")]
    pub label: Label<'a>,
    #[serde(flatten)]
    pub data: Data<'a>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize)]
#[serde(
    rename_all = "snake_case",
    tag = "__feature_type",
    bound(deserialize = "'de: 'a")
)]
pub enum Data<'a> {
    Apt(apt::Apt),
    Clone(clone::Clone<'a>),
    EnsureDirsExist(ensure_dirs_exist::EnsureDirsExist),
    Install(install::Install),
    Meta(meta_kv::Meta),
    Mount(mount::Mount<'a>),
    Remove(remove::Remove),
    Requires(requires::Requires),
    Rpm(rpms::Rpm<'a>),
    EnsureFileSymlink(symlink::Symlink),
    EnsureDirSymlink(symlink::Symlink),
    Tarball(tarball::Tarball),
    UserAdd(usergroup::User),
    GroupAdd(usergroup::Group),
}