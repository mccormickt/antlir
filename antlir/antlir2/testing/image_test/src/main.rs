/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::BTreeMap;
use std::collections::BTreeSet;
use std::collections::HashMap;
use std::collections::HashSet;
use std::ffi::OsString;
use std::io::Seek;
use std::io::Write;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::path::PathBuf;

use antlir2_features::mount::Mount;
use antlir2_isolate::isolate;
use antlir2_isolate::IsolationContext;
use anyhow::Context;
use anyhow::Result;
use clap::Parser;
use json_arg::JsonFile;
use tempfile::NamedTempFile;
use tracing::debug;
use tracing_subscriber::prelude::*;

fn make_log_files(_base: &str) -> Result<(NamedTempFile, NamedTempFile)> {
    Ok((NamedTempFile::new()?, NamedTempFile::new()?))
}

#[derive(Parser, Debug)]
/// Run a unit test inside an image layer.
struct Args {
    #[clap(long)]
    /// Path to layer to run the test in
    layer: PathBuf,
    #[clap(long, default_value = "root")]
    /// Run the test as this user
    user: String,
    #[clap(long)]
    /// Boot the container with /init as pid1 before running the test
    boot: bool,
    #[clap(long)]
    /// Pass these env vars into the test environment
    preserve_env: Vec<String>,
    #[clap(long)]
    /// Mounts required by the layer-under-test
    mounts: JsonFile<BTreeSet<Mount<'static>>>,
    #[clap(subcommand)]
    test: Test,
}

#[derive(Parser, Debug)]
enum Test {
    Custom {
        test_cmd: Vec<OsString>,
    },
    Gtest {
        #[clap(long, env = "GTEST_OUTPUT")]
        output: Option<String>,
        #[clap(allow_hyphen_values = true)]
        test_cmd: Vec<OsString>,
    },
    Pyunit {
        #[clap(long)]
        list_tests: Option<PathBuf>,
        #[clap(long)]
        output: Option<PathBuf>,
        #[clap(long)]
        test_filter: Vec<OsString>,
        test_cmd: Vec<OsString>,
    },
    Rust {
        #[clap(allow_hyphen_values = true)]
        test_cmd: Vec<OsString>,
    },
}

impl Test {
    /// Some tests need to write to output paths on the host. Instead of a
    /// complicated fd-passing dance in the name of isolation purity, we just
    /// mount the parent directories of the output files so that the inner test
    /// can do writes just as tpx expects.
    fn bind_mounts(&self) -> HashSet<PathBuf> {
        match self {
            Self::Custom { .. } => HashSet::new(),
            Self::Gtest { output, .. } => match output {
                Some(output) => {
                    let path = Path::new(match output.split_once(':') {
                        Some((_format, path)) => path,
                        None => output.as_str(),
                    });
                    HashSet::from([path
                        .parent()
                        .expect("output file always has parent")
                        .to_owned()])
                }
                None => HashSet::new(),
            },
            Self::Rust { .. } => HashSet::new(),
            Self::Pyunit {
                list_tests, output, ..
            } => {
                let mut paths = HashSet::new();
                if let Some(p) = list_tests {
                    paths.insert(
                        p.parent()
                            .expect("output file always has parent")
                            .to_owned(),
                    );
                }
                if let Some(p) = output {
                    paths.insert(
                        p.parent()
                            .expect("output file always has parent")
                            .to_owned(),
                    );
                }
                paths
            }
        }
    }
    fn into_inner_cmd(self) -> Vec<OsString> {
        match self {
            Self::Custom { test_cmd } => test_cmd,
            Self::Gtest {
                mut test_cmd,
                output,
            } => {
                if let Some(out) = output {
                    test_cmd.push(format!("--gtest_output={out}").into());
                }
                test_cmd
            }
            Self::Rust { test_cmd } => test_cmd,
            Self::Pyunit {
                mut test_cmd,
                list_tests,
                test_filter,
                output,
            } => {
                if let Some(list) = list_tests {
                    test_cmd.push("--list-tests".into());
                    test_cmd.push(list.into());
                }
                if let Some(out) = output {
                    test_cmd.push("--output".into());
                    test_cmd.push(out.into());
                }
                for filter in test_filter {
                    test_cmd.push("--test-filter".into());
                    test_cmd.push(filter);
                }
                test_cmd
            }
        }
    }
}

