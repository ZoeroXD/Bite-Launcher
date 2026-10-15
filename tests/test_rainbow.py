"""Rainbow-only firmware edits in temporary fixture folders; never real /boot."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure_rainbow as rainbow


class RainbowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.boot = self.root / "boot/firmware"
        self.boot.mkdir(parents=True)
        self.config = self.boot / "config.txt"
        self.original = b"# Original\nauto_initramfs=1\n[cm4]\notg_mode=1\n"
        self.config.write_bytes(self.original)
        self.config.chmod(0o644)
        (self.root / "var/lib").mkdir(parents=True)
        self.state = self.root / "var/lib/bite-launcher/rainbow.json"
        model = self.root / "proc/device-tree/model"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"Raspberry Pi 4 Model B\x00")

    def test_current_boot_path_requires_a_mount(self):
        self.assertEqual(rainbow.locate(self.root, lambda _: True), self.config)
        with self.assertRaises(ValueError):
            rainbow.locate(self.root, lambda _: False)

    def test_legacy_boot_path(self):
        legacy = self.root / "boot/config.txt"
        self.config.rename(legacy)
        self.assertEqual(rainbow.locate(self.root, lambda _: True), legacy)

    def test_non_pi_skips(self):
        (self.root / "proc/device-tree/model").unlink()
        self.assertIsNone(rainbow.locate(self.root, lambda _: True))

    def test_missing_pi_config_stops(self):
        self.config.unlink()
        with self.assertRaises(ValueError):
            rainbow.locate(self.root, lambda _: True)

    def test_check_creates_nothing(self):
        before = set(self.root.rglob("*"))
        planned = rainbow.plan(self.config, self.state)
        self.assertIn(b"[all]\ndisable_splash=1\n", planned[1])
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_backup_before_atomic_edit_and_no_other_boot_changes(self):
        untouched = {"cmdline.txt": b"console=tty1 root=PARTUUID=1234 rootwait\n",
                     "initramfs8": b"not a real initramfs"}
        for name, data in untouched.items():
            (self.boot / name).write_bytes(data)
        message = rainbow.apply(self.config, self.state)
        record = rainbow.load_record(self.state, self.config)
        backup = self.boot / record["backup"]
        self.assertEqual(backup.read_bytes(), self.original)
        self.assertIn(str(backup), message)
        self.assertTrue(self.config.read_bytes().startswith(self.original))
        self.assertEqual(self.config.read_text().count("disable_splash=1"), 1)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o644)
        for name, data in untouched.items():
            self.assertEqual((self.boot / name).read_bytes(), data)

    def test_reinstall_does_not_duplicate_or_replace_backup(self):
        rainbow.apply(self.config, self.state)
        previous = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertIn("already off", rainbow.apply(self.config, self.state))
        self.assertEqual(previous, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_preexisting_unconditional_off_is_not_owned_or_reverted(self):
        self.config.write_bytes(b"disable_splash = 1 # existing preference\n[pi4]\ngpu_mem=64\n")
        before = self.config.read_bytes()
        self.assertIn("preserving", rainbow.apply(self.config, self.state))
        self.assertFalse(self.state.exists())
        rainbow.restore(self.config, self.state)
        self.assertEqual(self.config.read_bytes(), before)

    def test_possible_later_overrides_receive_one_global_block(self):
        for extra in (b"[pi4]\ndisable_splash=0\n", b"include custom.txt\n",
                      b"[pi5]\ndisable_splash=1\n"):
            with self.subTest(extra=extra):
                # The pi5-only setting is not enough without a global setting.
                prefix = b"" if b"pi5" in extra else b"disable_splash=1\n"
                self.config.write_bytes(prefix + extra)
                _, after, _ = rainbow.plan(self.config, self.state)
                self.assertTrue(after.endswith(b"[all]\ndisable_splash=1\n" +
                                               rainbow.END.encode() + b"\n"))

    def test_comments_do_not_count_as_settings(self):
        self.config.write_bytes(b"# disable_splash=1\n")
        before, after, _ = rainbow.plan(self.config, self.state)
        self.assertNotEqual(before, after)

    def test_restore_exact_bytes_and_keep_backup(self):
        for original in (self.original, b"[pi4]\r\ngpu_mem=64", b"disable_splash=0\n"):
            with self.subTest(original=original):
                self.config.write_bytes(original)
                rainbow.apply(self.config, self.state)
                backup = self.boot / rainbow.load_record(self.state, self.config)["backup"]
                rainbow.restore(self.config, self.state)
                self.assertEqual(self.config.read_bytes(), original)
                self.assertFalse(self.state.exists())
                self.assertEqual(backup.read_bytes(), original)

    def test_later_user_changes_are_not_overwritten(self):
        rainbow.apply(self.config, self.state)
        self.config.write_bytes(self.config.read_bytes() + b"\ndtparam=i2c_arm=on\n")
        edited = self.config.read_bytes()
        for action in (rainbow.apply, rainbow.restore):
            with self.assertRaises(ValueError):
                action(self.config, self.state)
            self.assertEqual(self.config.read_bytes(), edited)

    def test_corrupted_backup_is_not_restored(self):
        rainbow.apply(self.config, self.state)
        record = rainbow.load_record(self.state, self.config)
        (self.boot / record["backup"]).write_bytes(b"not original")
        before = self.config.read_bytes()
        with self.assertRaises(ValueError):
            rainbow.restore(self.config, self.state)
        self.assertEqual(self.config.read_bytes(), before)

    def test_failed_write_rolls_back(self):
        real_write = rainbow.atomic_write
        failed = False

        def fail_after_first_config_replace(path, data, mode=0o600):
            nonlocal failed
            real_write(path, data, mode)
            if path == self.config and not failed:
                failed = True
                raise OSError("simulated post-replace failure")

        with patch.object(rainbow, "atomic_write", side_effect=fail_after_first_config_replace):
            with self.assertRaisesRegex(RuntimeError, "original restored"):
                rainbow.apply(self.config, self.state)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.state.exists())
        self.assertEqual(len(list(self.boot.glob("config.txt.bite-rainbow-*.bak"))), 1)

    def test_failed_backup_does_not_edit_config(self):
        with patch.object(rainbow.tempfile, "mkstemp", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                rainbow.apply(self.config, self.state)
        self.assertEqual(self.config.read_bytes(), self.original)
        self.assertFalse(self.state.exists())

    def test_concurrent_edit_during_preparation_is_preserved(self):
        real_write = rainbow.atomic_write
        edited = self.original + b"dtparam=spi=on\n"

        def edit_during_record(path, data, mode=0o600):
            real_write(path, data, mode)
            if path == self.state:
                self.config.write_bytes(edited)

        with patch.object(rainbow, "atomic_write", side_effect=edit_during_record):
            with self.assertRaisesRegex(ValueError, "left untouched"):
                rainbow.apply(self.config, self.state)
        self.assertEqual(self.config.read_bytes(), edited)
        self.assertFalse(self.state.exists())

    def test_interrupted_record_can_be_cleared_when_original_is_restored(self):
        rainbow.apply(self.config, self.state)
        self.config.write_bytes(self.original)
        self.assertIn("already present", rainbow.restore(self.config, self.state))
        self.assertFalse(self.state.exists())

    def test_symlink_config_is_rejected(self):
        other = self.boot / "other.txt"
        self.config.rename(other)
        self.config.symlink_to(other)
        with self.assertRaises(ValueError):
            rainbow.plan(self.config, self.state)
        self.assertEqual(other.read_bytes(), self.original)

    def test_unsafe_permissions_and_orphan_block_stop(self):
        self.config.chmod(0o666)
        with self.assertRaises(ValueError):
            rainbow.plan(self.config, self.state)
        self.config.chmod(0o644)
        self.config.write_text(rainbow.BEGIN + "\n[all]\ndisable_splash=1\n" + rainbow.END + "\n")
        with self.assertRaises(ValueError):
            rainbow.plan(self.config, self.state)

    def test_state_symlink_is_rejected(self):
        self.state.parent.symlink_to(self.boot, target_is_directory=True)
        with self.assertRaises(ValueError):
            rainbow.apply(self.config, self.state)
        self.assertEqual(self.config.read_bytes(), self.original)

    def test_installer_precheck_precedes_changes_and_uninstall_restores(self):
        folder = Path(__file__).resolve().parents[1]
        installer = (folder / "install.sh").read_text()
        check = "sudo /usr/bin/python3 -I -S configure_rainbow.py --check"
        self.assertLess(installer.index(check), installer.index('if [ "$CHECK_ONLY"'))
        self.assertLess(installer.index(check), installer.index("sudo apt-get"))
        self.assertIn("sudo /usr/bin/python3 -I -S /opt/bitedj-launcher/configure_rainbow.py",
                      installer)
        uninstaller = (folder / "uninstall.sh").read_text()
        self.assertLess(uninstaller.index("configure_rainbow.py --restore"),
                        uninstaller.index("sudo rm -rf /opt/bitedj-launcher"))


if __name__ == "__main__":
    unittest.main()
