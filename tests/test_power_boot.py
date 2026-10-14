"""V3.2 regressions: all power requests are mocked; never reboot this machine."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_unified as base
from test_unified import gui, rt, session, QTest, Qt
import configure_boot as boot
import configure_session as integration


class PowerMenuTests(unittest.TestCase):
    setUpClass = classmethod(base.GuiTests.setUpClass.__func__)
    setUp = base.GuiTests.setUp

    def tearDown(self):
        self.w.power_timer.stop()
        base.GuiTests.tearDown(self)

    def click(self, action):
        QTest.mouseClick(self.w, Qt.MouseButton.LeftButton,
                        pos=self.w.power_button_rect(action).center().toPoint())

    def test_order_and_fit_at_touchscreen_sizes(self):
        for width, height in ((1280, 800), (800, 480), (480, 800)):
            self.w.resize(width, height)
            self.assertLess(self.w.switch_button_rect().bottom(),
                            self.w.power_button_rect("shutdown").top())
            self.assertLess(self.w.power_button_rect("shutdown").bottom(),
                            self.w.power_button_rect("reboot").top())
            self.assertTrue(self.w.rect().contains(self.w.net_panel_rect().toAlignedRect()))

    def test_each_power_action_requires_confirmation_and_uses_exact_command(self):
        for action, command in (("shutdown", "poweroff"), ("reboot", "reboot")):
            with self.subTest(action=action), patch.object(gui.QProcess, "start") as start:
                self.w.power_action = None
                self.w.power_busy = False
                self.w.net_open = True
                self.click(action)
                self.assertEqual(self.w.power_action, action)
                start.assert_not_called()
                self.click("confirm")
                start.assert_called_once_with("/usr/bin/systemctl",
                                              ["--no-ask-password", "--no-block", command])
                self.click("confirm")
                self.assertEqual(start.call_count, 1)
                self.w.power_timer.stop()

    def test_cancel_and_escape_do_not_execute_anything(self):
        with patch.object(gui.QProcess, "start") as start:
            for action in ("shutdown", "reboot"):
                self.w.net_open = True
                self.click(action)
                self.click("cancel")
                self.assertIsNone(self.w.power_action)
                self.click(action)
                QTest.keyClick(self.w, Qt.Key.Key_Escape)
                self.assertIsNone(self.w.power_action)
            start.assert_not_called()

    def test_drag_off_cancels_and_closed_menu_is_not_armed(self):
        with patch.object(gui.QProcess, "start") as start:
            self.w.net_open = True
            pos = self.w.power_button_rect("shutdown").center().toPoint()
            QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=pos)
            QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=gui.QPointF(10, 100).toPoint())
            self.assertIsNone(self.w.power_action)
            self.click("shutdown")
            QTest.mouseClick(self.w, Qt.MouseButton.LeftButton, pos=self.point)
            self.assertFalse(self.w.net_open)
            self.assertIsNone(self.w.power_action)
            start.assert_not_called()

    def test_refused_request_keeps_launcher_and_saved_mode(self):
        with patch.object(gui.QProcess, "start"), \
             patch.object(gui.QProcess, "readAllStandardError", return_value=b"Access denied"), \
             patch.object(rt, "stop") as stop, \
             patch.object(session, "save_mode") as mode:
            self.w.request_power("reboot")
            self.w.confirm_power()
            self.w.power_finished(1, gui.QProcess.ExitStatus.NormalExit)
            self.assertFalse(self.w.power_busy)
            self.assertTrue(self.w.power_error)
            self.assertTrue(self.w.isVisible())
            stop.assert_not_called()
            mode.assert_not_called()

    def test_unarmed_confirmation_does_nothing(self):
        with patch.object(gui.QProcess, "start") as start:
            self.w.confirm_power()
            start.assert_not_called()
            self.assertFalse(self.w.power_busy)

    def test_accepted_request_keeps_ui_until_system_closes_session(self):
        with patch.object(gui.QProcess, "start"), patch.object(rt, "stop") as stop, \
             patch.object(session, "save_mode") as mode:
            self.w.request_power("shutdown")
            self.w.confirm_power()
            self.w.power_finished(0, gui.QProcess.ExitStatus.NormalExit)
            self.assertTrue(self.w.power_busy)
            self.assertTrue(self.w.isVisible())
            self.assertFalse(self.w.power_timer.isActive())
            self.assertFalse(self.w.power_error)
            stop.assert_not_called()
            mode.assert_not_called()

    def test_menu_does_not_launch_app_behind_it(self):
        self.w.resize(480, 800)
        self.w.net_open = True
        with patch.object(self.w, "launch") as launch:
            point = self.w.net_panel_rect().center().toPoint()
            QTest.mouseClick(self.w, Qt.MouseButton.LeftButton, pos=point)
            launch.assert_not_called()


class BootGuardTests(unittest.TestCase):
    def test_guard_is_idempotent_preserves_helpers_and_can_be_removed(self):
        original = ("/usr/bin/lwrespawn /usr/bin/pcmanfm-pi &\n"
                    "/usr/bin/lwrespawn /usr/bin/wf-panel-pi &\n"
                    "/usr/bin/kanshi &\n/usr/bin/lxsession-xdg-autostart\n")
        modified = boot.guard(original)
        self.assertEqual(modified.count(boot.BEGIN), 2)
        self.assertIn("/usr/bin/kanshi &\n/usr/bin/lxsession-xdg-autostart\n", modified)
        self.assertEqual(boot.guard(modified), modified)
        self.assertEqual(boot.guard(modified, remove=True), original)
        subprocess.run(["/bin/sh", "-n"], input=modified, text=True, check=True)

    def test_actual_shell_skips_desktop_only_for_managed_launcher_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            state = home / ".config/bite-launcher"
            state.mkdir(parents=True)
            launcher = home / "bitedj-launcher"
            launcher.write_text("#!/bin/sh\nexit 0\n")
            launcher.chmod(0o755)
            panel = home / "wf-panel-pi"
            panel.write_text("#!/bin/sh\nprintf 'panel-started\\n'\n")
            panel.chmod(0o755)
            with patch.object(boot, "LAUNCHER", str(launcher)):
                script = boot.guard(f"{panel}\n")
            marker = state / "managed-boot"
            off = state / "autostart-off"
            def run():
                return subprocess.run(["/bin/sh"], input=script, env={**os.environ, "HOME": str(home)},
                                      text=True, capture_output=True, check=True).stdout
            self.assertEqual(run(), "panel-started\n")  # another/unmanaged user
            marker.touch()
            self.assertEqual(run(), "")  # launcher mode: never starts the panel
            off.touch()
            self.assertEqual(run(), "panel-started\n")  # saved desktop mode
            off.unlink()
            launcher.unlink()
            self.assertEqual(run(), "panel-started\n")  # missing app: fail open

    def test_never_guard_comments_echoes_or_unrelated_commands(self):
        for line in ("# wf-panel-pi &\n", "echo wf-panel-pi\n", "swaybg -c '#000000' &\n",
                     "wf-panel-pi & important-helper &\n", "kanshi &\n"):
            self.assertEqual(boot.guard(line), line)
        self.assertTrue(boot.desktop_command("pcmanfm --desktop --profile LXDE-pi &"))
        self.assertTrue(boot.desktop_command("swaybg -i /tmp/wallpaper.png &"))

    def test_system_backup_and_reinstall_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "autostart"
            original = "wf-panel-pi &\npcmanfm-pi &\nkanshi &\n"
            path.write_text(original)
            boot.configure_system(path)
            self.assertEqual(path.with_name("autostart.pre-bite-v32").read_text(), original)
            first = path.read_text()
            boot.configure_system(path)
            self.assertEqual(path.read_text(), first)
            boot.configure_system(path, remove=True)
            self.assertEqual(path.read_text(), original)

    def test_user_startup_guard_and_marker_removed_on_uninstall(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            auto = home / ".config/labwc/autostart"
            auto.parent.mkdir(parents=True)
            auto.write_text("wf-panel-pi &\npcmanfm-pi &\n")
            integration.configure(home, system_rc=home / "missing")
            self.assertEqual(auto.read_text().count(boot.BEGIN), 2)
            marker = home / ".config/bite-launcher/managed-boot"
            self.assertTrue(marker.exists())
            integration.configure(home, remove=True, system_rc=home / "missing")
            self.assertFalse(marker.exists())
            self.assertEqual(auto.read_text(), "wf-panel-pi &\npcmanfm-pi &\n")

    def test_failed_boot_restores_desktop_without_changing_saved_mode(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)), \
             patch.object(rt, "OFF", Path(folder) / "off"), \
             patch.object(session, "to_launcher", side_effect=RuntimeError("display failed")), \
             patch.object(session, "desktop_available", return_value=True), \
             patch.object(session, "start_desktop") as desktop, \
             patch.object(rt, "stop") as stop, patch.object(session, "save_mode") as mode:
            with self.assertRaises(RuntimeError):
                session.main(["boot"])
            self.assertEqual([c.args[0] for c in stop.call_args_list], ["wrapper", "launcher"])
            desktop.assert_called_once()
            mode.assert_not_called()
