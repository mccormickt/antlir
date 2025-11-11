/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use bytes::Bytes;
use nom::IResult;
use nom::Parser as _;

use super::NomBytes;
use crate::wire::tlv::Attr;
use crate::wire::tlv::attr_types;
use crate::wire::tlv::parse_tlv;
use crate::wire::tlv::parse_tlv_opt;
use crate::wire::tlv::parse_tlv_with_attr;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CommandHeader {
    /// Command size, excluing command header itself
    pub(crate) len: usize,
    /// Command type, Check btrfs_send_command in kernel send.h for all types
    pub(crate) ty: CommandType,
    /// CRC32 checksum, including the header, with checksum filled with 0.
    pub(crate) crc32: u32,
}

impl CommandHeader {
    pub(crate) fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, len) = nom::number::streaming::le_u32(input)?;
        let (input, ty) = CommandType::parse(input)?;
        let (input, crc32) = nom::number::streaming::le_u32(input)?;
        Ok((
            input,
            Self {
                len: len as usize,
                ty,
                crc32,
            },
        ))
    }
}

/// All of the btrfs sendstream commands. Copied from linux/fs/btrfs/send.h
#[derive(
    Debug,
    Copy,
    Clone,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    num_enum::FromPrimitive
)]
#[cfg_attr(feature = "serde", derive(::serde::Deserialize, ::serde::Serialize))]
#[repr(u16)]
pub(crate) enum CommandType {
    Unspecified = 0,
    Subvol = 1,
    Snapshot = 2,
    Mkfile = 3,
    Mkdir = 4,
    Mknod = 5,
    Mkfifo = 6,
    Mksock = 7,
    Symlink = 8,
    Rename = 9,
    Link = 10,
    Unlink = 11,
    Rmdir = 12,
    SetXattr = 13,
    RemoveXattr = 14,
    Write = 15,
    Clone = 16,
    Truncate = 17,
    Chmod = 18,
    Chown = 19,
    Utimes = 20,
    End = 21,
    UpdateExtent = 22,
    Fallocate = 23,
    Fileattr = 24,
    EncodedWrite = 25,
    EnableVerity = 26,
    #[num_enum(catch_all)]
    Unknown(u16),
}

impl CommandType {
    fn from_u16(u: u16) -> Self {
        <Self as num_enum::FromPrimitive>::from_primitive(u)
    }
}

impl CommandType {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, ty) = nom::number::streaming::le_u16(input)?;
        Ok((input, Self::from_u16(ty)))
    }
}

trait ParseCommand: Sized {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self>;
}

trait ParseCommandVersion: Sized {
    fn parse(input: NomBytes, sendstream_version: u32) -> IResult<NomBytes, Self>;
}

impl<T> ParseCommandVersion for T
where
    T: ParseCommand,
{
    fn parse(input: NomBytes, _sendstream_version: u32) -> IResult<NomBytes, Self> {
        <T as ParseCommand>::parse(input)
    }
}

impl crate::Command {
    pub(crate) fn parser(
        sendstream_version: u32,
    ) -> impl nom::Parser<NomBytes, Output = Self, Error = nom::error::Error<NomBytes>> {
        move |input: NomBytes| -> IResult<NomBytes, Self> {
            let (input, cmd) = <Self as ParseCommandVersion>::parse(input, sendstream_version)?;
            Ok((input, cmd))
        }
    }
}

macro_rules! parse_subtypes {
    ($f:ident, $($t:ident),+) => {
        fn $f(hdr: CommandHeader, cmd_data: NomBytes, sendstream_version: u32) -> IResult<NomBytes, crate::Command> {
            match hdr.ty {
                $(CommandType::$t => {
                    let (remaining, cmd) = <crate::$t as ParseCommandVersion>::parse(cmd_data, sendstream_version)?;
                    Ok((remaining, cmd.into()))
                }),+
                ty => {
                    let (remaining, cmd) = crate::Unknown::parse(cmd_data, ty)?;
                    Ok((remaining, cmd.into()))
                }
            }
        }

        #[cfg(test)]
        pub(crate) static PARSED_SUBTYPES: &[CommandType] = &[ $(CommandType::$t,)+ ];
    }
}

