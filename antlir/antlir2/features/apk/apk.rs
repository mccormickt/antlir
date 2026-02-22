/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::collections::BTreeSet;
use std::io::BufReader;
use std::io::BufWriter;
use std::io::Seek;
use std::ops::Deref;
use std::path::Path;
use std::path::PathBuf;
use std::process::Stdio;

use antlir2_compile::Arch;
use antlir2_compile::CompilerContext;
use antlir2_depgraph_if::Requirement;
use antlir2_depgraph_if::item::Item;
use antlir2_features::types::BuckOutSource;
use antlir2_isolate::IsolationContext;
use antlir2_isolate::unshare;
use anyhow::Context;
use anyhow::Result;
use buck_label::Label;
use json_arg::JsonFile;
use serde::Deserialize;
use serde::Serialize;
use serde::de::Error as _;
use serde_json::Deserializer;
use tempfile::TempDir;
use tracing::trace;

pub type Feature = Apk;

#[derive(
    Debug,
    Clone,
    Copy,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    Deserialize,
    Serialize
)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    Install,
    Remove,
    RemoveIfExists,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Source {
    Subject(String),
    #[serde(rename = "src")]
    Source(BuckOutSource),
}

/// Buck2's `record` always includes `null` values for unset fields, so we need
/// custom deserialization that handles multiple keys where all but one are null.
impl<'de> Deserialize<'de> for Source {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct SourceStruct {
            subject: Option<String>,
            src: Option<BuckOutSource>,
        }

        SourceStruct::deserialize(deserializer).and_then(|s| match (s.subject, s.src) {
            (Some(subj), None) => Ok(Self::Subject(subj)),
            (None, Some(source)) => Ok(Self::Source(source)),
            _ => Err(D::Error::custom(
                "exactly one of {subject, src} must be set",
            )),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Deserialize, Serialize)]
pub struct ApkItem {
    pub action: Action,
    pub apk: Source,
    pub feature_label: Label,
}

#[derive(
    Debug,
    Clone,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    Deserialize,
    Serialize,
    Default
)]
#[serde(deny_unknown_fields)]
pub struct Apk {
    pub items: Vec<ApkItem>,
    pub driver_cmd: Vec<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Plan {
    build_appliance: PathBuf,
    repos: PathBuf,
    /// True when the layer was built with at least one URL repo configured
    /// (either flavor default or pinned). Used to decide whether the apk
    /// driver's container needs network access.
    #[serde(default)]
    has_url_repos: bool,
    resolved: JsonFile<ResolvedTransaction>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct ResolvedTransaction {
    pub install: BTreeSet<String>,
    pub remove: BTreeSet<String>,
}

impl antlir2_depgraph_if::RequiresProvides for Apk {
    fn provides(&self) -> Result<Vec<Item>, String> {
        Ok(Default::default())
    }

    fn requires(&self) -> Result<Vec<Requirement>, String> {
        Ok(Default::default())
    }
}

impl antlir2_compile::CompileFeature for Apk {
    #[tracing::instrument(name = "apks", skip(self, ctx), ret, err(Debug))]
    fn compile(&self, ctx: &CompilerContext) -> antlir2_compile::Result<()> {
        let plan: Plan = ctx
            .plan("apk")
            .context("apk feature was not planned")?
            .context("while loading apk plan")?;
        run_apk_driver(
            DriverContext::Compile {
                ctx,
                build_appliance: plan.build_appliance,
                repos: plan.repos,
                has_url_repos: plan.has_url_repos,
            },
            &self.driver_cmd,
            &self.items,
            DriverMode::Run,
            Some(plan.resolved.into_inner()),
        )
        .map(|_| ())
        .map_err(antlir2_compile::Error::from)
    }
}

impl Apk {
    #[tracing::instrument(skip_all)]
    pub fn plan(&self, ctx: DriverContext) -> Result<ResolvedTransaction> {
        let mut events = run_apk_driver(
            ctx,
            &self.driver_cmd,
            &self.items,
            DriverMode::Resolve,
            None,
        )?;
        if events.len() != 1 {
            return Err(anyhow::anyhow!(
                "expected exactly one event in resolve-only mode, got {}: {:?}",
                events.len(),
                events,
            ));
        }
        let event = events.remove(0);
        if let DriverEvent::TransactionResolved { install, remove } = event {
            Ok(ResolvedTransaction { install, remove })
        } else {
            Err(anyhow::anyhow!(
                "resolve-only event should have been TransactionResolved, got: {:?}",
                event,
            ))
        }
    }
}

#[derive(Debug, Serialize)]
struct DriverSpec<'a> {
    repos: Option<&'a Path>,
    install_root: &'a Path,
    items: &'a [ApkItem],
    mode: DriverMode,
    arch: Arch,
    resolved_transaction: Option<ResolvedTransaction>,
    layer_label: Label,
}

#[derive(Debug, Copy, Clone, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum DriverMode {
    Resolve,
    Run,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
enum DriverEvent {
    TransactionResolved {
        install: BTreeSet<String>,
        remove: BTreeSet<String>,
    },
    PackageInstalled {
        name: String,
        version: String,
    },
    PackageRemoved {
        name: String,
    },
    Error(String),
    Warning(String),
}

pub enum DriverContext<'a> {
    Compile {
        ctx: &'a CompilerContext,
        build_appliance: PathBuf,
        repos: PathBuf,
        has_url_repos: bool,
    },
    Plan {
        label: Label,
        root: Option<PathBuf>,
        build_appliance: PathBuf,
        repos: PathBuf,
        has_url_repos: bool,
        target_arch: Arch,
    },
}

