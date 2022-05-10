/*
 * Copyright (c) Meta Platforms, Inc. and its affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use anyhow::Context;
use futures::future::try_join_all;
use slog::Logger;

use metalos_host_configs::packages::generic::Package;
use package_download::{ensure_package_on_disk_ignoring_artifacts, HttpsDownloader};
use state::{State, Token};

/// Any config that can be staged on-host consists of a list of packages.
/// Staging is downloading those packages then optionally running some kind of
/// preflight checks.
pub trait StagableConfig: State<state::Thrift> {
    /// Return a list of every package in this config, after which they will be
    /// scheduled for parallel downloading.
    fn packages(&self) -> Vec<Package>;

    /// Called after all packages have successfully downloaded so that any
    /// stage-blocking checks can be made on the downloaded artifacts. For
    /// example, RuntimeConfig might want to check that service images have a
    /// valid systemd unit.
    fn check_downloaded_artifacts(&self) -> anyhow::Result<()> {
        Ok(())
    }
}

/// Stage a config, downloading any packages and performing any stage-blocking
/// checks.
pub async fn stage<C>(log: Logger, conf: C) -> anyhow::Result<Token<C, state::Thrift>>
where
    C: StagableConfig,
{
    let downloader = HttpsDownloader::new().context("while constructing HTTPS downloader")?;
    try_join_all(conf.packages().into_iter().map(|package| {
        let log = log.clone();
        let downloader = downloader.clone();
        async move {
            ensure_package_on_disk_ignoring_artifacts(log, &downloader, &package).await?;
            Ok::<_, package_download::Error>(())
        }
    }))
    .await
    .context("while downloading packages")?;
    conf.check_downloaded_artifacts()
        .context("stage-blocking checks failed")?;
    let token = conf.save().context("while save config to disk")?;
    token.stage().context("while staging config on disk")?;
    Ok(token)
}