parse_subtypes!(
    parse_subtypes,
    Chmod,
    Chown,
    Clone,
    Link,
    Mkdir,
    Mkfifo,
    Mkfile,
    Mknod,
    Mksock,
    RemoveXattr,
    Rename,
    Rmdir,
    SetXattr,
    Snapshot,
    Subvol,
    Symlink,
    Truncate,
    Unlink,
    UpdateExtent,
    Utimes,
    Write,
    EncodedWrite,
    End
);

impl ParseCommandVersion for crate::Command {
    fn parse(input: NomBytes, sendstream_version: u32) -> IResult<NomBytes, Self> {
        let (input, hdr) = CommandHeader::parse(input)?;
        let (input, cmd_data) = nom::bytes::streaming::take(hdr.len).parse(input)?;
        let (cmd_remaining, cmd) = parse_subtypes(hdr, cmd_data, sendstream_version)?;
        assert!(
            cmd_remaining.is_empty(),
            "command data not fully consumed ({} bytes left) for {cmd:?}, parser is broken",
            cmd_remaining.len()
        );
        Ok((input, cmd))
    }
}

impl crate::Unknown {
    fn parse(_input: NomBytes, command_type: CommandType) -> IResult<NomBytes, Self> {
        Ok((
            // throw away the rest of the command bytes that we don't know what
            // to do with
            Bytes::new().into(),
            Self { command_type },
        ))
    }
}

impl ParseCommand for crate::Subvol {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, uuid) = parse_tlv(input)?;
        let (input, ctransid) = parse_tlv(input)?;
        Ok((
            input,
            Self {
                path,
                uuid,
                ctransid,
            },
        ))
    }
}

impl ParseCommand for crate::Chmod {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, mode) = parse_tlv(input)?;
        Ok((input, Self { path, mode }))
    }
}

impl ParseCommand for crate::Chown {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, uid) = parse_tlv(input)?;
        let (input, gid) = parse_tlv(input)?;
        Ok((input, Self { path, uid, gid }))
    }
}

impl ParseCommand for crate::Clone {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, dst_offset) = parse_tlv(input)?;
        let (input, len) = parse_tlv(input)?;
        let (input, dst_path) = parse_tlv(input)?;
        let (input, uuid) = parse_tlv_with_attr::<_, 16, attr_types::CloneUuid>(input)?;
        let (input, ctransid) = parse_tlv_with_attr::<_, 8, attr_types::CloneCtransid>(input)?;
        let (input, src_path) = parse_tlv_with_attr::<_, 0, attr_types::ClonePath>(input)?;
        let (input, src_offset) = parse_tlv_with_attr::<_, 8, attr_types::CloneOffset>(input)?;
        Ok((
            input,
            Self {
                src_offset,
                len,
                src_path,
                uuid,
                ctransid,
                dst_path,
                dst_offset,
            },
        ))
    }
}

impl ParseCommand for crate::Link {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, link_name) = parse_tlv(input)?;
        let (input, target) = parse_tlv(input)?;
        Ok((input, Self { target, link_name }))
    }
}

impl ParseCommand for crate::Symlink {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, link_name) = parse_tlv(input)?;
        let (input, ino) = parse_tlv(input)?;
        let (input, target) = parse_tlv(input)?;
        Ok((
            input,
            Self {
                target,
                ino,
                link_name,
            },
        ))
    }
}

impl ParseCommand for crate::Mkdir {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, ino) = parse_tlv(input)?;
        Ok((input, Self { path, ino }))
    }
}

impl ParseCommand for crate::Mkfile {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, ino) = parse_tlv(input)?;
        Ok((input, Self { path, ino }))
    }
}

impl ParseCommand for crate::Mkspecial {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, ino) = parse_tlv(input)?;
        let (input, rdev) = parse_tlv(input)?;
        let (input, mode) = parse_tlv(input)?;
        Ok((
            input,
            Self {
                path,
                ino,
                rdev,
                mode,
            },
        ))
    }
}

