/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is dual-licensed under either the MIT license found in the
 * LICENSE-MIT file in the root directory of this source tree or the Apache
 * License, Version 2.0 found in the LICENSE-APACHE file in the root directory
 * of this source tree. You may select, at your option, one of the
 * above-listed licenses.
 */

//!
//! Interfaces for introspection of the DICE graph

use crate::Dice;
use crate::DiceImplementation;
use crate::introspection::graph::AnyKey;
use crate::introspection::graph::GraphIntrospectable;

pub mod graph;
pub(crate) mod introspect;

pub use crate::introspection::introspect::serialize_dense_graph;
pub use crate::introspection::introspect::serialize_graph;

impl Dice {
    pub fn to_introspectable(&self) -> GraphIntrospectable {
        match &self.implementation {
            DiceImplementation::Modern(_) => {
                unimplemented!("todo")
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use allocative::Allocative;
    use anyhow::Context as _;
    use async_trait::async_trait;
    use buck2_futures::cancellation::CancellationContext;
    use derive_more::Display;
    use dupe::Dupe;

    use crate::HashMap;
    use crate::api::computations::DiceComputations;
    use crate::api::cycles::DetectCycles;
    use crate::api::key::Key;
    use crate::impls::dice::DiceModern;
    use crate::introspection::graph::SerializedGraphNodesForKey;
    use crate::introspection::serialize_graph;

    #[derive(Clone, Dupe, Display, Debug, Eq, Hash, PartialEq, Allocative)]
    #[display("{:?}", self)]
    struct KeyA(usize);

    #[async_trait]
    impl Key for KeyA {
        type Value = ();

        async fn compute(
            &self,
            ctx: &mut DiceComputations,
            _cancellations: &CancellationContext,
        ) -> Self::Value {
            if self.0 > 0 {
                ctx.compute(&KeyA(self.0 - 1)).await.unwrap();
            } else {
                ctx.compute(&KeyB).await.unwrap();
            }
        }

        fn equality(_: &Self::Value, _: &Self::Value) -> bool {
            unimplemented!()
        }
    }

    #[derive(Clone, Dupe, Display, Debug, Eq, Hash, PartialEq, Allocative)]
    #[display("{:?}", self)]
    struct KeyB;

    #[async_trait]
    impl Key for KeyB {
        type Value = ();

        async fn compute(
            &self,
            _: &mut DiceComputations,
            _cancellations: &CancellationContext,
        ) -> Self::Value {
            // Noop
        }

        fn equality(_: &Self::Value, _: &Self::Value) -> bool {
            unimplemented!()
        }
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 1)]
    async fn test_serialization() -> anyhow::Result<()> {
        let dice = DiceModern::builder().build(DetectCycles::Disabled);
        let mut ctx = dice.updater().commit().await;
        ctx.compute(&KeyA(3)).await?;

        let mut nodes = Vec::new();
        let mut edges = Vec::new();
        let mut nodes_currently_running = Vec::new();

        serialize_graph(
            &dice.to_introspectable(),
            &mut nodes,
            &mut edges,
            &mut nodes_currently_running,
        )
        .unwrap();
        let nodes = String::from_utf8(nodes)?;
        let edges = String::from_utf8(edges)?;

        let mut node_map = HashMap::<String, u64>::default();
        let mut edge_list = Vec::<(u64, u64)>::new();

        for line in nodes.lines() {
            let mut it = line.trim().split('\t');
            let idx = it.next().context("No idx")?.parse()?;
            let _key_type = it.next().context("No key type")?;
            let key = it.next().context("No key")?;
            node_map.insert(key.into(), idx);
        }

        for line in edges.lines() {
            let mut it = line.trim().split('\t');
            let from = it.next().context("No idx")?.parse()?;
            let to = it.next().context("No key")?.parse()?;
            edge_list.push((from, to));
        }

        let a3 = *node_map.get("KeyA(3)").context("Missing key")?;
        let a2 = *node_map.get("KeyA(2)").context("Missing key")?;
        let a1 = *node_map.get("KeyA(1)").context("Missing key")?;
        let a0 = *node_map.get("KeyA(0)").context("Missing key")?;
        let b = *node_map.get("KeyB").context("Missing key")?;

        let mut expected_edge_list = vec![(a3, a2), (a2, a1), (a1, a0), (a0, b)];
        expected_edge_list.sort_unstable();
        edge_list.sort_unstable();
        assert_eq!(expected_edge_list, edge_list);

        // TODO(cjhopman): fix this
        // assert!(nodes_currently_running.is_empty());

        Ok(())
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 1)]
    async fn test_serialization_dense() -> anyhow::Result<()> {
        let dice = DiceModern::builder().build(DetectCycles::Disabled);
        let mut ctx = dice.updater().commit().await;
        ctx.compute(&KeyA(3)).await?;

        let node = bincode::serialize(&dice.to_introspectable())?;

        let _out: Vec<SerializedGraphNodesForKey> = bincode::deserialize(&node)?;
        Ok(())
    }
}
