#!/bin/sh
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
#
# End-to-end demonstration for the vendored Wolfi apk snapshot: compile
# and run a small Rust program *inside the layer under test*, using only
# the Rust + C toolchain that `:rust-container--layer` installed offline
# from `:os`.
#
# This exercises the whole path at once — the minimal vendored APKINDEX,
# `apk add --allow-untrusted`, and the rust + gcc/binutils packages — by
# doing the one thing a Rust container exists to do: build a binary.

set -eu

echo "== toolchain (installed offline from the wolfi snapshot) =="
rustc --version
cc --version 2>/dev/null | head -1 || echo "cc: (version banner unavailable)"

src=/tmp/demo.rs
bin=/tmp/demo

cat > "$src" <<'RUST'
//! A small Rust application, built offline from the vendored Wolfi apk
//! snapshot, to demonstrate a working hermetic Rust toolchain.

/// Sieve of Eratosthenes over the half-open range `[0, limit)`.
struct Sieve {
    limit: usize,
}

impl Sieve {
    fn new(limit: usize) -> Self {
        Sieve { limit }
    }

    /// Every prime strictly below `self.limit`.
    fn primes(&self) -> Vec<usize> {
        let mut is_prime = vec![true; self.limit];
        for slot in is_prime.iter_mut().take(self.limit.min(2)) {
            *slot = false; // 0 and 1 are not prime
        }
        let mut n = 2;
        while n * n < self.limit {
            if is_prime[n] {
                let mut m = n * n;
                while m < self.limit {
                    is_prime[m] = false;
                    m += n;
                }
            }
            n += 1;
        }
        (0..self.limit).filter(|&i| is_prime[i]).collect()
    }
}

fn main() {
    let primes = Sieve::new(50).primes();
    let sum: usize = primes.iter().sum();
    println!("primes below 50: {:?}", primes);
    println!("count = {}, sum = {}", primes.len(), sum);

    assert_eq!(primes.len(), 15);
    assert_eq!(sum, 328);
    assert_eq!(primes.last(), Some(&47));

    println!("DEMO-OK: rust built and ran offline from the wolfi snapshot");
}
RUST

echo
echo "== compiling $src with the vendored rustc =="
rustc -O --edition 2021 "$src" -o "$bin"

echo "== running the compiled binary =="
output="$("$bin")"
echo "$output"

if ! echo "$output" | grep -q "DEMO-OK"; then
    echo "FAIL: the demo binary did not print its success marker" >&2
    exit 1
fi

echo
echo "ok: the wolfi-snapshot rust toolchain compiled and ran a program offline"
