/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::BTreeMap;
use std::ffi::OsString;
use std::os::unix::io::FromRawFd;
use std::os::unix::io::OwnedFd;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::Command;
use std::process::Stdio;

use anyhow::Context;
use anyhow::Result;
use bon::Builder;
use clap::Parser;
use nix::unistd::User;
use serde::Deserialize;
use serde::Serialize;

#[derive(Debug, Clone, Builder, Serialize, Deserialize)]
/// Specification of how to execute the test.
/// This specification is just how to invoke the inner test binary, the
/// containerization should already have been set up by 'spawn'.
pub(crate) struct Spec {
    /// The test command
    cmd: Vec<OsString>,
    /// CWD of the test
    working_directory: PathBuf,
    /// Run the test as this user
    user: String,
    /// Set these env vars in the test environment
    #[serde(default)]
    env: BTreeMap<String, String>,
}

#[derive(Debug, Parser)]
/// Execute the inner test
pub(crate) struct Args {
    /// Args to pass to the inner test binary
    args: Vec<OsString>,
}

impl Args {
    pub(crate) fn run(self) -> Result<()> {
        let spec = std::fs::read_to_string("/__antlir2_image_test__/exec_spec.json")
            .context("while reading '/__antlir2_image_test__/exec_spec.json'")?;
        let spec: Spec = serde_json::from_str(&spec)
            .context("while parsing '/__antlir2_image_test__/exec_spec.json'")?;
        std::env::set_current_dir(&spec.working_directory)
            .with_context(|| format!("while changing to '{}'", spec.working_directory.display()))?;
        let mut env = spec.env;
        env.insert("USER".into(), spec.user.clone());
        env.insert(
            "PWD".into(),
            spec.working_directory
                .to_str()
                .with_context(|| {
                    format!("pwd '{}' was not utf8", spec.working_directory.display())
                })?
                .into(),
        );

        let user = User::from_name(&spec.user)
            .context("failed to lookup user")?
            .with_context(|| format!("no such user '{}'", spec.user))?;

        // Check if streaming output is enabled (from spec.env, not process env)
        let stream_output = env
            .get("ANTLIR_STREAM_TO_CONSOLE")
            .map(|v| v == "1")
            .unwrap_or(false);

        // Get extra test args if set (from spec.env), split into vector
        let extra_test_args: Vec<&str> = env
            .get("ANTLIR_EXTRA_TEST_ARGS")
            .map(|s| s.split_whitespace().collect())
            .unwrap_or_default();

        // Build the command
        let mut cmd_iter = spec.cmd.into_iter();
        let mut cmd = Command::new(cmd_iter.next().context("test command was empty")?);
        cmd.args(cmd_iter)
            .args(&self.args)
            .args(&extra_test_args)
            .envs(env)
            .uid(user.uid.into())
            .gid(user.gid.into());

        if stream_output {
            // Setup tee to duplicate output to both stdout and /dev/console
            // 1. Create a pipe
            // 2. Spawn `tee -a /dev/console` with stdin from pipe read end
            // 3. Redirect our stdout/stderr to pipe write end

            let (pipe_read, pipe_write) = nix::unistd::pipe().context("while creating pipe")?;
            let pipe_read: OwnedFd = pipe_read;
            let pipe_write: OwnedFd = pipe_write;

            // Spawn tee as a helper process
            // tee reads from stdin and writes to both stdout and /dev/console
            // Mute tee's stderr to suppress I/O error messages (expected due to broken pipes in
            // the case of terminations)
            Command::new("tee")
                .arg("--output-error=exit")
                .arg("-a")
                .arg("/dev/console")
                .stdin(Stdio::from(pipe_read))
                .stderr(Stdio::null())
                .spawn()
                .context("while spawning tee")?;

            // Redirect stdout and stderr to the pipe write end using dup2
            // SAFETY: STDOUT_FILENO and STDERR_FILENO are valid open file descriptors
            unsafe {
                let stdout = OwnedFd::from_raw_fd(nix::libc::STDOUT_FILENO);
                let stderr = OwnedFd::from_raw_fd(nix::libc::STDERR_FILENO);
                nix::unistd::dup2(&pipe_write, &mut std::mem::ManuallyDrop::new(stdout))
                    .context("while redirecting stdout to pipe")?;
                nix::unistd::dup2(&pipe_write, &mut std::mem::ManuallyDrop::new(stderr))
                    .context("while redirecting stderr to pipe")?;
            }

            // Close the original pipe_write fd (now duplicated to stdout/stderr)
            drop(pipe_write);
        }

        // exec() the test command - replaces this process
        Err(cmd.exec().into())
    }
}
