/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::fs::File;
use std::io::BufWriter;
use std::path::Path;
use std::path::PathBuf;

use antlir2_compile::Arch;
use anyhow::Context;
use anyhow::Result;
use buck_label::Label;
use clap::Parser;
use json_arg::JsonFile;

use apk::ApkItem;

#[derive(Debug, Parser)]
struct Args {
    #[clap(long)]
    rootless: bool,
    #[clap(long)]
    label: Label,
    #[clap(long)]
    build_appliance: PathBuf,
    #[clap(long)]
    parent_subvol_symlink: Option<PathBuf>,
    #[clap(long)]
    repos: PathBuf,
    /// Set when at least one URL repo is configured. The planner uses
    /// this to decide whether the unshare sandbox needs network access.
    #[clap(long)]
    has_url_repos: bool,
    #[clap(long)]
    target_arch: Arch,
    #[clap(long)]
    items: JsonFile<Vec<ApkItem>>,
    #[clap(long)]
    driver_cmd: Vec<String>,
    #[clap(long)]
    out: PathBuf,
}

enum Parent {
    Subvol(PathBuf),
    None,
}

impl Parent {
    fn path(&self) -> Option<&Path> {
        match self {
            Self::Subvol(p) => Some(p),
            Self::None => None,
        }
    }
}

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_max_level(tracing::Level::TRACE)
        .init();

    let args = Args::parse();

    if args.rootless {
        antlir2_rootless::unshare_new_userns().context("while setting up userns")?;
    }

    antlir2_isolate::unshare_and_privatize_mount_ns().context("while isolating mount ns")?;

    let parent = args
        .parent_subvol_symlink
        .map_or(Parent::None, Parent::Subvol);

    let apk = apk::Apk {
        items: args.items.into_inner(),
        driver_cmd: args.driver_cmd,
    };
    let tx = apk
        .plan(apk::DriverContext::plan(
            args.label,
            parent.path().map(Path::to_owned),
            args.build_appliance,
            args.repos,
            args.has_url_repos,
            args.target_arch,
        ))
        .context("while planning transaction")?;
    let out = BufWriter::new(File::create(&args.out).context("while creating output file")?);
    serde_json::to_writer(out, &tx).context("while serializing plan")?;
    Ok(())
}
