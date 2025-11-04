/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use std::ops::Deref;

use bytes::Bytes;
use bytes::BytesMut;

#[derive(Debug, Clone)]
pub struct NomBytes(Bytes);

impl From<Bytes> for NomBytes {
    fn from(bytes: Bytes) -> Self {
        Self(bytes)
    }
}

impl From<BytesMut> for NomBytes {
    fn from(bytes: BytesMut) -> Self {
        Self(bytes.freeze())
    }
}

impl From<NomBytes> for Bytes {
    fn from(bytes: NomBytes) -> Self {
        bytes.0
    }
}

impl From<NomBytes> for BytesMut {
    fn from(bytes: NomBytes) -> Self {
        bytes.0.into()
    }
}

impl Deref for NomBytes {
    type Target = [u8];

    fn deref(&self) -> &[u8] {
        self.0.deref()
    }
}

impl<const L: usize> TryFrom<NomBytes> for [u8; L] {
    type Error = <Self as TryFrom<&'static [u8]>>::Error;

    fn try_from(value: NomBytes) -> Result<Self, Self::Error> {
        value.0.as_ref().try_into()
    }
}

impl nom::Input for NomBytes {
    type Item = u8;
    type Iter = bytes::buf::IntoIter<Bytes>;
    type IterIndices = std::iter::Enumerate<bytes::buf::IntoIter<Bytes>>;

    fn input_len(&self) -> usize {
        self.0.len()
    }

    fn take(&self, index: usize) -> Self {
        self.0.slice(0..index).into()
    }

    fn take_from(&self, index: usize) -> Self {
        self.0.slice(index..).into()
    }

    fn take_split(&self, index: usize) -> (Self, Self) {
        let mut right = self.0.clone();
        let left = right.split_to(index);
        (left.into(), right.into())
    }

    fn position<P>(&self, predicate: P) -> Option<usize>
    where
        P: Fn(Self::Item) -> bool,
    {
        self.0.iter().position(|b| predicate(*b))
    }

    fn iter_elements(&self) -> Self::Iter {
        self.0.clone().into_iter()
    }

    fn iter_indices(&self) -> Self::IterIndices {
        self.0.clone().into_iter().enumerate()
    }

    fn slice_index(&self, count: usize) -> Result<usize, nom::Needed> {
        if self.0.len() >= count {
            Ok(count)
        } else {
            Err(nom::Needed::new(count - self.0.len()))
        }
    }
}

impl<'a> nom::Compare<&'a [u8]> for NomBytes {
    #[inline(always)]
    fn compare(&self, t: &'a [u8]) -> nom::CompareResult {
        self.0.as_ref().compare(t)
    }

    #[inline(always)]
    fn compare_no_case(&self, t: &'a [u8]) -> nom::CompareResult {
        self.0.as_ref().compare_no_case(t)
    }
}