impl DriverContext<'_> {
    pub fn plan(
        label: Label,
        root: Option<PathBuf>,
        build_appliance: PathBuf,
        repos: PathBuf,
        has_url_repos: bool,
        target_arch: Arch,
    ) -> Self {
        Self::Plan {
            label,
            root,
            build_appliance,
            repos,
            has_url_repos,
            target_arch,
        }
    }

    fn label(&self) -> &Label {
        match self {
            Self::Plan { label, .. } => label,
            Self::Compile { ctx, .. } => ctx.label(),
        }
    }

    fn build_appliance(&self) -> &Path {
        match self {
            Self::Plan {
                build_appliance, ..
            } => build_appliance,
            Self::Compile {
                build_appliance, ..
            } => build_appliance,
        }
    }

    fn repos(&self) -> &Path {
        match self {
            Self::Plan { repos, .. } => repos,
            Self::Compile { repos, .. } => repos,
        }
    }

    fn has_url_repos(&self) -> bool {
        match self {
            Self::Plan { has_url_repos, .. } => *has_url_repos,
            Self::Compile { has_url_repos, .. } => *has_url_repos,
        }
    }

    fn target_arch(&self) -> Arch {
        match self {
            Self::Plan { target_arch, .. } => *target_arch,
            Self::Compile { ctx, .. } => ctx.target_arch(),
        }
    }

    fn root_path(&self) -> Option<&Path> {
        match self {
            Self::Plan { root, .. } => root.as_deref(),
            Self::Compile { ctx, .. } => Some(ctx.root_path()),
        }
    }

    fn is_planning(&self) -> bool {
        matches!(self, Self::Plan { .. })
    }
}

enum Root {
    Empty(TempDir),
    Root(PathBuf),
}

impl Deref for Root {
    type Target = Path;

    fn deref(&self) -> &Path {
        match self {
            Self::Empty(tmp) => tmp.path(),
            Self::Root(root) => root,
        }
    }
}

