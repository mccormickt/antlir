#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import json
import os
import subprocess
import tempfile
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from antlir.antlir2.features.apk import driver


class TestResolve(unittest.TestCase):
    def _capture_resolve(self, spec):
        """Run resolve() and capture its JSON output from stdout."""
        buf = StringIO()
        with patch("sys.stdout", buf), \
             patch("antlir.antlir2.features.apk.driver.setup_apk_config"):
            driver.resolve(spec)
        return json.loads(buf.getvalue())

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_install(self, mock_run_apk, _mock_config):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "(1/3) Installing musl (1.2.4-r0)\n"
            "(2/3) Installing busybox (1.36.1-r0)\n"
            "(3/3) Installing curl (8.5.0-r0)\n"
        )
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": "curl"}},
            ],
        }
        output = self._capture_resolve(spec)

        mock_run_apk.assert_called_once_with(
            "/tmp/root",
            ["add", "--simulate", "--no-cache", "curl"],
            check=False,
        )
        self.assertEqual(
            output["transaction_resolved"]["install"],
            ["musl", "busybox", "curl"],
        )
        self.assertEqual(output["transaction_resolved"]["remove"], [])

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_remove(self, mock_run_apk, _mock_config):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "(1/2) Purging curl (8.5.0-r0)\n"
            "(2/2) Purging libcurl (8.5.0-r0)\n"
        )
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "remove", "apk": {"subject": "curl"}},
            ],
        }
        output = self._capture_resolve(spec)

        mock_run_apk.assert_called_once_with(
            "/tmp/root",
            ["del", "--simulate", "--no-cache", "curl"],
            check=False,
        )
        self.assertEqual(output["transaction_resolved"]["install"], [])
        self.assertEqual(
            output["transaction_resolved"]["remove"],
            ["curl", "libcurl"],
        )

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_mixed(self, mock_run_apk, _mock_config):
        install_result = MagicMock()
        install_result.returncode = 0
        install_result.stdout = "(1/1) Installing nginx (1.24.0-r0)\n"
        install_result.stderr = ""

        remove_result = MagicMock()
        remove_result.returncode = 0
        remove_result.stdout = "(1/1) Purging curl (8.5.0-r0)\n"
        remove_result.stderr = ""

        mock_run_apk.side_effect = [install_result, remove_result]

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": "nginx"}},
                {"action": "remove", "apk": {"subject": "curl"}},
            ],
        }
        output = self._capture_resolve(spec)

        self.assertEqual(
            output["transaction_resolved"]["install"], ["nginx"]
        )
        self.assertEqual(
            output["transaction_resolved"]["remove"], ["curl"]
        )

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_install_failure_no_packages(self, mock_run_apk, _mock_config):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "ERROR: unable to select packages:\n  nonexistent (no candidates)\n"
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": "nonexistent"}},
            ],
        }
        with self.assertRaises(RuntimeError) as cm:
            self._capture_resolve(spec)
        self.assertIn("apk simulate failed", str(cm.exception))

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_install_with_purging(self, mock_run_apk, _mock_config):
        """APK simulate can show Purging lines during install (replacements)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "(1/2) Installing new-pkg (1.0-r0)\n"
            "(2/2) Purging old-pkg (0.9-r0)\n"
        )
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": "new-pkg"}},
            ],
        }
        output = self._capture_resolve(spec)

        self.assertEqual(
            output["transaction_resolved"]["install"], ["new-pkg"]
        )
        self.assertEqual(
            output["transaction_resolved"]["remove"], ["old-pkg"]
        )

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_install_src(self, mock_run_apk, _mock_config):
        """Install from a local .apk file uses os.path.realpath."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(1/1) Installing foo (1.0-r0)\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": None, "src": "/path/to/foo.apk"}},
            ],
        }
        output = self._capture_resolve(spec)

        # src path gets resolved via os.path.realpath
        call_args = mock_run_apk.call_args[0]
        self.assertEqual(call_args[0], "/tmp/root")
        self.assertIn("--simulate", call_args[1])

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_remove_if_exists(self, mock_run_apk, _mock_config):
        """remove_if_exists is treated the same as remove in resolve."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(1/1) Purging curl (8.5.0-r0)\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "remove_if_exists", "apk": {"subject": "curl"}},
            ],
        }
        output = self._capture_resolve(spec)

        self.assertEqual(
            output["transaction_resolved"]["remove"], ["curl"]
        )


class TestRun(unittest.TestCase):
    def _capture_run(self, spec):
        """Run run() and capture its JSON-lines output from stdout."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            driver.run(spec)
        lines = buf.getvalue().strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]

    def setUp(self):
        # Use a real tempdir for install_root so the de-mocked
        # install_busybox_applets and synthesize_post_install_triggers
        # have a writable, hermetic root to inspect (both early-return
        # when the busybox-paths.d / apk db inputs aren't present).
        self._tmp = tempfile.TemporaryDirectory()
        self.install_root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_install(self, mock_run_apk, mock_setup_config, mock_setup_mounts):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "(1/2) Installing musl (1.2.4-r0)\n"
            "(2/2) Installing curl (8.5.0-r0)\n"
        )
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "install", "apk": {"subject": "curl"}},
            ],
        }
        events = self._capture_run(spec)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["package_installed"]["name"], "musl")
        self.assertEqual(events[0]["package_installed"]["version"], "1.2.4-r0")
        self.assertEqual(events[1]["package_installed"]["name"], "curl")
        self.assertEqual(events[1]["package_installed"]["version"], "8.5.0-r0")

    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_remove(self, mock_run_apk, mock_setup_config, mock_setup_mounts):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(1/1) Purging curl (8.5.0-r0)\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "remove", "apk": {"subject": "curl"}},
            ],
        }
        events = self._capture_run(spec)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["package_removed"]["name"], "curl")

    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_remove_if_exists_success(self, mock_run_apk, mock_setup_config, mock_setup_mounts):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(1/1) Purging curl (8.5.0-r0)\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "remove_if_exists", "apk": {"subject": "curl"}},
            ],
        }
        events = self._capture_run(spec)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["package_removed"]["name"], "curl")

    @patch("antlir.antlir2.features.apk.driver.bootstrap_apk_root")
    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_remove_if_exists_not_installed(self, mock_run_apk, mock_setup_config, mock_setup_mounts, _mock_bootstrap):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "package not found"
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "remove_if_exists", "apk": {"subject": "nonexistent"}},
            ],
        }
        events = self._capture_run(spec)

        # Non-zero exit emits a structured warning event
        self.assertEqual(len(events), 1)
        self.assertIn("warning", events[0])
        self.assertIn("remove_if_exists", events[0]["warning"])


