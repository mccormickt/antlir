/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::ffi::OsStr;
use std::path::PathBuf;

use antlir2_compile::CompilerContext;
use antlir2_depgraph::Graph;
use clap::Parser;
use json_arg::JsonFile;

use crate::Error;
use crate::Result;

mod compile;
mod depgraph;
mod map;
mod plan;
mod shell;
pub(crate) use compile::Compile;
pub(crate) use depgraph::Depgraph;
pub(crate) use map::Map;
pub(crate) use plan::Plan;
pub(crate) use shell::Shell;

/// Args that are common to "compileish" commands (for now, 'compile' and
/// 'plan', but maybe others in the future)
#[derive(Parser, Debug)]
pub(self) struct Compileish {
    #[clap(long)]
    /// Root directory of under-construction image. Must already exist (either
    /// empty or as a snapshot of a parent layer)
    pub(crate) root: PathBuf,
    #[clap(flatten)]
    pub(crate) external: CompileishExternal,
    #[clap(long)]
    /// Path to available dnf repositories
    pub(crate) dnf_repos: PathBuf,
}

#[derive(Parser, Debug)]
/// Compile arguments that are _always_ passed from external sources (in other
/// words, by buck2 actions) and are never generated by internal code in the
/// 'isolate' subcommand.
pub(self) struct CompileishExternal {
    #[clap(long = "depgraph-json")]
    /// Path to input depgraph json file with features to include in this image
    pub(crate) depgraph: JsonFile<Graph<'static>>,
}

impl Compileish {
    #[deny(unused_variables)]
    pub(self) fn to_args(&self) -> [&OsStr; 6] {
        let Self {
            external: CompileishExternal { depgraph },
            root,
            dnf_repos,
        } = self;
        [
            OsStr::new("--depgraph-json"),
            depgraph.path().as_os_str(),
            OsStr::new("--root"),
            root.as_os_str(),
            OsStr::new("--dnf-repos"),
            dnf_repos.as_os_str(),
        ]
    }

    pub(super) fn compiler_context(&self) -> Result<CompilerContext> {
        CompilerContext::new(self.root.clone(), self.dnf_repos.clone()).map_err(Error::Compile)
    }
}