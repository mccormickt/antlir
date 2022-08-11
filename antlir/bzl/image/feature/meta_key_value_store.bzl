# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

load("//antlir/bzl:target_tagger.bzl", "new_target_tagger", "target_tagger_to_feature")
load(":meta_key_value_store.shape.bzl", "meta_key_value_store_item_t")

def feature_meta_store(key, value, require_key = None):
    """
  `feature.meta_store("key", "value")` writes the key value pair into
  the META_KEY_VALUE_STORE_FILE in the image. This can be read later. It is enforced that
  for every unique key there can be only one corresponding value set.

  The arguments `key` and `value` are mandatory; `provides`, and `require` are optional.

  The argument `key` is the key to be written, and `value` is the value written.

  The argument `require_key` defines a requirement to be satisfied before the current value
  and is used for ordering individual key value pairs. For example, we might want to store
  a list of pre_run_commands, which must be run in a specific order. Since Antlir features
  are unordered, we need provides/requires semantics to order the individual key/value metadata
  pairs that form this list. Note that we can't just pass the array all in as a single item
  because child layers might want to install their own pre-run-commands.
    """

    item = meta_key_value_store_item_t(
        key = key,
        value = value,
        require_key = require_key,
    )
    return target_tagger_to_feature(
        new_target_tagger(),
        items = struct(meta_key_value_store = [item]),
    )
