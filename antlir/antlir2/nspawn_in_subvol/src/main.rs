/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::HashMap;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;

use antlir2_isolate::isolate;
use antlir2_isolate::InvocationType;
use antlir2_isolate::IsolationContext;
use anyhow::anyhow;
use anyhow::Context;
use clap::Parser;
use tracing_subscriber::filter;
use tracing_subscriber::fmt::time::LocalTime;
use tracing_subscriber::prelude::*;

#[derive(Parser, Debug)]
struct Args {
    #[clap(long)]
    subvol: PathBuf,
    /// `--bind-mount-ro src dst` creates an RO bind-mount of src to dst in the subvol
    #[clap(long, num_args = 2)]
    bind_mount_ro: Vec<PathBuf>,
    #[clap(long)]
    artifacts_require_repo: bool,
}

fn init_logging() {
    let default_filter = filter::Targets::new().with_default(tracing::Level::DEBUG);
    let log_layer = tracing_subscriber::fmt::layer()
        .with_timer(LocalTime::rfc_3339())
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .with_filter(default_filter);
    tracing_subscriber::registry().with(log_layer).init();
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    init_logging();

    let repo_root = find_root::find_repo_root(
        &absolute_path::AbsolutePathBuf::canonicalize(
            std::env::current_exe().context("while getting argv[0]")?,
        )
        .context("argv[0] not absolute")?,
    )
    .context("while looking for repo root")?;

    // antlir2_isolate re-parses these into --bind-ro args and escapes any colons, so we
    // instead take an explicit pair to not have to deal with the added complexity of
    // de-and-re-serializing.
    let bind_ro_inputs = args
        .bind_mount_ro
        .chunks(2)
        .map(|pair| match pair {
            [src, dst] => Ok((dst.clone(), src.clone())),
            _ => Err(anyhow!("Unrecognized --mount arg: {:?}", pair)),
        })
        .collect::<anyhow::Result<HashMap<_, _>>>()?;
    let mut cmd_builder = IsolationContext::builder(args.subvol);
    cmd_builder
        .inputs(bind_ro_inputs)
        .ephemeral(true)
        .invocation_type(InvocationType::Pid2Interactive);
    if args.artifacts_require_repo {
        cmd_builder.inputs(repo_root.into_path_buf());
        cmd_builder.inputs(PathBuf::from("/usr/local/fbcode"));
    }
    Err(isolate(cmd_builder.build()).into_command().exec().into())
}