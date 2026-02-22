/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

//! Hermetic `apk index` runner.
//!
//! Produces an APKINDEX.tar.gz alongside a set of pre-built `.apk` files
//! by invoking `apk index` from inside a wolfi build appliance, rather
//! than relying on host PATH for `apk-tools`. Lets antlir2's apk_repo
//! rule build on any host that can run an unprivileged userns
//! (Docker, RHEL, Debian, Ubuntu, …) without needing `apk-tools`
//! pre-installed.

use std::fs;
use std::path::PathBuf;

use anyhow::Context;
use anyhow::Result;
use clap::Parser;

#[derive(Debug, Parser)]
struct Args {
    #[clap(long)]
    rootless: bool,
    #[clap(long)]
    build_appliance: PathBuf,
    /// Output directory. The indexer writes `<arch>/<apk-files...>` and
    /// `<arch>/APKINDEX.tar.gz` into this directory.
    #[clap(long)]
    out_dir: PathBuf,
    #[clap(long)]
    arch: String,
    /// Path to a `.apk` file to include in the repo. Repeat for each.
    #[clap(long = "apk")]
    apks: Vec<PathBuf>,
    /// Optional signing key for `apk index --sign-key`. When set, the
    /// indexer signs APKINDEX.tar.gz with this key inside the BA.
    #[clap(long)]
    signing_key: Option<PathBuf>,
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

    // Stage every .apk into out_dir/<arch>/, where `apk index` expects
    // them. The host-side copy is fine outside the isolation: just a
    // regular fs operation against buck-out.
    let arch_dir = args.out_dir.join(&args.arch);
    fs::create_dir_all(&arch_dir)
        .with_context(|| format!("creating {}", arch_dir.display()))?;

    let mut apk_filenames = Vec::with_capacity(args.apks.len());
    for apk in &args.apks {
        let name = apk
            .file_name()
            .with_context(|| format!("apk path has no filename: {}", apk.display()))?
            .to_owned();
        let dst = arch_dir.join(&name);
        fs::copy(apk, &dst)
            .with_context(|| format!("copying {} -> {}", apk.display(), dst.display()))?;
        apk_filenames.push(name);
    }

    if apk_filenames.is_empty() {
        anyhow::bail!("apk_repo received no apk files; nothing to index");
    }

    // Run `apk index` inside the BA. The BA's rootfs becomes /, so apk
    // is at /sbin/apk (wolfi) or /usr/bin/apk depending on layout. We
    // mount the output dir under a fixed path and cd there so the
    // produced APKINDEX.tar.gz lands in the right place on the host.
    let cwd = std::env::current_dir().context("getting cwd")?;
    let mut isol = antlir2_isolate::IsolationContext::builder(&args.build_appliance);
    isol.ephemeral(false)
        .readonly()
        .inputs((
            PathBuf::from("/__antlir2__/working_directory"),
            cwd,
        ))
        .working_directory(PathBuf::from("/__antlir2__/working_directory"))
        .outputs((
            PathBuf::from("/__antlir2__/out"),
            args.out_dir.clone(),
        ))
        .tmpfs(PathBuf::from("/tmp"))
        .setenv(("TMPDIR", "/tmp"));
    if let Some(key) = &args.signing_key {
        isol.inputs((
            PathBuf::from("/__antlir2__/signing-key"),
            key.clone(),
        ));
    }

    let isol = antlir2_isolate::unshare(isol.build())?;

    let mut cmd = isol.command("apk")?;
    cmd.current_dir(PathBuf::from("/__antlir2__/out").join(&args.arch));
    cmd.arg("index").arg("-o").arg("APKINDEX.tar.gz");
    if args.signing_key.is_some() {
        cmd.arg("--sign-key").arg("/__antlir2__/signing-key");
    }
    for name in &apk_filenames {
        cmd.arg(format!("./{}", name.to_string_lossy()));
    }

    let status = cmd
        .status()
        .context("spawning apk index inside the build appliance")?;
    anyhow::ensure!(
        status.success(),
        "apk index failed inside the build appliance: {:?}",
        status
    );
    Ok(())
}
