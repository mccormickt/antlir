/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::HashSet;
use std::ffi::OsString;
use std::path::PathBuf;

use antlir2_isolate::Ephemeral;
use antlir2_isolate::IsolationContext;
use antlir2_isolate::nspawn;
use antlir2_isolate::unshare;
use anyhow::Context;
use anyhow::Result;
use anyhow::ensure;
use clap::Parser;
use tracing_subscriber::prelude::*;

#[derive(Debug, Parser)]
struct Args {
    /// Path to mounted layer
    layer: PathBuf,
    #[clap(long = "input")]
    inputs: Vec<PathBuf>,
    #[clap(long = "output")]
    outputs: Vec<PathBuf>,
    #[clap(long = "create-output-file")]
    create_output_files: Vec<PathBuf>,
    #[clap(long, conflicts_with = "ephemeral_btrfs")]
    /// Use layer as readonly root, don't make an ephemeral snapshot
    readonly: bool,
    #[clap(long, conflicts_with = "readonly")]
    ephemeral_btrfs: bool,
    #[clap(long)]
    rootless: bool,
    program: OsString,
    args: Vec<OsString>,
}

fn main() -> Result<()> {
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::fmt::Layer::default()
                .event_format(
                    tracing_glog::Glog::default()
                        .with_span_context(true)
                        .with_timer(tracing_glog::LocalTime::default()),
                )
                .fmt_fields(tracing_glog::GlogFields::default()),
        )
        .with(tracing_subscriber::EnvFilter::from_default_env())
        .init();

    antlir2_rootless::init().context("while setting up antlir2_rootless")?;

    let args = Args::parse();

    if args.rootless {
        antlir2_rootless::unshare_new_userns().context("while unsharing userns")?;
    }

    for path in &args.create_output_files {
        std::fs::File::create(path)
            .with_context(|| format!("while creating '{}'", path.display()))?;
    }
    let cwd = std::env::current_dir().context("while getting cwd")?;
    let mut ctx = IsolationContext::builder(args.layer);
    ctx.inputs(args.inputs.into_iter().collect::<HashSet<_>>())
        .outputs(args.outputs.into_iter().collect::<HashSet<_>>())
        .outputs(args.create_output_files.into_iter().collect::<HashSet<_>>())
        .outputs(cwd.clone())
        .working_directory(cwd);
    if args.readonly {
        ctx.ephemeral(false);
    } else if args.ephemeral_btrfs {
        ctx.ephemeral(Ephemeral::Btrfs);
    } else {
        ctx.ephemeral(true);
    }
    let ctx = ctx.build();
    let ctx = if args.rootless {
        unshare(ctx).context("while isolating")?
    } else {
        nspawn(ctx).context("while isolating")?
    };
    let res = ctx.command(args.program)?.args(args.args).spawn()?.wait()?;
    ensure!(res.success(), "isolated command failed");
    Ok(())
}
