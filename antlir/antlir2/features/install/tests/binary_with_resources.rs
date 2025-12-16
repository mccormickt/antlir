/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree.
 */

use anyhow::Result;

fn main() -> Result<()> {
    let resource_path = buck_resources::get("antlir/antlir2/features/install/tests/my_resource")?;
    // Read and print the resource content
    let content = std::fs::read_to_string(&resource_path)?;
    println!("Binary resource content: {}", content.trim());
    Ok(())
}
