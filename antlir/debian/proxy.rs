/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::net::TcpListener;
#[cfg(unix)]
use std::os::unix::io::FromRawFd;
use std::os::unix::io::RawFd;
use std::sync::Arc;

use anyhow::anyhow;
use anyhow::Error;
use anyhow::Result;
use blob_store::PackageBackend;
use clap::Parser;
use fbinit::FacebookInit;
use log::info;
use manifold_client::cpp_client::ClientOptionsBuilder;
use manifold_client::cpp_client::ManifoldCppClient;
use snapshotter_helpers::ANTLIR_SNAPSHOTS_BUCKET;
use snapshotter_helpers::API_KEY;
use tokio::net::TcpListener as TokioTcpListener;
use tokio_stream::wrappers::TcpListenerStream;
use urlencoding::decode;
use warp::reject::Reject;
use warp::Filter;

#[derive(Debug)]
struct ProxyError(Error);

impl Reject for ProxyError {}

#[derive(Parser)]
struct Args {
    #[clap(long)]
    socket_fd: RawFd,
}

async fn serve_blob(
    hash: String,
    client: Arc<dyn PackageBackend>,
) -> std::result::Result<Vec<u8>, warp::reject::Rejection> {
    let d_hash = decode(&hash);
    match d_hash {
        Ok(value) => match client.get(&value).await {
            Ok(res) => Ok(res.to_vec()),
            Err(e) => Err(warp::reject::custom(ProxyError(e))),
        },
        Err(e) => Err(warp::reject::custom(ProxyError(anyhow!(e)))),
    }
}

async fn serve_repo_artifact(
    hash_file_name: String,
    client: Arc<dyn PackageBackend>,
) -> std::result::Result<Vec<u8>, warp::reject::Rejection> {
    let hash_name = hash_file_name.split_once('-');
    match hash_name {
        Some((hash, _name)) => serve_blob(hash.to_string(), client.clone()).await,
        None => Err(warp::reject::custom(ProxyError(anyhow!(
            "{} must match the format '$hash-$name'",
            hash_file_name
        )))),
    }
}

async fn serve(client: Arc<dyn PackageBackend>, socket_fd: RawFd) -> Result<()> {
    let cl2 = client.clone();
    let release_file = warp::path!("dists" / String / "Release")
        .and_then(move |hash: String| serve_blob(hash, client.clone()));

    let client = cl2.clone();
    let package_file = warp::path!(
        "dists" / String / String / String / "by-hash" / String / String
    )
    .and_then(move |_dist, _component, _binary, hash_key, hash| {
        serve_blob(format!("{}:{}", hash_key, hash), cl2.clone())
    });

    let cl2 = client.clone();
    let deb_file =
        warp::path!("deb" / String).and_then(move |hash: String| serve_blob(hash, client.clone()));
    let log = warp::log("apt::proxy");

    let client = cl2.clone();
    let repomd_xml = warp::path!("dists" / String / "repodata" / "repomd.xml")
        .and_then(move |hash: String| serve_blob(hash, cl2.clone()));

    let cl2 = client.clone();
    let repo_artifacts = warp::path!("dists" / String / "repodata" / String).and_then(
        move |_repo_hash, artifact_hash_file: String| {
            serve_repo_artifact(artifact_hash_file, client.clone())
        },
    );

    let rpms = warp::path!("dists" / String / "rpm" / String)
        .and_then(move |_repo_hash, rpm_hash| serve_blob(rpm_hash, cl2.clone()));
    let routes = release_file
        .or(package_file)
        .or(deb_file)
        .or(repomd_xml)
        .or(repo_artifacts)
        .or(rpms)
        .with(log);

    info!(
        "\n\tI don't know who you are,\t
        I don't know the deb or rpm you are looking for,\t
        but i have hashes, a very particular set of hashes,\t
        hashes that I've acquired with snapshotter,\t
        I will find it and serve it",
    );
    let tcp_listener = unsafe { TcpListener::from_raw_fd(socket_fd) };
    tcp_listener.set_nonblocking(true)?;
    let tokio_tcp_listner = TokioTcpListener::from_std(tcp_listener)?;
    let incoming = TcpListenerStream::new(tokio_tcp_listner);
    warp::serve(routes).serve_incoming(incoming).await;
    Ok(())
}

#[fbinit::main]
async fn main(fb: FacebookInit) -> Result<()> {
    let args = Args::parse();
    pretty_env_logger::init();
    let manifold_client_opts = ClientOptionsBuilder::default()
        .api_key(API_KEY)
        .build()
        .map_err(Error::msg)?;
    let manifold_client =
        ManifoldCppClient::from_options(fb, ANTLIR_SNAPSHOTS_BUCKET, &manifold_client_opts)
            .map_err(Error::from)?;
    serve(Arc::new(manifold_client), args.socket_fd).await
}
