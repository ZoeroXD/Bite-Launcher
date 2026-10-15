import argparse
import importlib.util
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import bite_runtime as rt
import bite_session as session
import configure_session as integration

spec = importlib.util.spec_from_file_location("flx4", Path(__file__).resolve().parents[1] / "bitedj-hold.py")
flx4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flx4)


class MacroTests(unittest.TestCase):
    def test_each_action_threshold_once_and_rearm(self):
        for note, (duration, action) in flx4.ACTIONS.items():
            holds = flx4.Holds()
            holds.event(0, note, True)
            self.assertEqual(holds.tick(0), [])
            holds.event(1, note, True)
            self.assertEqual(holds.tick(1), [])
            self.assertEqual(holds.tick(duration + 0.99), [])
            self.assertEqual(holds.tick(duration + 1), [action])
            self.assertEqual(holds.tick(duration + 100), [])
            holds.event(0, note, False)
            holds.event(0, note, True)
            self.assertEqual(holds.tick(200), [])
            self.assertEqual(holds.tick(200 + duration), [action])

    def test_early_release_either_deck_cancels(self):
        for channel in (0, 1):
            holds = flx4.Holds()
            holds.event(0, 77, True)
            holds.event(1, 77, True)
            holds.tick(0)
            holds.event(channel, 77, False)
            self.assertEqual(holds.tick(9), [])

    def test_unrelated_notes_do_nothing(self):
        holds = flx4.Holds()
        holds.event(2, 77, True)
        holds.event(0, 42, True)
        self.assertFalse(holds.held)
        self.assertEqual(holds.tick(100), [])


class RuntimeTests(unittest.TestCase):
    def test_stop_only_targets_owned_matching_test_process(self):
        with tempfile.TemporaryDirectory() as folder:
            target_path = str(Path(folder) / "mixxx-test")
            target = subprocess.Popen([target_path, "-c", "import time; time.sleep(30)"], executable=sys.executable)
            unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            try:
                with patch.object(rt, "app_path", return_value=target_path):
                    self.assertIn(target.pid, rt.matching("bitedj"))
                    rt.stop("bitedj", timeout=1)
                self.assertIsNotNone(target.poll())
                self.assertIsNone(unrelated.poll())
            finally:
                for proc in (target, unrelated):
                    if proc.poll() is None:
                        proc.terminate()
                    proc.wait()

    def test_narrow_process_identity(self):
        self.assertTrue(rt.role(["/usr/bin/python3", rt.APP], "launcher"))
        self.assertFalse(rt.role(["bash", "-c", f"cat {rt.APP}"], "launcher"))
        self.assertFalse(rt.role(["nano", rt.APP], "launcher"))
        self.assertTrue(rt.role(["python3", rt.SESSION, "wrapper"], "wrapper"))
        self.assertFalse(rt.role(["python3", rt.SESSION, "toggle"], "wrapper"))
        self.assertTrue(rt.role(["swaybg", "-c", "#000000"], "backdrop"))
        self.assertFalse(rt.role(["swaybg", "-m", "fill", "-i", "wallpaper"], "backdrop"))

    def test_lock_prevents_duplicate(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)):
            with rt.lock("unit"):
                with self.assertRaises(RuntimeError):
                    with rt.lock("unit"):
                        pass
            with rt.lock("unit"):
                pass

    def test_settings_quoted_and_unquoted_clock(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "conf"
            file.write_text('BITEDJ_CLOCK_FORMAT="%-I:%M %p" # comment\nBITEDJ_FPS=30\n')
            with patch.dict(os.environ, {"DJRIG_CONF": str(file)}):
                self.assertEqual(rt.config()["BITEDJ_CLOCK_FORMAT"], "%-I:%M %p")
                self.assertEqual(rt.config()["BITEDJ_FPS"], "30")
                file.write_text("BITEDJ_CLOCK_FORMAT=%-I:%M %p # old format\n")
                self.assertEqual(rt.config()["BITEDJ_CLOCK_FORMAT"], "%-I:%M %p")

    def test_start_exact_command_without_forced_scaling(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)):
            with patch.object(rt, "matching", return_value={}), patch.object(rt, "app_path", return_value="/home/dj/bitedj/build/mixxx"), \
                 patch("os.access", return_value=True), patch.object(rt, "config", return_value={}), \
                 patch.object(rt, "session_env", return_value={"QT_QPA_PLATFORM": "wayland"}), patch.object(rt, "spawn") as spawn:
                rt.start_bitedj()
                self.assertEqual(spawn.call_args.args[0], ["/home/dj/bitedj/build/mixxx", "--fullScreen"])
                self.assertNotIn("QT_QPA_PLATFORM", spawn.call_args.kwargs["env"])
                self.assertNotIn("QT_SCALE_FACTOR", spawn.call_args.kwargs["env"])

    def test_start_does_not_duplicate_bitedj(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)), \
             patch.object(rt, "matching", return_value={123: ["mixxx"]}), patch.object(rt, "spawn") as spawn:
            self.assertIsNone(rt.start_bitedj())
            spawn.assert_not_called()


