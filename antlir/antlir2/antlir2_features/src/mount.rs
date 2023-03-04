/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::path::PathBuf;

use serde::de::Error;
use serde::Deserialize;
use serde::Serialize;

use crate::types::LayerInfo;
use crate::types::PathInLayer;

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case", bound(deserialize = "'de: 'a"))]
pub enum Mount<'a> {
    Host(HostMount<'a>),
    Layer(LayerMount<'a>),
}

/// Buck2's `record` will always include `null` values, but serde's native enum
/// deserialization will fail if there are multiple keys, even if the others are
/// null.
/// TODO(vmagro): make this general in the future (either codegen from `record`s
/// or as a proc-macro)
impl<'a, 'de: 'a> Deserialize<'de> for Mount<'a> {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        #[serde(bound(deserialize = "'de: 'a"))]
        struct MountStruct<'a> {
            host: Option<HostMount<'a>>,
            layer: Option<LayerMount<'a>>,
        }

        MountStruct::deserialize(deserializer).and_then(|s| match (s.host, s.layer) {
            (Some(v), None) => Ok(Self::Host(v)),
            (None, Some(v)) => Ok(Self::Layer(v)),
            (_, _) => Err(D::Error::custom("exactly one of {host, layer} must be set")),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize)]
#[serde(bound(deserialize = "'de: 'a"))]
pub struct HostMount<'a> {
    pub mountpoint: PathInLayer<'a>,
    pub is_directory: bool,
    pub src: PathBuf,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize)]
#[serde(rename_all = "snake_case", bound(deserialize = "'de: 'a"))]
pub struct LayerMount<'a> {
    pub mountpoint: PathInLayer<'a>,
    pub src: LayerInfo<'a>,
}

impl<'a> Mount<'a> {
    pub fn mountpoint(&self) -> &PathInLayer {
        match self {
            Self::Host(h) => &h.mountpoint,
            Self::Layer(l) => &l.mountpoint,
        }
    }

    pub fn is_directory(&self) -> bool {
        match self {
            Self::Layer(_) => true,
            Self::Host(h) => h.is_directory,
        }
    }
}