macro_rules! mkspecial {
    ($t:ident) => {
        impl ParseCommand for crate::$t {
            fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
                <crate::Mkspecial as ParseCommand>::parse(input).map(|(r, s)| (r, Self(s)))
            }
        }
    };
}

mkspecial!(Mknod);
mkspecial!(Mkfifo);
mkspecial!(Mksock);

impl ParseCommand for crate::RemoveXattr {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, name) = parse_tlv(input)?;
        Ok((input, Self { path, name }))
    }
}

impl ParseCommand for crate::Rename {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, from) = parse_tlv(input)?;
        let (input, to) = parse_tlv_with_attr::<_, 0, attr_types::PathTo>(input)?;
        Ok((input, Self { from, to }))
    }
}

impl ParseCommand for crate::Rmdir {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        Ok((input, Self { path }))
    }
}

impl ParseCommand for crate::SetXattr {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, name) = parse_tlv(input)?;
        let (input, data) = parse_tlv(input)?;
        Ok((input, Self { path, name, data }))
    }
}

impl ParseCommand for crate::Truncate {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, size) = parse_tlv(input)?;
        Ok((input, Self { path, size }))
    }
}

impl ParseCommand for crate::Snapshot {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, uuid) = parse_tlv(input)?;
        let (input, ctransid) = parse_tlv(input)?;
        let (input, clone_uuid) = parse_tlv_with_attr::<_, 16, attr_types::CloneUuid>(input)?;
        let (input, clone_ctransid) =
            parse_tlv_with_attr::<_, 8, attr_types::CloneCtransid>(input)?;
        Ok((
            input,
            Self {
                path,
                uuid,
                ctransid,
                clone_uuid,
                clone_ctransid,
            },
        ))
    }
}

impl ParseCommand for crate::Unlink {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        Ok((input, Self { path }))
    }
}

impl ParseCommand for crate::UpdateExtent {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, offset) = parse_tlv(input)?;
        let (input, len) = parse_tlv(input)?;
        Ok((input, Self { path, offset, len }))
    }
}

impl ParseCommandVersion for crate::Utimes {
    fn parse(input: NomBytes, sendstream_version: u32) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, atime) = parse_tlv(input)?;
        let (input, mtime) = parse_tlv(input)?;
        let (input, ctime) = parse_tlv(input)?;
        let (input, otime) = if sendstream_version >= 2 && !input.is_empty() {
            parse_tlv_opt(input)?
        } else {
            (input, None)
        };
        Ok((
            input,
            Self {
                path,
                atime,
                mtime,
                ctime,
                otime,
            },
        ))
    }
}

impl ParseCommandVersion for crate::Write {
    fn parse(input: NomBytes, sendstream_version: u32) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, offset) = parse_tlv(input)?;
        if sendstream_version >= 2 {
            let (input, _) = nom::bytes::streaming::tag(Attr::Data.tag().as_slice())(input)?;
            let data = crate::Data(input.into());
            Ok((Bytes::new().into(), Self { path, offset, data }))
        } else {
            let (input, data) = parse_tlv(input)?;
            Ok((input, Self { path, offset, data }))
        }
    }
}

impl ParseCommand for crate::EncodedWrite {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        let (input, path) = parse_tlv(input)?;
        let (input, offset) = parse_tlv(input)?;
        let (input, unencoded_file_len) = parse_tlv(input)?;
        let (input, unencoded_len) = parse_tlv(input)?;
        let (input, unencoded_offset) = parse_tlv(input)?;
        let (input, compression) = parse_tlv(input)?;
        let (input, encryption) = if !input.is_empty() {
            parse_tlv_opt(input)?
        } else {
            (input, None)
        };
        // the data is the rest of the commmand
        let (input, _) = nom::bytes::streaming::tag(Attr::Data.tag().as_slice())(input)?;
        let data = crate::Data(input.into());
        Ok((
            Bytes::new().into(),
            Self {
                path,
                offset,
                unencoded_file_len,
                unencoded_len,
                unencoded_offset,
                compression,
                encryption,
                data,
            },
        ))
    }
}

impl ParseCommand for crate::End {
    fn parse(input: NomBytes) -> IResult<NomBytes, Self> {
        Ok((input, Self))
    }
}