def _write_db_stanza(install_root, *, name, version="1.0.0", trigger=False, provides=None):
    """Append a minimal package stanza to lib/apk/db/installed.

    Used by tests that need to exercise driver code paths gated on the
    package being recorded as installed (e.g. busybox-trigger guard,
    post-install trigger synthesis). `provides` is written the way apk
    does: a single space-separated `p:` line of `name=version` tokens.
    """
    db = os.path.join(install_root, "lib/apk/db/installed")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    lines = [f"P:{name}", f"V:{version}"]
    if provides:
        lines.append("p:" + " ".join(provides))
    if trigger:
        lines.append("t:/etc/example-trigger-dir")
    with open(db, "a") as f:
        for line in lines:
            f.write(line + "\n")
        f.write("\n")


class TestInstalledProvideNames(unittest.TestCase):
    """Resolving virtual/provided names from the installed db's `p:` line."""

    def test_package_names_and_provides_resolve(self):
        with tempfile.TemporaryDirectory() as install_root:
            # `rust` is a virtual name the `rust-1.95` package provides,
            # exactly as the wolfi rust toolchain ships it. apk records
            # every provide on one space-separated `p:` line.
            _write_db_stanza(
                install_root,
                name="rust-1.95",
                version="1.95.0-r0",
                provides=[
                    "rust=1.95.0-r0",
                    "cmd:cargo=1.95.0-r0",
                    "cmd:rustc=1.95.0-r0",
                ],
            )
            _write_db_stanza(install_root, name="busybox", version="1.37.0-r58")

            names = driver._installed_provide_names(install_root)

            # the package's own name and every provided name resolve
            self.assertEqual(
                {"rust-1.95", "rust", "cmd:cargo", "cmd:rustc", "busybox"},
                names,
            )

    def test_empty_or_missing_db(self):
        with tempfile.TemporaryDirectory() as install_root:
            self.assertEqual(driver._installed_provide_names(install_root), set())


