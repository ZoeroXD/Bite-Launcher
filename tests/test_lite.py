"""Lite configuration/session tests: no real units, VT switches or power actions."""
import contextlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bite_lite as lite
import bite_runtime as rt
import bite_session as session
import configure_lite as setup
import install_preflight
import test_unified as base
from test_unified import QTest, Qt, gui


class LiteInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = SimpleNamespace(pw_name="dj", pw_uid=os.getuid(), pw_dir="/home/dj")
        self.calls = []

    def run_command(self, args):
        self.calls.append(args)
        return SimpleNamespace(returncode=3 if "is-active" in args else 0, stdout="", stderr="")

    def test_detect_desktop_lite_and_partial(self):
        self.assertEqual(setup.detect(self.root), "lite")
        folder = self.root / "usr/bin"
        folder.mkdir(parents=True)
        for name in ("pcmanfm-pi", "wf-panel-pi"):
            (folder / name).touch(mode=0o755)
        self.assertEqual(setup.detect(self.root), "desktop")
        (folder / "wf-panel-pi").unlink()
        with self.assertRaises(ValueError):
            setup.detect(self.root)

    def test_check_only_creates_nothing(self):
        setup.check(self.root, self.user, "lite", self.run_command)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(any("enable" in args for args in self.calls))

    def test_lite_unit_has_real_session_console_and_no_autologin(self):
        data = setup.payload(self.user, "lite")
        unit = data[setup.SERVICE]
        for text in ("User=dj", "PAMName=login", "TTYPath=/dev/tty7",
                     "StandardInput=tty-fail", "ExecStopPost=+/usr/bin/chvt 1",
                     "Restart=no", "WantedBy=multi-user.target",
                     "ConditionPathExists=!/home/dj/.config/bite-launcher/autostart-off",
                     ' -S "/usr/bin/python3 /opt/bitedj-launcher/bite_lite.py session"'):
            self.assertIn(text, unit)
        self.assertNotIn("autologin", unit)
        self.assertNotIn("sudo", unit)
        self.assertNotIn("NOPASSWD", "".join(data.values()))
        self.assertIn('action.lookup("verb") == "start"', data[setup.POLICY])
        self.assertIn('action.lookup("unit") == "bite-launcher-lite.service"', data[setup.POLICY])
        self.assertIn('subject.user == "dj"', data[setup.POLICY])

    def test_install_idempotent_no_live_restart_and_preserves_getty(self):
        getty = self.root / "etc/systemd/system/getty@tty1.service.d/custom.conf"
        getty.parent.mkdir(parents=True)
        getty.write_text("# preserve console settings\n")
        setup.install(self.root, self.user, "lite", self.run_command)
        first = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        setup.install(self.root, self.user, "lite", self.run_command)
        self.assertEqual(first, {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertIn(["systemctl", "enable", lite.UNIT], self.calls)
        self.assertFalse(any("--now" in args or "restart" in args for args in self.calls))
        self.assertEqual(getty.read_text(), "# preserve console settings\n")

    def test_desktop_profile_does_not_create_console_unit(self):
        setup.install(self.root, self.user, "desktop", self.run_command)
        self.assertEqual(json.loads((self.root / setup.PROFILE[1:]).read_text())["kind"], "desktop")
        self.assertFalse((self.root / setup.SERVICE[1:]).exists())
        self.assertEqual(self.calls, [])

    def test_conflicting_file_preserved(self):
        target = self.root / setup.SERVICE[1:]
        target.parent.mkdir(parents=True)
        target.write_text("custom service")
        with self.assertRaises(ValueError):
            setup.install(self.root, self.user, "lite", self.run_command)
        self.assertEqual(target.read_text(), "custom service")

    def test_modified_managed_file_and_other_user_block_changes(self):
        setup.install(self.root, self.user, "lite", self.run_command)
        other = SimpleNamespace(pw_name="other", pw_uid=os.getuid() + 1, pw_dir="/home/other")
        with self.assertRaises(ValueError):
            setup.check(self.root, other, "lite", self.run_command)
        target = self.root / setup.POLICY[1:]
        target.write_text(target.read_text() + "// custom\n")
        with self.assertRaises(ValueError):
            setup.remove(self.root, self.user, self.run_command)
        self.assertTrue(target.read_text().endswith("// custom\n"))

    def test_display_manager_and_busy_vt_are_not_replaced(self):
        dm = self.root / "etc/systemd/system/display-manager.service"
        dm.parent.mkdir(parents=True)
        dm.symlink_to("/nonexistent/custom-manager")
        with self.assertRaises(ValueError):
            setup.check(self.root, self.user, "lite", self.run_command)
        dm.unlink()
        with self.assertRaises(ValueError):
            setup.check(self.root, self.user, "lite",
                        lambda args: SimpleNamespace(returncode=0, stdout="", stderr=""))

    def test_symlink_is_rejected(self):
        target = self.root / setup.POLICY[1:]
        target.parent.mkdir(parents=True)
        target.symlink_to("/tmp/not-a-policy")
        with self.assertRaises(ValueError):
            setup.check(self.root, self.user, "lite", self.run_command)

    def test_vendor_unit_and_local_dropin_are_preserved(self):
        for relative in ("usr/lib/systemd/system/" + setup.UNIT,
                         "etc/systemd/system/" + setup.UNIT + ".d/custom.conf"):
            with self.subTest(relative=relative):
                path = self.root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# custom")
                with self.assertRaises(ValueError):
                    setup.check(self.root, self.user, "lite", self.run_command)
                self.assertEqual(path.read_text(), "# custom")
                path.unlink()

    def test_malformed_record_stops_cleanly(self):
        path = self.root / setup.STATE[1:]
        path.parent.mkdir(parents=True)
        for value in ([], {"uid": self.user.pw_uid, "version": 1, "files": []}):
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                setup.check(self.root, self.user, "lite", self.run_command)

    def test_failed_enable_rolls_back_owned_files(self):
        def fail(args):
            return SimpleNamespace(returncode=1 if "enable" in args else 3 if "is-active" in args else 0,
                                   stdout="", stderr="simulated failure")
        with self.assertRaises(RuntimeError):
            setup.install(self.root, self.user, "lite", fail)
        self.assertFalse(any(p.is_file() for p in self.root.rglob("*")))

    def test_uninstall_removes_only_owned_files(self):
        setup.install(self.root, self.user, "lite", self.run_command)
        unrelated = self.root / "etc/bite-launcher/labwc/custom"
        unrelated.write_text("keep")
        setup.remove(self.root, self.user, self.run_command)
        self.assertEqual(unrelated.read_text(), "keep")
        self.assertFalse((self.root / setup.SERVICE[1:]).exists())
        self.assertTrue((self.root / setup.PROFILE[1:]).exists())  # Removed with /opt at final uninstall.
        self.assertIn(["systemctl", "disable", "--now", lite.UNIT], self.calls)
        # Interrupted uninstall can be retried without selecting Desktop cleanup.
        setup.check(self.root, self.user, "lite", self.run_command)
        setup.remove(self.root, self.user, self.run_command)

    def test_uninstaller_does_not_request_desktop_on_lite(self):
        script = (Path(__file__).resolve().parents[1] / "uninstall.sh").read_text()
        self.assertIn('if [ "$SESSION_KIND" = desktop ] && [ -x /usr/local/bin/bite-mode ]; then', script)

    def test_removal_allowed_after_desktop_added(self):
        setup.install(self.root, self.user, "lite", self.run_command)
        dm = self.root / "etc/systemd/system/display-manager.service"
        dm.write_text("# user installed a desktop")
        self.calls.clear()
        setup.check_remove(self.root, self.user)
        self.assertEqual(self.calls, [])
        setup.remove(self.root, self.user, self.run_command)
        self.assertEqual(dm.read_text(), "# user installed a desktop")

    def test_remove_refuses_other_users_profile(self):
        setup.install(self.root, self.user, "desktop", self.run_command)
        other = SimpleNamespace(pw_uid=os.getuid() + 1)
        with self.assertRaises(ValueError):
            setup.check_remove(self.root, other)

    def test_preflight_accepts_fresh_pi_lite_without_desktop(self):
        release = self.root / "etc/os-release"
        release.parent.mkdir(parents=True)
        release.write_text("ID=debian\nVERSION_CODENAME=trixie\n")
        model = self.root / "proc/device-tree/model"
        model.parent.mkdir(parents=True)
        model.write_bytes(b"Raspberry Pi 4\0")
        report = install_preflight.inspect(root=self.root, home=self.root / "home/dj", env={},
                                           run=lambda args, **k: SimpleNamespace(returncode=0, stdout=""))
        self.assertEqual(report.conflicts, [])
        self.assertTrue(any("Lite detected" in note for note in report.notes))


class LiteModeTests(unittest.TestCase):
    def test_profile_is_per_user(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(rt, "PROFILE", Path(temp) / "profile"):
            self.assertFalse(rt.lite_mode())
            rt.PROFILE.write_text(json.dumps({"kind": "lite", "uid": os.getuid()}))
            self.assertTrue(rt.lite_mode())
            rt.PROFILE.write_text(json.dumps({"kind": "lite", "uid": os.getuid() + 1}))
            self.assertFalse(rt.lite_mode())

    def test_session_verifies_both_lifeline_and_owned_compositor(self):
        record = {"uid": os.getuid(), "pid": 101, "compositor": 202}
        with patch.object(rt, "lite_mode", return_value=True), \
                patch.object(rt, "read_runtime", return_value=record), \
                patch.object(rt, "matching", return_value={101: ["python3"]}), \
                patch.object(rt, "processes", return_value={202: ["/usr/bin/labwc", "-C", lite.CONFIG]}):
            self.assertTrue(lite.valid_session())
            record["compositor"] = 303
            self.assertFalse(lite.valid_session())

    def test_terminal_order_and_no_broad_compositor_kill(self):
        events = []
        with patch.object(lite, "valid_session", return_value=True), \
                patch.object(session, "save_mode", side_effect=lambda off: events.append(("save", off))), \
                patch.object(rt, "stop", side_effect=lambda role, **k: events.append(("stop", role))):
            lite.to_terminal()
        self.assertEqual(events, [("save", True), ("stop", "wrapper"), ("stop", "bitedj"),
                                  ("stop", "launcher"), ("stop", "backdrop"), ("stop", "lite-session")])

    def test_unknown_session_does_not_close_apps_or_change_mode(self):
        with patch.object(lite, "valid_session", return_value=False), \
                patch.object(rt, "stop") as stop, patch.object(session, "save_mode") as save:
            with self.assertRaises(RuntimeError):
                lite.to_terminal()
            stop.assert_not_called()
            save.assert_not_called()

    def test_lite_toggle_routes_to_terminal_not_desktop(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(rt, "RUN", Path(temp)), \
                patch.object(rt, "lite_mode", return_value=True), \
                patch.object(rt, "matching", return_value={10: ["launcher"]}), \
                patch.object(lite, "to_terminal") as terminal, patch.object(session, "to_desktop") as desktop:
            session.main(["toggle"])
            terminal.assert_called_once()
            desktop.assert_not_called()
            with self.assertRaises(RuntimeError):
                session.main(["desktop"])

    def test_boot_terminal_mode_does_not_start_graphics(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(rt, "OFF", Path(temp) / "off"), \
                patch.object(rt, "lite_mode", return_value=True), patch.object(lite, "start") as start:
            rt.OFF.touch()
            session.main(["boot"])
            start.assert_not_called()

    def test_console_start_releases_lock_before_service_start(self):
        locked = False
        @contextlib.contextmanager
        def lock(_name):
            nonlocal locked
            locked = True
            yield
            locked = False
        with patch.object(rt, "lite_mode", return_value=True), \
                patch.object(lite, "valid_session", return_value=False), \
                patch.object(rt, "lock", side_effect=lock), patch.object(session, "save_mode") as save, \
                patch.object(lite, "start", side_effect=lambda: self.assertFalse(locked)) as start:
            session.main(["launcher"])
            start.assert_called_once()
            save.assert_called_once_with(False)

    def test_lite_does_not_stop_unrelated_desktop_clients(self):
        with patch.object(rt, "lite_mode", return_value=True), \
                patch("pathlib.Path.is_file", return_value=True), \
                patch.object(rt, "check_display"), patch.object(rt, "request_view", return_value="token"), \
                patch.object(session, "save_mode"), patch.object(rt, "matching", return_value={1: []}), \
                patch.object(rt, "view_ready", return_value=True), patch.object(session, "stop_desktop") as stop:
            session.to_launcher()
            stop.assert_not_called()

    def test_service_start_is_narrow_and_waits_for_visible_window(self):
        with patch.object(rt, "lite_mode", return_value=True), \
                patch.object(lite, "valid_session", side_effect=[False, True]), \
                patch.object(rt, "matching", return_value={}), \
                patch.object(rt, "read_runtime", return_value={"token": "fresh"}), \
                patch.object(rt, "view_ready", return_value=True), \
                patch.object(lite.subprocess, "run",
                             return_value=SimpleNamespace(returncode=0, stderr="")) as run:
            lite.start()
            self.assertEqual(run.call_args.args[0],
                             ["systemctl", "--no-ask-password", "start", lite.UNIT])


class LiteGuiTests(unittest.TestCase):
    setUpClass = classmethod(base.GuiTests.setUpClass.__func__)
    setUp = base.GuiTests.setUp
    tearDown = base.GuiTests.tearDown

    def test_lite_menu_has_no_desktop_button(self):
        self.w.lite = True
        image = gui.QImage(1280, 800, gui.QImage.Format.Format_ARGB32)
        painter = gui.QPainter(image)
        with patch.object(gui.QPainter, "drawText") as text:
            self.w.paint_net_panel(painter)
        painter.end()
        labels = [call.args[-1] for call in text.call_args_list]
        self.assertNotIn("Switch to Pi Desktop", labels)
        self.assertIn("Shut Down", labels)
        self.assertIn("Reboot", labels)
        self.assertTrue(any("terminal" in str(label) for label in labels))

    def test_lite_mode_action_requests_terminal(self):
        self.switch.stop()
        self.w.lite = True
        with patch.object(self.w, "bite_mode_cmd", return_value="/usr/local/bin/bite-mode"), \
                patch.object(rt, "spawn") as spawn:
            self.w.switch_to_desktop()
            self.assertEqual(spawn.call_args.args[0], ["/usr/local/bin/bite-mode", "terminal"])
            self.w.switching = False
        self.switch.start()

    def test_lite_shutdown_requires_confirmation(self):
        self.w.lite = True
        self.w.net_open = True
        with patch.object(gui.QProcess, "start") as start:
            QTest.mouseClick(self.w, Qt.MouseButton.LeftButton,
                            pos=self.w.power_button_rect("shutdown").center().toPoint())
            self.assertEqual(self.w.power_action, "shutdown")
            start.assert_not_called()
            self.w.power_action = None

    def test_lite_menu_fits_landscape_and_portrait(self):
        self.w.lite = True
        for width, height in ((1280, 800), (800, 480), (480, 800)):
            self.w.resize(width, height)
            self.assertLess(self.w.power_button_rect("shutdown").bottom(),
                            self.w.power_button_rect("reboot").top())
            self.assertTrue(self.w.rect().contains(self.w.net_panel_rect().toAlignedRect()))