fn run_apk_driver(
    ctx: DriverContext,
    driver: &[String],
    items: &[ApkItem],
    mode: DriverMode,
    resolved_transaction: Option<ResolvedTransaction>,
) -> Result<Vec<DriverEvent>> {
    let spec = DriverSpec {
        repos: Some(ctx.repos()),
        install_root: Path::new("/__antlir2__/root"),
        items,
        mode,
        arch: ctx.target_arch(),
        resolved_transaction,
        layer_label: ctx.label().clone(),
    };

    let root = match ctx.root_path() {
        Some(r) => Root::Root(r.to_owned()),
        None => Root::Empty(TempDir::new().context("while creating empty root dir")?),
    };

    let opts = memfd::MemfdOptions::default().close_on_exec(false);
    let mfd = opts.create("input").context("while creating memfd")?;
    serde_json::to_writer(BufWriter::new(mfd.as_file()), &spec)
        .context("while serializing apk-driver input")?;
    mfd.as_file().rewind().context("while rewinding memfd for apk-driver input")?;

    let mut isol = IsolationContext::builder(ctx.build_appliance());
    isol.ephemeral(false)
        .readonly()
        .inputs((
            PathBuf::from("/__antlir2__/working_directory"),
            std::env::current_dir().context("while getting current directory for isolation")?,
        ))
        .working_directory(Path::new("/__antlir2__/working_directory"))
        .tmpfs(Path::new("/__antlir2__/apk/cache"))
        .tmpfs(Path::new("/var/log"))
        .tmpfs(Path::new("/dev"))
        .tmpfs(Path::new("/tmp"))
        .setenv(("TMPDIR", "/tmp"))
        .setenv(("PYTHONDONTWRITEBYTECODE", "1"));

    // apk fetches package indexes from URL repos (e.g. Wolfi) over HTTPS,
    // so the container needs network access in that case. When the layer
    // is configured with only local repos, leave network disabled — the
    // unshare() default of a fresh CLONE_NEWNET namespace keeps the build
    // hermetic and surfaces accidental remote fetches.
    if ctx.has_url_repos() {
        isol.enable_network(true);

        // DNS resolution requires /etc/resolv.conf inside the container.
        if Path::new("/etc/resolv.conf").exists() {
            isol.inputs((
                Path::new("/etc/resolv.conf"),
                Path::new("/etc/resolv.conf"),
            ));
        }
    }
    if ctx.is_planning() {
        isol.inputs((Path::new("/__antlir2__/root"), root.deref()))
            .tmpfs_overlay(Path::new("/__antlir2__/root"));
    } else {
        isol.outputs((Path::new("/__antlir2__/root"), root.deref()));
    }

    let isol = unshare(isol.build())?;

    let mut driver_cmd = driver.iter();

    let mut cmd = isol.command(driver_cmd.next().context("driver_cmd is empty")?)?;
    cmd.args(driver_cmd);

    let mut child = cmd
        .stdin(mfd.into_file())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .inspect_err(|e| trace!("Spawning apk-driver failed: {e}"))
        .context("while spawning apk-driver")?;

    let stderr_handle = child.stderr.take().expect("this is a pipe");
    let stderr_thread = std::thread::spawn(move || std::io::read_to_string(stderr_handle));

    let deser =
        Deserializer::from_reader(BufReader::new(child.stdout.take().expect("this is a pipe")));
    let mut events = Vec::new();
    for event in deser.into_iter::<DriverEvent>() {
        let event = event.context("while deserializing event from apk-driver")?;
        trace!("apk-driver: {event:?}");
        events.push(event);
    }
    let result = child.wait().context("while waiting for apk-driver")?;
    let stderr_output = stderr_thread
        .join()
        .map_err(|_| anyhow::anyhow!("stderr reader thread panicked"))?
        .unwrap_or_default();

    for event in &events {
        if let DriverEvent::Warning(msg) = event {
            tracing::warn!("apk-driver: {msg}");
        }
    }

    if !stderr_output.is_empty() {
        trace!("apk-driver stderr: {stderr_output}");
    }

    if !result.success() {
        Err(anyhow::anyhow!(
            "apk-driver failed with exit code {:?} while processing {} items\nstderr: {}",
            result.code(),
            items.len(),
            stderr_output,
        ))
    } else {
        let errors: Vec<_> = events
            .iter()
            .filter_map(|ev| match ev {
                DriverEvent::Error(error) => Some(error.as_str()),
                _ => None,
            })
            .collect();
        if !errors.is_empty() {
            return Err(anyhow::anyhow!(
                "there were one or more apk errors: {errors:?}"
            ));
        }
        Ok(events)
    }
}
