/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use bytes::Buf;
use bytes::BytesMut;
use nom::IResult;
use nom::Parser;
use tokio_util::codec::Decoder;

use crate::Command;
use crate::Error;
use crate::wire::NomBytes;
pub(super) struct SendstreamDecoder;

pub(super) enum Item {
    /// Magic header that starts a sendstream - the only data here is the
    /// sendstream version
    SendstreamStart(#[allow(dead_code)] u32),
    Command(Command),
}

static MAGIC_HEADER: &[u8] = b"btrfs-stream\0";

/// Parse a chunk of bytes to see if we can extract the header expected atop each sendstream.
fn sendstream_header(input: NomBytes) -> IResult<NomBytes, u32> {
    let (remainder, (_magic, version)) = (
        nom::bytes::streaming::tag::<&[u8], NomBytes, nom::error::Error<NomBytes>>(MAGIC_HEADER),
        nom::number::streaming::le_u32,
    )
        .parse(input)?;
    Ok((remainder, version))
}

impl Decoder for SendstreamDecoder {
    type Item = Item;
    type Error = Error;

    fn decode(&mut self, src: &mut BytesMut) -> Result<Option<Self::Item>, Self::Error> {
        // TODO: make a NomBytes for BytesMut too? This copy feels bad
        let parsable: NomBytes = src.clone().into();
        let starting_len = parsable.len();
        match nom::branch::alt((
            sendstream_header.map(Item::SendstreamStart),
            Command::parse.map(Item::Command),
        ))
        .parse(parsable)
        {
            Ok((remaining, item)) => {
                src.advance(starting_len - remaining.len());
                Ok(Some(item))
            }
            Err(nom::Err::Incomplete(needed)) => {
                if let nom::Needed::Size(s) = needed {
                    src.reserve(s.into());
                }
                Ok(None)
            }
            Err(e) => Err(Error::Unparsable(e.to_string())),
        }
    }
}
