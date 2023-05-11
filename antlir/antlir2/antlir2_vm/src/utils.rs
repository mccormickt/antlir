/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::process::Command;

use tracing::debug;
use tracing::trace;

/// Log the command being executed, unless it can't be decoded.
pub(crate) fn log_command(command: &mut Command) -> &mut Command {
    let program = match command.get_program().to_str() {
        Some(s) => s,
        None => {
            debug!("The command is not valid Unicode. Skip logging.");
            return command;
        }
    };
    let args: Option<Vec<&str>> = command.get_args().map(|x| x.to_str()).collect();
    match args {
        Some(args) => trace!("Executing command: {} {}", program, args.join(" ")),
        None => debug!("The command is not valid Unicode. Skip logging."),
    };
    command
}

/// A lot of qemu arguments take a node_name. The main requirement of that is to be
/// unique. Add a helper to generate such names.
#[derive(Debug, Default)]
pub(crate) struct NodeNameCounter {
    prefix: String,
    count: u32,
}

impl NodeNameCounter {
    pub fn new(prefix: &str) -> Self {
        Self {
            prefix: prefix.to_string(),
            count: 0,
        }
    }

    pub fn next(&mut self) -> String {
        let count = self.count;
        self.count += 1;
        format!("{}{}", self.prefix, count)
    }
}

#[cfg(test)]
mod test {
    use super::*;

    #[test]
    fn basic() {
        let mut test = NodeNameCounter::new("vd");
        assert_eq!(test.next(), "vd0");
        assert_eq!(test.next(), "vd1");
        assert_eq!(test.next(), "vd2");
    }
}