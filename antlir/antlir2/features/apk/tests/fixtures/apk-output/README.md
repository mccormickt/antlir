apk-output fixtures
====================

These files mirror the stdout and stderr that real `apk add --simulate`
emits for specific failure modes. Tests load them via `mock_run_apk` so
the parser path under test sees byte-equivalent input to a production
build.

Capture protocol
----------------

To regenerate or extend a fixture, run apk inside a Wolfi rootfs:

    apk --root /tmp/empty-root --no-progress add --simulate --no-cache \
        musl busybox-does-not-exist 2>stderr 1>stdout
    mv stdout fixtures/apk-output/<scenario>.stdout
    mv stderr fixtures/apk-output/<scenario>.stderr

Each `<scenario>` pair must include `.stdout` and `.stderr` files even
if one is empty, so test loaders can match them by name.

Why fixtures
------------

Hand-typed mock output drifts from real apk over time as apk's CLI
formatting changes (extra `fetch` lines, error message wording, etc.).
Storing the captures verbatim catches the drift the next time the
test suite runs against newer apk-tools.

Current scenarios
-----------------

- `partial_install_missing_pkg`: one valid package + one nonexistent;
  apk emits an `Installing` line for the resolvable one and an
  `unable to select packages` error for the missing one.