class TestInstallBusyboxApplets(unittest.TestCase):
    """Verify the busybox post-install trigger synthesis."""

    def _setup_busybox_layer(self, install_root):
        """Lay down /bin/busybox and the apk-db record of it."""
        bb = os.path.join(install_root, "bin/busybox")
        os.makedirs(os.path.dirname(bb), exist_ok=True)
        with open(bb, "wb") as f:
            f.write(b"\x7fELF...stub busybox binary")
        os.chmod(bb, 0o755)
        _write_db_stanza(install_root, name="busybox", version="1.36.1-r17")

    def test_legacy_single_file_layout(self):
        """Wolfi/Alpine's single `/etc/busybox-paths.d/busybox` listing."""
        with tempfile.TemporaryDirectory() as install_root:
            self._setup_busybox_layer(install_root)
            paths_dir = os.path.join(install_root, "etc/busybox-paths.d")
            os.makedirs(paths_dir)
            with open(os.path.join(paths_dir, "busybox"), "w") as f:
                f.write("bin/sh\nbin/cat\nusr/bin/awk\n")

            driver.install_busybox_applets(install_root)

            for applet in ("bin/sh", "bin/cat", "usr/bin/awk"):
                link = os.path.join(install_root, applet)
                self.assertTrue(
                    os.path.islink(link),
                    f"{applet}: expected symlink, got {os.path.lexists(link)=}",
                )

    def test_per_applet_layout(self):
        """Per-applet files in `/etc/busybox-paths.d/`, no monolithic file."""
        with tempfile.TemporaryDirectory() as install_root:
            self._setup_busybox_layer(install_root)
            paths_dir = os.path.join(install_root, "etc/busybox-paths.d")
            os.makedirs(paths_dir)
            for applet, line in [
                ("sh", "bin/sh"),
                ("cat", "bin/cat"),
                ("awk", "usr/bin/awk"),
            ]:
                with open(os.path.join(paths_dir, applet), "w") as f:
                    f.write(line + "\n")

            driver.install_busybox_applets(install_root)

            for applet in ("bin/sh", "bin/cat", "usr/bin/awk"):
                link = os.path.join(install_root, applet)
                self.assertTrue(
                    os.path.islink(link),
                    f"{applet}: expected symlink",
                )

    def test_no_busybox_no_paths_dir_is_noop(self):
        """No busybox installed and no paths.d: silently return."""
        with tempfile.TemporaryDirectory() as install_root:
            driver.install_busybox_applets(install_root)
            # Nothing was created.
            self.assertEqual(os.listdir(install_root), [])

    def test_busybox_in_db_but_no_paths_dir_raises(self):
        """busybox is installed but the path-listing dir is missing."""
        with tempfile.TemporaryDirectory() as install_root:
            self._setup_busybox_layer(install_root)
            # No etc/busybox-paths.d/ at all.
            with self.assertRaises(RuntimeError) as cm:
                driver.install_busybox_applets(install_root)
            self.assertIn("busybox-paths.d", str(cm.exception))

    def test_busybox_in_db_but_paths_dir_empty_raises(self):
        with tempfile.TemporaryDirectory() as install_root:
            self._setup_busybox_layer(install_root)
            os.makedirs(os.path.join(install_root, "etc/busybox-paths.d"))
            with self.assertRaises(RuntimeError) as cm:
                driver.install_busybox_applets(install_root)
            self.assertIn("empty", str(cm.exception))

    def test_paths_dir_present_but_busybox_binary_missing_returns(self):
        """paths.d exists but /bin/busybox doesn't yet — early return."""
        with tempfile.TemporaryDirectory() as install_root:
            paths_dir = os.path.join(install_root, "etc/busybox-paths.d")
            os.makedirs(paths_dir)
            with open(os.path.join(paths_dir, "busybox"), "w") as f:
                f.write("bin/sh\n")
            # No db entry, no /bin/busybox file. Should silently return
            # rather than try to symlink to a nonexistent binary.
            driver.install_busybox_applets(install_root)
            self.assertFalse(
                os.path.exists(os.path.join(install_root, "bin/sh"))
            )

    def test_existing_symlinks_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as install_root:
            self._setup_busybox_layer(install_root)
            paths_dir = os.path.join(install_root, "etc/busybox-paths.d")
            os.makedirs(paths_dir)
            with open(os.path.join(paths_dir, "busybox"), "w") as f:
                f.write("bin/sh\n")
            # Pre-existing /bin/sh symlink with a different target.
            sh = os.path.join(install_root, "bin/sh")
            os.symlink("custom-sh", sh)

            driver.install_busybox_applets(install_root)

            self.assertEqual(os.readlink(sh), "custom-sh")