fn main() -> Result<()> {
    let args = Args::parse();

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

    let repo = find_root::find_repo_root(
        &absolute_path::AbsolutePathBuf::canonicalize(
            std::env::current_exe().context("while getting argv[0]")?,
        )
        .context("argv[0] not absolute")?,
    )
    .context("while looking for repo root")?;

    let mut setenv: BTreeMap<_, _> = args
        .preserve_env
        .into_iter()
        .filter_map(|key| std::env::var_os(&key).map(|val| (key, val)))
        .collect();
    // forward test runner env vars to the inner test
    for (key, val) in std::env::vars() {
        if key.starts_with("TEST_PILOT") {
            setenv.insert(key, val.into());
        }
    }

    let working_directory = std::env::current_dir().context("while getting cwd")?;

    let mut ctx = IsolationContext::builder(&args.layer);
    ctx.platform([
        // test is built out of the repo, so it needs the
        // repo to be available
        repo.as_ref(),
        #[cfg(facebook)]
        Path::new("/usr/local/fbcode"),
        #[cfg(facebook)]
        Path::new("/mnt/gvfs"),
    ])
    .inputs([
        // tests often read resource files from the repo
        repo.as_ref(),
    ])
    .working_directory(&working_directory)
    .setenv(setenv.clone())
    .outputs(args.test.bind_mounts())
    .boot(args.boot);
    ctx.inputs(
        args.mounts
            .into_inner()
            .into_iter()
            .map(|mount| match mount {
                Mount::Host(m) => (m.mountpoint.into_owned(), m.src),
                Mount::Layer(m) => (m.mountpoint.into_owned(), m.src.subvol_symlink.into_owned()),
            })
            .collect::<HashMap<_, _>>(),
    );

    if args.boot {
        // see 'man 8 systemd-run-generator', tl;dr this will:
        // - propagate the exit code to this process
        // - shut down the container as soon as the test binary finishes
        let mut systemd_run_arg = OsString::from("systemd.run=\"");
        let mut iter = args.test.into_inner_cmd().into_iter().peekable();
        while let Some(arg) = iter.next() {
            systemd_run_arg.push(arg);
            if iter.peek().is_some() {
                systemd_run_arg.push(" ");
            }
        }
        systemd_run_arg.push("\"");
        let (container_stdout, _container_stderr) = make_log_files("container")?;
        let (mut test_stdout, mut test_stderr) = make_log_files("test")?;
        let mut dropin = NamedTempFile::new()?;
        write!(dropin, "[Service]\nStandardOutput=truncate:")?;
        dropin.write_all(test_stdout.path().as_os_str().as_bytes())?;
        dropin.write_all(b"\n")?;
        write!(dropin, "StandardError=")?;
        dropin.write_all(test_stderr.path().as_os_str().as_bytes())?;
        dropin.write_all(b"\n")?;
        for (key, val) in &setenv {
            write!(dropin, "Environment=\"{key}=")?;
            dropin.write_all(val.as_bytes())?;
            writeln!(dropin, "\"")?;
        }
        // forward test runner env vars to the inner test
        for (key, val) in std::env::vars() {
            if key.starts_with("TEST_PILOT") {
                writeln!(dropin, "Environment=\"{key}={val}\"")?;
            }
        }
        ctx.outputs(test_stdout.path());
        ctx.outputs(test_stderr.path());
        ctx.inputs((
            Path::new("/run/systemd/system/kernel-command-line.service.d/test-out.conf"),
            dropin.path(),
        ));

        let mut isol = isolate(ctx.build());
        isol.command.arg(systemd_run_arg);
        debug!("executing test in booted isolated container: {isol:?}");
        let mut child = isol
            .command
            // the stdout/err of the systemd inside the container is a pipe
            // so that we can print it IFF the test fails
            .stdout(container_stdout.as_file().try_clone()?)
            .stderr(container_stdout.as_file().try_clone()?)
            .spawn()
            .context("while spawning systemd-nspawn")?;
        let res = child.wait().context("while waiting for systemd-nspawn")?;
        report(container_stdout)?;

        std::io::copy(&mut test_stdout, &mut std::io::stdout())?;
        std::io::copy(&mut test_stderr, &mut std::io::stderr())?;

        if !res.success() {
            std::process::exit(res.code().unwrap_or(255))
        } else {
            Ok(())
        }
    } else {
        let mut isol = isolate(ctx.build());
        isol.command.args(args.test.into_inner_cmd());
        debug!("executing test in isolated container: {isol:?}");
        return Err(anyhow::anyhow!(
            "failed to exec test: {:?}",
            isol.command.exec()
        ));
    }
}

fn report(mut container_stdout: NamedTempFile) -> Result<()> {
    // if tpx is running this test, have it upload the logs
    if let Some(artifacts_dir) = std::env::var_os("TEST_RESULT_ARTIFACTS_DIR") {
        std::fs::create_dir_all(&artifacts_dir)?;
        let dst = Path::new(&artifacts_dir).join("container-stdout.txt");
        // In case the output is not tmpfs, rename will not work so we need to
        // copy the bytes explicitly
        if let Err(mut e) = container_stdout.persist(&dst) {
            e.file.rewind()?;
            let mut dst = std::fs::File::create(&dst)?;
            std::io::copy(&mut e.file, &mut dst)?;
        }
        if let Some(annotations_dir) = std::env::var_os("TEST_RESULT_ARTIFACT_ANNOTATIONS_DIR") {
            std::fs::create_dir_all(&annotations_dir)?;
            std::fs::write(
                Path::new(&annotations_dir).join("container-stdout.txt.annotation"),
                r#"{"type": {"generic_text_log": {}}, "description": "systemd logs"}"#,
            )?;
        }
    }
    // otherwise, print it out on stderr
    else {
        std::io::copy(&mut container_stdout, &mut std::io::stderr())?;
    }
    Ok(())
}