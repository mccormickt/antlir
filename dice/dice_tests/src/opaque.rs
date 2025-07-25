/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is dual-licensed under either the MIT license found in the
 * LICENSE-MIT file in the root directory of this source tree or the Apache
 * License, Version 2.0 found in the LICENSE-APACHE file in the root directory
 * of this source tree. You may select, at your option, one of the
 * above-listed licenses.
 */

use std::sync::Arc;

use allocative::Allocative;
use async_trait::async_trait;
use buck2_futures::cancellation::CancellationContext;
use dice::DetectCycles;
use dice::Dice;
use dice::DiceComputations;
use dice::DiceData;
use dice::Key;
use dice::UserComputationData;
use dupe::Dupe;
use parking_lot::Mutex;

#[derive(Debug, PartialEq)]
enum KeyType {
    DoesNotReadOpaque,
    IsOpaque,
}

/// Key used in `compute_opaque`.
#[derive(
    Debug,
    derive_more::Display,
    Copy,
    Clone,
    Dupe,
    Eq,
    PartialEq,
    Hash,
    Allocative
)]
struct IsOpaque;
/// Key which computes the opaque value, but does not read it.
#[derive(
    Debug,
    derive_more::Display,
    Copy,
    Clone,
    Dupe,
    Eq,
    PartialEq,
    Hash,
    Allocative
)]
struct DoesNotReadOpaque;

/// Record computations, for test.
struct ComputationsTracker {
    computations: Vec<KeyType>,
}

#[async_trait]
impl Key for IsOpaque {
    type Value = Arc<String>;

    async fn compute(
        &self,
        ctx: &mut DiceComputations,
        _cancellations: &CancellationContext,
    ) -> Self::Value {
        ctx.global_data()
            .get::<Arc<Mutex<ComputationsTracker>>>()
            .unwrap()
            .lock()
            .computations
            .push(KeyType::IsOpaque);

        let number = ctx.per_transaction_data().data.get::<i32>().unwrap();

        Arc::new(format!("{number}"))
    }

    fn equality(x: &Self::Value, y: &Self::Value) -> bool {
        x == y
    }
}

#[async_trait]
impl Key for DoesNotReadOpaque {
    type Value = Arc<String>;

    async fn compute(
        &self,
        ctx: &mut DiceComputations,
        _cancellations: &CancellationContext,
    ) -> Self::Value {
        ctx.global_data()
            .get::<Arc<Mutex<ComputationsTracker>>>()
            .unwrap()
            .lock()
            .computations
            .push(KeyType::DoesNotReadOpaque);

        // Compute the key but ignore it.
        let _opaque = ctx.compute_opaque(&IsOpaque).await;

        Arc::new("Aaa".to_owned())
    }

    fn equality(x: &Self::Value, y: &Self::Value) -> bool {
        x == y
    }
}

#[tokio::test]
async fn key_does_not_read_opaque() -> anyhow::Result<()> {
    let tracker = Arc::new(Mutex::new(ComputationsTracker {
        computations: Vec::new(),
    }));

    let dice = {
        let mut builder = Dice::builder();
        builder.set(tracker.dupe());
        builder.build(DetectCycles::Enabled)
    };

    // Part 1: compute key which requests on opaque key, but does not use it.

    let mut dice_data = DiceData::new();
    dice_data.set(17);
    let mut ctx = dice.updater_with_data(UserComputationData {
        data: dice_data,
        ..Default::default()
    });

    // Both keys are computed.
    assert_eq!(
        "Aaa",
        &*ctx
            .existing_state()
            .await
            .compute(&DoesNotReadOpaque)
            .await?
    );
    assert_eq!(
        vec![KeyType::DoesNotReadOpaque, KeyType::IsOpaque],
        tracker.lock().computations
    );

    // Part 2: invalidate opaque key, and "compute" the key again.

    ctx.changed([IsOpaque])?;
    let mut ctx = ctx.commit().await;

    tracker.lock().computations.clear();

    // No keys are recomputed, because `Aaa` does not depend on `KeyWhichIsOpaque`.
    assert_eq!("Aaa", &*ctx.compute(&DoesNotReadOpaque).await?);
    assert_eq!(Vec::<KeyType>::new(), tracker.lock().computations);

    Ok(())
}