class TestSynthesizePostInstallTriggers(unittest.TestCase):
    """Verify driver re-runs known post-install triggers under --no-scripts."""

    def test_ca_certificates_bundle_concatenates_pems(self):
        with tempfile.TemporaryDirectory() as install_root:
            _write_db_stanza(install_root, name="ca-certificates-bundle")
            certs_dir = os.path.join(install_root, "etc/ssl/certs")
            os.makedirs(certs_dir)
            with open(os.path.join(certs_dir, "first.pem"), "w") as f:
                f.write("-----BEGIN FIRST-----\nFIRST\n-----END FIRST-----\n")
            with open(os.path.join(certs_dir, "second.crt"), "w") as f:
                f.write("-----BEGIN SECOND-----\nSECOND\n-----END SECOND-----\n")
            # A non-cert file in the same dir should be skipped.
            with open(os.path.join(certs_dir, "README"), "w") as f:
                f.write("informational, do not bundle\n")

            driver.synthesize_post_install_triggers(install_root)

            bundle_path = os.path.join(certs_dir, "ca-certificates.crt")
            self.assertTrue(os.path.exists(bundle_path))
            with open(bundle_path) as f:
                bundle = f.read()
            self.assertIn("FIRST", bundle)
            self.assertIn("SECOND", bundle)
            self.assertNotIn("informational", bundle)

    def test_unknown_triggered_package_emits_warning(self):
        """Triggers in the apk db that we don't synthesize → warning event."""
        with tempfile.TemporaryDirectory() as install_root:
            _write_db_stanza(
                install_root, name="some-other-pkg", trigger=True
            )

            buf = StringIO()
            with patch("sys.stdout", buf):
                driver.synthesize_post_install_triggers(install_root)
            events = [
                json.loads(line)
                for line in buf.getvalue().splitlines()
                if line.strip()
            ]
            self.assertTrue(any("warning" in e for e in events))
            warning = next(e["warning"] for e in events if "warning" in e)
            self.assertIn("some-other-pkg", warning)
            self.assertIn("trigger", warning)


class TestVerifyExtractedFiles(unittest.TestCase):
    """Verify driver catches truncated extractions via filesystem lstat."""

    def test_missing_file_is_reported(self):
        with tempfile.TemporaryDirectory() as install_root:
            db = os.path.join(install_root, "lib/apk/db/installed")
            os.makedirs(os.path.dirname(db))
            with open(db, "w") as f:
                f.write(
                    "P:foo\n"
                    "V:1.0.0-r0\n"
                    "F:usr/bin\n"
                    "R:foo\n"   # /usr/bin/foo — does not exist on disk
                    "F:usr/share\n"
                    "R:README\n"  # /usr/share/README — does not exist
                    "\n"
                )

            missing = driver._missing_extracted_files(install_root, ["foo"])
            paths = sorted(rel for _, rel in missing)
            self.assertEqual(paths, ["usr/bin/foo", "usr/share/README"])

    def test_present_files_are_not_reported(self):
        with tempfile.TemporaryDirectory() as install_root:
            os.makedirs(os.path.join(install_root, "usr/bin"))
            with open(os.path.join(install_root, "usr/bin/foo"), "w") as f:
                f.write("real binary")
            db = os.path.join(install_root, "lib/apk/db/installed")
            os.makedirs(os.path.dirname(db))
            with open(db, "w") as f:
                f.write("P:foo\nV:1.0.0-r0\nF:usr/bin\nR:foo\n\n")

            missing = driver._missing_extracted_files(install_root, ["foo"])
            self.assertEqual(missing, [])


class TestBareName(unittest.TestCase):
    def test_strips_version_pin(self):
        self.assertEqual(driver._bare_pkg_name("foo=1.0.0-r0"), "foo")
        self.assertEqual(driver._bare_pkg_name("foo>=1.0"), "foo")
        self.assertEqual(driver._bare_pkg_name("foo<1.0"), "foo")
        self.assertEqual(driver._bare_pkg_name("foo~1.0"), "foo")

    def test_strips_tag_prefix(self):
        self.assertEqual(driver._bare_pkg_name("@edge/foo"), "foo")
        self.assertEqual(driver._bare_pkg_name("@edge/foo=1.0"), "foo")

    def test_passthrough_for_bare_name(self):
        self.assertEqual(driver._bare_pkg_name("foo"), "foo")
        self.assertEqual(driver._bare_pkg_name("openssl-config"), "openssl-config")


