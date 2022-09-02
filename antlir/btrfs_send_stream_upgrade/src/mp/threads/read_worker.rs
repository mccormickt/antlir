/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::thread;
use std::thread::JoinHandle;

use crate::mp::threads::worker::Worker;
use crate::upgrade::send_stream_upgrade_context::SendStreamUpgradeContext;

pub struct ReadWorker {
    /// The name associated with the read worker
    rw_name: String,
    /// The join handle to check the status of the read worker
    rw_status: Option<JoinHandle<anyhow::Result<()>>>,
}

impl ReadWorker {
    fn read_work(_context: SendStreamUpgradeContext) -> anyhow::Result<()> {
        Ok(())
    }
}

impl Worker for ReadWorker {
    fn new(name: String, context: &mut SendStreamUpgradeContext) -> anyhow::Result<Self> {
        let sync_container = match context.ssuc_sync_container {
            Some(ref sync_container) => Some(sync_container.clone()),
            None => anyhow::bail!("Creating new read worker for context without sync container"),
        };
        let new_context = SendStreamUpgradeContext::clone_for_mp_threads(
            false,
            false,
            context.ssuc_logger.clone(),
            context.ssuc_options.clone(),
            context.get_source_version()?,
            context.get_destination_version()?,
            context.get_read_offset(),
            context.get_write_offset(),
            sync_container,
        )?;

        Ok(Self {
            rw_name: name,
            rw_status: Some(thread::spawn(move || Self::read_work(new_context))),
        })
    }
    fn get_status(&mut self) -> anyhow::Result<bool> {
        match self.rw_status {
            Some(ref handle) => {
                if !handle.is_finished() {
                    return Ok(true);
                }
            }
            None => anyhow::bail!("Failed to get status handle in read worker"),
        }
        // The thread is done now
        // Remove the join handle and look it up
        let handle = match self.rw_status.take() {
            Some(handle) => handle,
            None => anyhow::bail!("Unexepcted None status handle in read worker"),
        };
        match handle.join() {
            Ok(Ok(())) => Ok(false),
            // Normal anyhow error propagation
            Ok(Err(e)) => anyhow::bail!(e),
            // Note: This can happen in case of a panic
            // Just do our best here...
            Err(e) => anyhow::bail!("Thread {} paniced because {:?}", self.rw_name, e),
        }
    }
}
