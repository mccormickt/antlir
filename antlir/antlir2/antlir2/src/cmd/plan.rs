/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::path::PathBuf;

use antlir2_compile::CompileFeature;
use antlir2_depgraph::Graph;
use antlir2_rootless::Rootless;
use anyhow::Context;
use clap::Parser;
use itertools::Itertools;

use super::Compileish;
use crate::Error;
use crate::Result;

#[derive(Parser, Debug)]
/// Plan out an image compilation without doing any operations to the image.
///
/// The main focus of this for now is resolving dnf transactions ahead of time
/// to accomplish a few things:
///  - faster error reporting (we can tell if an installation will fail before
///    even attempting it)
///  - use buck2 dynamic dependencies to have buck download rpms and manage the
///  cache
pub(crate) struct Plan {
    #[clap(flatten)]
    pub(super) compileish: Compileish,
    #[clap(flatten)]
    pub(super) external: PlanExternal,
}

#[derive(Parser, Debug)]
/// Plan arguments that are _always_ passed from external sources (in other
/// words, by buck2 actions) and are never generated by internal code in the
/// 'isolate' subcommand.
pub(super) struct PlanExternal {
    #[clap(long)]
    /// Output path for serialized compiler plan
    pub(super) plan: PathBuf,
}

impl Plan {
    #[tracing::instrument(name = "plan", skip(self))]
    pub(crate) fn run(self, rootless: Option<Rootless>) -> Result<()> {
        let ctx = self.compileish.compiler_context(None)?;

        let root_guard = rootless.map(|r| r.escalate()).transpose()?;
        let depgraph = Graph::open(self.compileish.external.depgraph)?;
        let items: Vec<_> = depgraph
            .pending_features()?
            .map(|f| f.plan(&ctx).map_err(Error::Compile))
            .flatten_ok()
            .collect::<Result<_>>()?;
        drop(root_guard);

        let plan = antlir2_compile::plan::Plan::from_items(items)?;

        let f = std::fs::File::create(&self.external.plan).context("while creating plan file")?;
        serde_json::to_writer_pretty(f, &plan).context("while serializing plan")?;

        Ok(())
    }
}