class TestBootstrapApkRoot(unittest.TestCase):
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_existing_db_short_circuits(self, mock_run_apk):
        with tempfile.TemporaryDirectory() as install_root:
            db = os.path.join(install_root, "lib/apk/db/installed")
            os.makedirs(os.path.dirname(db))
            with open(db, "w") as f:
                f.write("P:something\nV:1.0\n\n")  # non-empty
            driver.bootstrap_apk_root(install_root)
            mock_run_apk.assert_not_called()

    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_initdb_then_wolfi(self, mock_run_apk):
        ok = MagicMock()
        ok.returncode = 0
        mock_run_apk.return_value = ok
        with tempfile.TemporaryDirectory() as install_root:
            driver.bootstrap_apk_root(install_root)
        # First call: --initdb. Second call: wolfi-baselayout.
        self.assertEqual(mock_run_apk.call_count, 2)
        self.assertIn("--initdb", mock_run_apk.call_args_list[0].args[1])
        self.assertIn("wolfi-baselayout", mock_run_apk.call_args_list[1].args[1])

    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_alpine_fallback_when_wolfi_fails(self, mock_run_apk):
        initdb_ok = MagicMock(returncode=0)
        wolfi_fail = MagicMock(returncode=1, stderr="wolfi-baselayout: no such package")
        alpine_ok = MagicMock(returncode=0)
        mock_run_apk.side_effect = [initdb_ok, wolfi_fail, alpine_ok]
        with tempfile.TemporaryDirectory() as install_root:
            driver.bootstrap_apk_root(install_root)
        self.assertEqual(mock_run_apk.call_count, 3)

    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_initdb_failure_raises(self, mock_run_apk):
        fail = MagicMock(returncode=1, stderr="cannot init db", stdout="")
        mock_run_apk.return_value = fail
        with tempfile.TemporaryDirectory() as install_root:
            with self.assertRaises(RuntimeError) as cm:
                driver.bootstrap_apk_root(install_root)
        self.assertIn("--initdb", str(cm.exception))

    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_baselayout_failure_emits_warning_does_not_raise(self, mock_run_apk):
        """If --initdb succeeds but baselayout install fails, emit a warning.

        The existing wolfi build appliance hits a known apk rename
        collision with `lib` directory tree from --initdb; the bootstrap
        is still useful (db is initialized) so we proceed with a warning
        rather than failing the build.
        """
        initdb_ok = MagicMock(returncode=0)
        baselayout_fail = MagicMock(
            returncode=1,
            stderr="failed to rename .apk.XXX to lib.",
            stdout="",
        )
        mock_run_apk.side_effect = [initdb_ok, baselayout_fail, baselayout_fail]
        with tempfile.TemporaryDirectory() as install_root:
            buf = StringIO()
            with patch("sys.stdout", buf):
                driver.bootstrap_apk_root(install_root)
            events = [
                json.loads(line)
                for line in buf.getvalue().splitlines()
                if line.strip()
            ]
        self.assertEqual(mock_run_apk.call_count, 3)
        self.assertTrue(any("warning" in e for e in events))
        warning = next(e["warning"] for e in events if "warning" in e)
        self.assertIn("baselayout", warning)
        self.assertIn("rename", warning)


