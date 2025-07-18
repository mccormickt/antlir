/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is dual-licensed under either the MIT license found in the
 * LICENSE-MIT file in the root directory of this source tree or the Apache
 * License, Version 2.0 found in the LICENSE-APACHE file in the root directory
 * of this source tree. You may select, at your option, one of the
 * above-listed licenses.
 */

use buck2_events::dispatch::get_dispatcher;
use buck2_wrapper_common::invocation_id::TraceId;

use crate::execute::request::CommandExecutionPaths;
use crate::execute::target::CommandExecutionTarget;

pub struct ReActionIdentity<'a> {
    /// This is currently unused, but historically it has been useful to add logging in the RE
    /// client, so it's worth keeping around.
    _target: &'a dyn CommandExecutionTarget,

    /// Actions with the same action key share e.g. memory requirements learnt by RE.
    pub action_key: String,

    /// Actions with the same affinity key get scheduled on similar hosts.
    pub affinity_key: String,

    /// Details about the action collected while uploading
    pub paths: &'a CommandExecutionPaths,

    //// Trace ID which started the execution of this action, to be added on the RE side
    pub trace_id: TraceId,
}

impl<'a> ReActionIdentity<'a> {
    pub fn new(
        target: &'a dyn CommandExecutionTarget,
        executor_action_key: Option<&str>,
        paths: &'a CommandExecutionPaths,
    ) -> Self {
        let mut action_key = target.re_action_key();
        if let Some(executor_action_key) = executor_action_key {
            action_key = format!("{executor_action_key} {action_key}");
        }

        let trace_id = get_dispatcher().trace_id().to_owned();

        Self {
            _target: target,
            action_key,
            affinity_key: target.re_affinity_key(),
            paths,
            trace_id,
        }
    }
}
