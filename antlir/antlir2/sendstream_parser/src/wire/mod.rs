/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use futures::StreamExt;
use tokio::io::AsyncRead;
use tokio_util::codec::FramedRead;

pub(crate) mod cmd;
mod framed;
mod nombytes;
mod tlv;
pub use nombytes::NomBytes;

#[derive(Debug)]
pub enum ParserControl {
    KeepGoing,
    Enough,
}

/// Parse an async source of bytes, expecting to find it to contain one or more sendstreams.
/// Because the parsed commands reference data owned by the source, we do not collect the commands.
/// Instead, we allow the caller to process them via `f`, which can instruct the processing to
/// continue or shut down gracefully via the returned `ParserControl`.
///
/// Each sendstream is expected to (1) start with a header, followed by (2) either a Subvol or
/// Snapshot command, followed by (3) 0 or more additional commands, terminated by (4) an End
/// command. Note that we don't validate #2 here, but we do expect #1 and #4.
///
/// Returns number of commands parsed.
///
/// See https://btrfs.readthedocs.io/en/latest/dev/dev-send-stream.html for reference.
pub async fn parse<R, F>(reader: R, mut f: F) -> crate::Result<u128>
where
    R: AsyncRead + Unpin + Send,
    F: FnMut(&crate::Command) -> ParserControl + Send,
{
    let mut reader = FramedRead::new(reader, framed::SendstreamDecoder);
    let mut command_count = 0;
    while let Some(item_res) = reader.next().await {
        match item_res {
            Ok(framed::Item::Command(command)) => {
                command_count += 1;
                if let ParserControl::Enough = f(&command) {
                    // caller got what they needed, no need to continue parsing
                    break;
                }
            }
            Ok(framed::Item::SendstreamStart(_)) => {}
            Err(e) => {
                return Err(e);
            }
        }
    }
    Ok(command_count)
}