class TestSetupMounts(unittest.TestCase):
    """Unit coverage for setup_mounts dir-mode and bind-failure handling."""

    @patch("antlir.antlir2.features.apk.driver.subprocess.run")
    def test_setup_mounts_creates_dirs_with_correct_modes(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        with tempfile.TemporaryDirectory() as install_root:
            driver.setup_mounts(install_root)

            for path, expected_mode in [
                ("tmp", 0o1777),
                ("proc", 0o555),
                ("dev", 0o755),
            ]:
                dst = os.path.join(install_root, path)
                self.assertTrue(os.path.isdir(dst), f"{dst} was not created")
                actual_mode = os.stat(dst).st_mode & 0o7777
                self.assertEqual(
                    actual_mode,
                    expected_mode,
                    f"{path}: mode 0o{actual_mode:o} != 0o{expected_mode:o}",
                )

            # Verify mount was invoked once per source path with --rbind.
            self.assertEqual(mock_run.call_count, 3)
            invocations = [c.args[0] for c in mock_run.call_args_list]
            for cmd, src in zip(invocations, ["/tmp", "/proc", "/dev"]):
                self.assertEqual(cmd[:3], ["mount", "--rbind", src])

    @patch("antlir.antlir2.features.apk.driver.subprocess.run")
    def test_setup_mounts_existing_dirs_keep_mode(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        with tempfile.TemporaryDirectory() as install_root:
            # Pre-create with non-default modes that setup_mounts would
            # otherwise overwrite. The function only chmods on creation,
            # so an existing directory should be left alone.
            for path in ("tmp", "proc", "dev"):
                dst = os.path.join(install_root, path)
                os.makedirs(dst)
                os.chmod(dst, 0o700)

            driver.setup_mounts(install_root)

            for path in ("tmp", "proc", "dev"):
                dst = os.path.join(install_root, path)
                actual_mode = os.stat(dst).st_mode & 0o7777
                self.assertEqual(
                    actual_mode,
                    0o700,
                    f"{path}: mode 0o{actual_mode:o} was modified; expected 0o700",
                )

    @patch("antlir.antlir2.features.apk.driver.subprocess.run")
    def test_setup_mounts_bind_failure_raises(self, mock_run):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "mount: bind not allowed in unprivileged userns"
        mock_run.return_value = mock_result

        with tempfile.TemporaryDirectory() as install_root:
            with self.assertRaises(RuntimeError) as cm:
                driver.setup_mounts(install_root)
            self.assertIn("Failed to bind-mount", str(cm.exception))
            self.assertIn("bind not allowed", str(cm.exception))


class TestSetupApkConfig(unittest.TestCase):
    def test_setup_repos_from_files(self):
        with tempfile.TemporaryDirectory() as install_root:
            with tempfile.TemporaryDirectory() as repos_dir:
                # Create a repo file
                repo_file = os.path.join(repos_dir, "main")
                with open(repo_file, "w") as f:
                    f.write("https://dl-cdn.alpinelinux.org/alpine/v3.19/main\n")
                    f.write("https://dl-cdn.alpinelinux.org/alpine/v3.19/community\n")

                spec = {"repos": repos_dir}
                driver.setup_apk_config(install_root, spec)

                repos_path = os.path.join(install_root, "etc/apk/repositories")
                self.assertTrue(os.path.exists(repos_path))
                with open(repos_path) as f:
                    content = f.read()
                self.assertIn("https://dl-cdn.alpinelinux.org/alpine/v3.19/main", content)
                self.assertIn("https://dl-cdn.alpinelinux.org/alpine/v3.19/community", content)

    def test_setup_repos_with_keys(self):
        with tempfile.TemporaryDirectory() as install_root:
            with tempfile.TemporaryDirectory() as repos_dir:
                # Create a repo file
                repo_file = os.path.join(repos_dir, "main")
                with open(repo_file, "w") as f:
                    f.write("https://example.com/repo\n")

                # Create keys directory with a key
                keys_dir = os.path.join(repos_dir, "keys")
                os.makedirs(keys_dir)
                key_file = os.path.join(keys_dir, "test.rsa.pub")
                with open(key_file, "wb") as f:
                    f.write(b"fake-key-data")

                spec = {"repos": repos_dir}
                driver.setup_apk_config(install_root, spec)

                dst_key = os.path.join(install_root, "etc/apk/keys/test.rsa.pub")
                self.assertTrue(os.path.exists(dst_key))
                with open(dst_key, "rb") as f:
                    self.assertEqual(f.read(), b"fake-key-data")

    def test_setup_no_repos(self):
        with tempfile.TemporaryDirectory() as install_root:
            spec = {}
            driver.setup_apk_config(install_root, spec)

            # Should create etc/apk but not repositories file
            apk_dir = os.path.join(install_root, "etc/apk")
            self.assertTrue(os.path.isdir(apk_dir))
            repos_path = os.path.join(apk_dir, "repositories")
            self.assertFalse(os.path.exists(repos_path))

    def test_setup_repos_local_directory(self):
        with tempfile.TemporaryDirectory() as install_root:
            with tempfile.TemporaryDirectory() as repos_dir:
                # Create a subdirectory (treated as a local repo)
                local_repo = os.path.join(repos_dir, "local-repo")
                os.makedirs(local_repo)

                spec = {"repos": repos_dir}
                driver.setup_apk_config(install_root, spec)

                repos_path = os.path.join(install_root, "etc/apk/repositories")
                self.assertTrue(os.path.exists(repos_path))
                with open(repos_path) as f:
                    content = f.read()
                self.assertIn(local_repo, content)


class TestRunApk(unittest.TestCase):
    @patch("antlir.antlir2.features.apk.driver.subprocess.run")
    def test_run_apk_success(self, mock_subprocess):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK"
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        result = driver.run_apk("/tmp/root", ["info"])

        # `--allow-untrusted` is always passed: locally-vendored repos ship
        # an unsigned APKINDEX that apk would otherwise refuse to install.
        mock_subprocess.assert_called_once_with(
            ["apk", "--root", "/tmp/root", "--no-progress", "--allow-untrusted", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf8",
            check=False,
        )
        self.assertEqual(result.returncode, 0)

    @patch("antlir.antlir2.features.apk.driver.subprocess.run")
    def test_run_apk_failure_checked(self, mock_subprocess):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "ERROR: something went wrong"
        mock_subprocess.return_value = mock_result

        with self.assertRaises(RuntimeError) as cm:
            driver.run_apk("/tmp/root", ["add", "nonexistent"])
        self.assertIn("apk command failed", str(cm.exception))
        self.assertIn("exit 1", str(cm.exception))

    @patch("antlir.antlir2.features.apk.driver.subprocess.run")
    def test_run_apk_failure_unchecked(self, mock_subprocess):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "ERROR: something"
        mock_subprocess.return_value = mock_result

        result = driver.run_apk("/tmp/root", ["del", "pkg"], check=False)
        self.assertEqual(result.returncode, 1)


class TestMain(unittest.TestCase):
    @patch("antlir.antlir2.features.apk.driver.resolve")
    def test_main_resolve(self, mock_resolve):
        spec = {"mode": "resolve", "install_root": "/tmp/root", "items": []}
        with patch("sys.stdin", StringIO(json.dumps(spec))):
            driver.main()
        mock_resolve.assert_called_once_with(spec)

    @patch("antlir.antlir2.features.apk.driver.run")
    def test_main_run(self, mock_run):
        spec = {"mode": "run", "install_root": "/tmp/root", "items": []}
        with patch("sys.stdin", StringIO(json.dumps(spec))):
            driver.main()
        mock_run.assert_called_once_with(spec)

    def test_main_unknown_mode(self):
        spec = {"mode": "invalid"}
        with patch("sys.stdin", StringIO(json.dumps(spec))):
            with self.assertRaises(SystemExit) as cm:
                driver.main()
            self.assertEqual(cm.exception.code, 1)


_FIXTURES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "apk-output",
)


def _load_apk_fixture(name):
    """Read an `apk add --simulate`-style stdout/stderr fixture pair."""
    stdout_path = os.path.join(_FIXTURES_DIR, name + ".stdout")
    stderr_path = os.path.join(_FIXTURES_DIR, name + ".stderr")
    with open(stdout_path) as f:
        stdout = f.read()
    with open(stderr_path) as f:
        stderr = f.read()
    return stdout, stderr


class TestResolveErrorPaths(unittest.TestCase):
    def _capture_resolve(self, spec):
        buf = StringIO()
        with patch("sys.stdout", buf), \
             patch("antlir.antlir2.features.apk.driver.setup_apk_config"):
            driver.resolve(spec)
        return json.loads(buf.getvalue())

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_resolve_install_partial_failure_raises(self, mock_run_apk, _mock_config):
        """Non-zero exit always raises even with partial output.

        Uses the `partial_install_missing_pkg` fixture (real-format apk
        output) instead of hand-typed strings so the parser path is
        exercised against bytes that match what apk actually emits.
        """
        stdout, stderr = _load_apk_fixture("partial_install_missing_pkg")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = stdout
        mock_result.stderr = stderr
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": "musl"}},
                {"action": "install", "apk": {"subject": "busybox"}},
            ],
        }
        with self.assertRaises(RuntimeError) as cm:
            self._capture_resolve(spec)
        self.assertIn("apk simulate failed", str(cm.exception))
        self.assertIn("Partially resolved", str(cm.exception))
        # The fixture's parser-relevant lines must round-trip through
        # the resolve code: musl should land in the partial-resolved
        # set, busybox should not.
        self.assertIn("musl", str(cm.exception))