class ModeTests(unittest.TestCase):
    def test_stop_order_and_always_close_bitedj(self):
        events = []
        with patch.object(session, "desktop_available", return_value=True), \
             patch.object(rt, "session_env", return_value={}), \
             patch.object(session, "save_mode", side_effect=lambda x: events.append(("save", x))), \
             patch.object(rt, "stop", side_effect=lambda name, **k: events.append(("stop", name))), \
             patch.object(session, "start_desktop", side_effect=lambda: events.append(("desktop", True))):
            session.to_desktop()
        self.assertEqual(events, [("save", True), ("stop", "wrapper"), ("stop", "bitedj"),
                                  ("stop", "launcher"), ("desktop", True)])

    def test_no_desktop_no_destructive_switch(self):
        with patch.object(session, "desktop_available", return_value=False), patch.object(rt, "stop") as stop, \
             patch.object(session, "save_mode") as save:
            with self.assertRaises(RuntimeError):
                session.to_desktop()
            stop.assert_not_called()
            save.assert_not_called()

    def test_saved_mode_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            with patch.object(rt, "STATE", state), patch.object(rt, "OFF", state / "autostart-off"):
                session.save_mode(True)
                self.assertTrue(rt.OFF.exists())
                session.save_mode(False)
                self.assertFalse(rt.OFF.exists())

    def test_toggle_and_icon_distinct(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)), \
             patch.object(session, "to_launcher") as launcher, patch.object(session, "to_desktop") as desktop, \
             patch.object(rt, "matching", return_value={123: ["launcher"]}):
            session.main(["toggle"])
            desktop.assert_called_once()
            session.main(["launcher"])
            launcher.assert_called_once()
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)), \
             patch.object(session, "to_launcher") as launcher, patch.object(rt, "matching", return_value={}):
            session.main(["toggle"])
            launcher.assert_called_once()

    def test_already_running_does_not_duplicate_wrapper(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            with patch.object(rt, "RUN", run), patch.object(session, "save_mode"), \
                 patch("pathlib.Path.is_file", return_value=True), \
                 patch.object(rt, "matching", return_value={123: ["active"]}), \
                 patch.object(rt, "spawn") as spawn, patch.object(session, "stop_desktop"), \
                 patch.object(rt, "check_display"), patch.object(rt, "view_ready", return_value=True):
                session.to_launcher()
                spawn.assert_not_called()

    def test_failed_backend_does_not_change_mode_or_hide_desktop(self):
        with patch("pathlib.Path.is_file", return_value=True), \
             patch.object(rt, "check_display", side_effect=RuntimeError("plugin missing")), \
             patch.object(rt, "spawn") as spawn, patch.object(session, "save_mode") as save, \
             patch.object(session, "stop_desktop") as stop:
            with self.assertRaises(RuntimeError):
                session.to_launcher()
            spawn.assert_not_called()
            save.assert_not_called()
            stop.assert_not_called()

    def test_failed_ready_does_not_hide_desktop_and_removes_attempt_backdrop(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(rt, "RUN", Path(folder)), \
             patch("pathlib.Path.is_file", return_value=True), patch.object(rt, "check_display"), \
             patch.object(rt, "matching", return_value={}), patch.object(rt, "spawn"), \
             patch.object(session, "save_mode") as save, patch.object(session, "stop_desktop") as desktop, \
             patch.object(rt, "stop") as stop, patch.object(rt, "view_ready", return_value=False), \
             patch.object(session.time, "monotonic", side_effect=[0, 11]):
            with self.assertRaisesRegex(RuntimeError, "did not display"):
                session.to_launcher()
            desktop.assert_not_called()
            self.assertEqual([c.args[0] for c in stop.call_args_list], ["wrapper", "backdrop"])
            save.assert_called_once_with(False)  # no crash-driven switch to desktop mode

    def test_boot_respects_saved_desktop_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            off = run / "autostart-off"
            off.touch()
            with patch.object(rt, "RUN", run), patch.object(rt, "OFF", off), \
                 patch.object(session, "desktop_available", return_value=True), \
                 patch.object(session, "start_desktop") as desktop, patch.object(session, "to_launcher") as launcher:
                session.main(["boot"])
                desktop.assert_called_once()
                launcher.assert_not_called()


class InstallerTests(unittest.TestCase):
    def test_invalid_xml_leaves_autostart_unchanged(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            config = home / ".config/labwc"
            config.mkdir(parents=True)
            (config / "rc.xml").write_text("<labwc_config><broken>")
            auto = config / "autostart"
            auto.write_text("keep-this &\n")
            with self.assertRaises(ET.ParseError):
                integration.configure(home)
            self.assertEqual(auto.read_text(), "keep-this &\n")

    def test_xml_and_autostart_roundtrip(self):
        examples = [
            '<labwc_config/>',
            '<labwc_config><keyboard/></labwc_config>',
            '<openbox_config xmlns="http://openbox.org/3.4/rc"><keyboard><keybind key="C-A-d"><action name="Execute" command="old"/></keybind><keybind key="W-e"><action name="Execute" command="pcmanfm"/></keybind></keyboard></openbox_config>',
            '<labwc_config><keyboard><default/></keyboard><theme><name>keep-me</name></theme></labwc_config>',
        ]
        for example in examples:
            with self.subTest(example=example), tempfile.TemporaryDirectory() as folder:
                home = Path(folder)
                config = home / ".config/labwc"
                config.mkdir(parents=True)
                rc = config / "rc.xml"
                rc.write_text(example)
                auto = config / "autostart"
                auto.write_text("custom-command &\n/usr/local/bin/bitedj-launcher &\n$HOME/bin/bite-mode boot &\n")
                integration.configure(home)
                first = rc.read_text()
                integration.configure(home)
                self.assertEqual(first, rc.read_text())
                self.assertEqual(auto.read_text().count(integration.BOOT), 1)
                self.assertIn("custom-command", auto.read_text())
                self.assertNotIn("bitedj-launcher &", auto.read_text())
                self.assertEqual(rc.read_text().count(integration.COMMAND), 1)
                with auto.open("a") as f:
                    f.write("new-user-command &\n")
                integration.configure(home, remove=True)
                self.assertNotIn(integration.COMMAND, rc.read_text())
                self.assertIn("new-user-command", auto.read_text())
                ET.fromstring(rc.read_text())
                if 'command="old"' in example:
                    self.assertIn('command="old"', rc.read_text())

    def test_saved_mode_untouched_by_install(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            off = home / ".config/bite-launcher/autostart-off"
            off.parent.mkdir(parents=True)
            off.touch()
            integration.configure(home, system_rc=home / "missing")
            self.assertTrue(off.exists())


from PyQt6.QtCore import QPoint, QEvent, Qt
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
import bitedj_launcher as gui


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.runtime_dir = tempfile.TemporaryDirectory()
        self.run_patch = patch.object(rt, "RUN", Path(self.runtime_dir.name))
        self.run_patch.start()
        self.net_patch = patch.object(gui.Network, "refresh")
        self.net_patch.start()
        self.pid_patch = patch.object(gui.Desktop, "bitedj_pid", return_value=None)
        self.pid_patch.start()
        args = argparse.Namespace(windowed=True, demo_controller=True, snapshot=None)
        self.w = gui.Desktop(args, {})
        self.w.resize(1280, 800)
        self.w.show()
        self.app.processEvents()
        self.point = self.w.clock_rect().center().toPoint()
        self.switch = patch.object(self.w, "switch_to_desktop")
        self.action = self.switch.start()

    def tearDown(self):
        self.switch.stop()
        for timer in (self.w.anim, self.w.poll, self.w.slow, self.w.sec, self.w.clock_hold):
            timer.stop()
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.net_patch.stop()
        self.pid_patch.stop()
        self.run_patch.stop()
        self.runtime_dir.cleanup()

    def test_tap_opens_details(self):
        QTest.mouseClick(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.assertTrue(self.w.net_open)
        self.action.assert_not_called()

    def test_hold_threshold_cancel_and_once(self):
        QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.w.clock_started = time.monotonic() - 7
        self.w.finish_clock_hold()
        self.action.assert_not_called()
        QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.assertFalse(self.w.clock_hold.isActive())
        self.assertFalse(self.w.net_open)
        self.w.finish_clock_hold()
        self.action.assert_not_called()
        QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.w.clock_started = time.monotonic() - 8.01
        self.w.finish_clock_hold()
        self.w.finish_clock_hold()
        QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.action.assert_called_once()
        self.assertFalse(self.w.net_open)

    def test_moving_out_cancels(self):
        QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        QTest.mouseMove(self.w, QPoint(100, 200))
        self.w.clock_started = time.monotonic() - 9
        self.w.finish_clock_hold()
        self.action.assert_not_called()

    def test_focus_loss_cancels(self):
        from PyQt6.QtGui import QFocusEvent
        QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.w.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
        self.w.clock_started = time.monotonic() - 9
        self.w.finish_clock_hold()
        self.action.assert_not_called()

    def test_timers_remain_stopped_after_no_controller_scan_while_running(self):
        with patch.object(self.w, "isActiveWindow", return_value=False), \
             patch.object(self.w.catalog, "match", return_value=None), \
             patch.object(gui, "connected_devices", return_value=[]), \
             patch.object(self.w, "bitedj_pid", return_value=123):
            self.w.set_state(gui.RUNNING)
            self.w.external_running = True
            self.w.args.demo_controller = False
            self.w.scan()
            self.assertEqual(self.w.state, gui.RUNNING)
            self.assertFalse(self.w.anim.isActive())

    def test_real_timer_eight_seconds(self):
        QTest.mousePress(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        QTest.qWait(100)
        self.action.assert_not_called()
        QTest.qWait(8050)
        self.action.assert_called_once()
        QTest.mouseRelease(self.w, Qt.MouseButton.LeftButton, pos=self.point)
        self.action.assert_called_once()

    def test_hidden_button_and_disconnect_recheck(self):
        self.w.args.demo_controller = False
        with patch.object(self.w.catalog, "match", return_value=None), \
             patch.object(gui, "connected_devices", return_value=[]), patch.object(rt, "start_bitedj") as start:
            # Recognized UI can go stale between pointer press and release.
            self.w.launch()
            self.assertEqual(self.w.state, gui.WAITING)
            start.assert_not_called()
            QTest.mouseClick(self.w, Qt.MouseButton.LeftButton, pos=self.w.button_rect().center().toPoint())
            QTest.keyClick(self.w, Qt.Key.Key_Return)
            start.assert_not_called()

    def test_30fps_and_running_suspends_updates(self):
        self.assertEqual(self.w.anim.interval(), 33)
        with patch.object(self.w, "isActiveWindow", return_value=False):
            self.w.set_state(gui.RUNNING)
            self.assertFalse(self.w.anim.isActive())
            self.assertFalse(self.w.slow.isActive())
            self.assertFalse(self.w.sec.isActive())
        self.w.set_state(gui.RECOGNIZED)
        self.assertTrue(self.w.anim.isActive())
        self.assertTrue(self.w.slow.isActive())

    def test_running_process_never_hides_or_blanks_launcher(self):
        self.w.fade = 1.0
        self.w.clock.elapsed = lambda: 12000
        recognized = self.w.grab().toImage()
        self.w.set_state(gui.RUNNING)
        self.w.fade = 1.0
        running = self.w.grab().toImage()
        self.assertTrue(self.w.isVisible())
        self.assertEqual(recognized, running)
        with patch.object(rt, "start_bitedj") as start:
            self.w.launch()
            start.assert_not_called()

    def test_foreground_running_launcher_continues_drawing(self):
        with patch.object(self.w, "isActiveWindow", return_value=True):
            self.w.set_state(gui.RUNNING)
            self.assertTrue(self.w.isVisible())
            self.assertTrue(self.w.anim.isActive())
            self.assertTrue(self.w.slow.isActive())

    def test_hidden_window_cannot_acknowledge_readiness(self):
        self.w.view_token = "hidden-test"
        self.w.hide()
        self.w.publish_painted_view()
        self.assertFalse(rt.read_runtime("view-ready.json"))

    def test_fresh_request_paints_and_restores_existing_hidden_window(self):
        self.w.hide()
        token = rt.request_view()
        self.w.consume_view_request()
        self.w.fade = 1.0
        self.w.update()
        QTest.qWait(100)
        self.assertTrue(self.w.isVisible())
        record = rt.read_runtime("view-ready.json")
        self.assertEqual(record.get("token"), token)
        self.assertTrue(record.get("painted"))
        self.assertEqual(record.get("pid"), os.getpid())

    def test_stale_ready_marker_does_not_pass(self):
        rt.write_runtime("view-ready.json", {"protocol": rt.VIEW_PROTOCOL, "token": "old",
                                            "pid": 123, "painted": True})
        with patch.object(rt, "matching", return_value={123: ["launcher"]}):
            self.assertFalse(rt.view_ready("new"))
            self.assertTrue(rt.view_ready("old"))

    def reference_frame(self, waiting):
        # Keep pixel-level layout/fin regression coverage against the old images.
        # Theme tests separately verify dynamic colors on the rendered controls.
        self.w.accent = gui.QColor(gui.PURPLE)
        self.w.set_controller(None if waiting else ("Pioneer DDJ-FLX4", "Pioneer DDJ-FLX4"))
        self.w.fade = 1.0
        self.w.clock.elapsed = lambda: 12000
        self.w.net.wifi_iface = "wlan0"
        self.w.net.wifi_up = True
        self.w.net.wifi_signal = 78
        self.w.net.ssid = "StudioNet"
        self.w.net.eth_iface = "eth0"
        self.w.net.eth_up = False
        self.w.net.wifi_ip = "192.168.1.42"
        with patch.object(gui.time, "strftime", return_value="09:41 AM"):
            return self.w.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)

    def test_full_recognized_screen_matches_original_zip(self):
        reference = QImage(str(Path(__file__).parent / "reference/recognized.png")).convertToFormat(QImage.Format.Format_RGB32)
        self.assertEqual(reference, self.reference_frame(False))

    def test_waiting_screen_matches_original_except_requested_helper_text(self):
        reference = QImage(str(Path(__file__).parent / "reference/waiting.png")).convertToFormat(QImage.Format.Format_RGB32)
        restored = self.reference_frame(True)
        # Original said "BiteDJ"; latest user wording says "Bite DJ.".
        self.assertEqual(reference.copy(0, 0, 1280, 434), restored.copy(0, 0, 1280, 434))
        self.assertEqual(reference.copy(0, 468, 1280, 332), restored.copy(0, 468, 1280, 332))


if __name__ == "__main__":
    unittest.main(verbosity=2)