class TestRunOutputParsing(unittest.TestCase):
    def _capture_run(self, spec):
        buf = StringIO()
        with patch("sys.stdout", buf):
            driver.run(spec)
        lines = buf.getvalue().strip().split("\n")
        return [json.loads(line) for line in lines if line.strip()]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.install_root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_install_calls_run_apk_correctly(self, mock_run_apk, mock_setup_config, mock_setup_mounts):
        """Verify the install path's run_apk invocation uses the right flags.

        bootstrap_apk_root makes a `--initdb` call before the actual
        install, so we look for the install-specific call rather than
        asserting on call index.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(1/1) Installing curl (8.5.0-r0)\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "install", "apk": {"subject": "curl"}},
            ],
        }
        self._capture_run(spec)

        install_calls = [
            c.args[1]
            for c in mock_run_apk.call_args_list
            if "curl" in c.args[1]
        ]
        self.assertEqual(len(install_calls), 1)
        install_args = install_calls[0]
        self.assertIn("add", install_args)
        self.assertIn("--no-cache", install_args)
        self.assertIn("--no-scripts", install_args)
        self.assertNotIn("--initdb", install_args)

    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_remove_parses_actual_output(self, mock_run_apk, mock_setup_config, mock_setup_mounts):
        """Remove events come from apk del output, not request list."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "(1/1) Purging curl (8.5.0-r0)\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "remove", "apk": {"subject": "curl"}},
            ],
        }
        events = self._capture_run(spec)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["package_removed"]["name"], "curl")

    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    @patch("antlir.antlir2.features.apk.driver.run_apk")
    def test_run_remove_no_output_means_no_events(self, mock_run_apk, mock_setup_config, mock_setup_mounts):
        """If apk del produces no Purging lines, no events are emitted."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "OK\n"
        mock_result.stderr = ""
        mock_run_apk.return_value = mock_result

        spec = {
            "install_root": self.install_root,
            "items": [
                {"action": "remove", "apk": {"subject": "curl"}},
            ],
        }
        events = self._capture_run(spec)
        self.assertEqual(len(events), 0)


class TestSilentItemDrops(unittest.TestCase):
    """Verify that malformed items raise instead of being silently skipped."""

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    def test_resolve_install_no_subject_or_src_raises(self, _mock_config):
        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": None, "src": None}},
            ],
        }
        with self.assertRaises(RuntimeError) as cm:
            buf = StringIO()
            with patch("sys.stdout", buf):
                driver.resolve(spec)
        self.assertIn("neither subject nor src", str(cm.exception))

    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    def test_resolve_remove_no_subject_raises(self, _mock_config):
        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "remove", "apk": {"subject": None}},
            ],
        }
        with self.assertRaises(RuntimeError) as cm:
            buf = StringIO()
            with patch("sys.stdout", buf):
                driver.resolve(spec)
        self.assertIn("no subject", str(cm.exception))

    @patch("antlir.antlir2.features.apk.driver.bootstrap_apk_root")
    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    def test_run_install_no_subject_or_src_raises(
        self, _mock_config, _mock_mounts, _mock_bootstrap
    ):
        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "install", "apk": {"subject": "", "src": ""}},
            ],
        }
        with self.assertRaises(RuntimeError) as cm:
            buf = StringIO()
            with patch("sys.stdout", buf):
                driver.run(spec)
        self.assertIn("neither subject nor src", str(cm.exception))

    @patch("antlir.antlir2.features.apk.driver.bootstrap_apk_root")
    @patch("antlir.antlir2.features.apk.driver.setup_mounts")
    @patch("antlir.antlir2.features.apk.driver.setup_apk_config")
    def test_run_remove_if_exists_no_subject_raises(
        self, _mock_config, _mock_mounts, _mock_bootstrap
    ):
        spec = {
            "install_root": "/tmp/root",
            "items": [
                {"action": "remove_if_exists", "apk": {"subject": ""}},
            ],
        }
        with self.assertRaises(RuntimeError) as cm:
            buf = StringIO()
            with patch("sys.stdout", buf):
                driver.run(spec)
        self.assertIn("no subject", str(cm.exception))


class TestSetupApkConfigJsonFile(unittest.TestCase):
    """Tests for repos-as-JSON-file support."""

    def test_repos_json_file(self):
        with tempfile.TemporaryDirectory() as install_root:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump(["https://packages.wolfi.dev/os", "https://example.com/repo"], f)
                json_path = f.name
            try:
                spec = {"repos": json_path}
                driver.setup_apk_config(install_root, spec)

                repos_file = os.path.join(install_root, "etc/apk/repositories")
                self.assertTrue(os.path.exists(repos_file))
                with open(repos_file) as f:
                    content = f.read()
                self.assertIn("https://packages.wolfi.dev/os", content)
                self.assertIn("https://example.com/repo", content)
            finally:
                os.unlink(json_path)

    def test_repos_nonexistent_path_raises(self):
        with tempfile.TemporaryDirectory() as install_root:
            spec = {"repos": "/nonexistent/path/repos.json"}
            with self.assertRaises(RuntimeError) as cm:
                driver.setup_apk_config(install_root, spec)
            self.assertIn("does not exist", str(cm.exception))

    def test_repos_dir_keys_not_added_as_repo(self):
        """The keys/ subdirectory should not appear in repositories file."""
        with tempfile.TemporaryDirectory() as install_root:
            with tempfile.TemporaryDirectory() as repos_dir:
                # Create a repo file
                repo_file = os.path.join(repos_dir, "main")
                with open(repo_file, "w") as f:
                    f.write("https://example.com/repo\n")

                # Create keys directory
                keys_dir = os.path.join(repos_dir, "keys")
                os.makedirs(keys_dir)

                spec = {"repos": repos_dir}
                driver.setup_apk_config(install_root, spec)

                repos_path = os.path.join(install_root, "etc/apk/repositories")
                with open(repos_path) as f:
                    content = f.read()
                self.assertNotIn("keys", content)
                self.assertIn("https://example.com/repo", content)


if __name__ == "__main__":
    unittest.main()
