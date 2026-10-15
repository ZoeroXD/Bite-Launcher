"""Local theme detection, regression and startup tests; no Pi or network needed."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import theme_sync as theme
from launcher_palette import GRAY

ROOT = Path(__file__).resolve().parents[1]
# Minimal fixture matches the relevant upstream rules; not a runtime default.
QSS = """WPushButton { background-color: transparent; }
#TabControls {
  background-color: #488ab3;
}
#TabControls WPushButton[value="0"] { color: black; border: 2px solid black; }
#TabControls WPushButton[value="1"] { background-color: black; }
WPushButton[value="1"]:hover { background-color: red; }
"""


class ThemeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.file = self.home / "bitedj/res/skins/BiteDJ/style.qss"
        self.file.parent.mkdir(parents=True)
        self.file.write_text(QSS)
        self.conf = {"BITEDJ_THEME_FILE": str(self.file)}

    def detect(self, qss):
        self.file.write_text(qss)
        return theme.detect(self.conf)

    def test_current_bar_not_selected_black(self):
        result = theme.detect(self.conf)
        self.assertEqual(result.color, "#488ab3")
        self.assertEqual(result.line, 3)
        self.assertFalse(result.fallback)
        self.assertEqual(result.source, str(self.file))

    def test_color_changes_between_reads(self):
        self.assertEqual(theme.detect(self.conf).color, "#488ab3")
        self.assertEqual(self.detect(QSS.replace("#488ab3", "#d18b31")).color, "#d18b31")

    def test_black_and_near_black_use_shared_existing_gray(self):
        for value in ("black", "#000", "#000000", "rgb(16,16,16)", "#01000f"):
            with self.subTest(value=value):
                result = self.detect(QSS.replace("#488ab3", value))
                self.assertEqual(result.color, GRAY)
                self.assertTrue(result.fallback)
                self.assertIn("near-black", result.reason)
        self.assertEqual(self.detect(QSS.replace("#488ab3", "#111111")).color, "#111111")

    def test_rgb_hex_and_named_colors(self):
        for value, expected in (("#abc", "#aabbcc"), ("rgb(72, 138, 179)", "#488ab3"),
                                ("rgb(100%,0%,0%)", "#ff0000"), ("white", "#ffffff"),
                                ("#ff488ab3", "#488ab3")):
            with self.subTest(value=value):
                self.assertEqual(self.detect(QSS.replace("#488ab3", value)).color, expected)

    def test_later_same_selector_wins(self):
        self.assertEqual(self.detect(QSS + "#TabControls {background-color:#aabbcc;}").color,
                         "#aabbcc")

    def test_direct_inactive_rule_wins(self):
        result = self.detect(QSS + '#TabControls WPushButton[value = "0"] {background:#c56a21;}')
        self.assertEqual(result.color, "#c56a21")

    def test_play_specific_override_wins_over_later_generic(self):
        qss = QSS + '''#TabControls #TabButtonPlay {background:#c56a21;}
#TabControls WPushButton {background:#123456;}
#TabControls #TabButtonPlay[value="1"] {background:black;}
#TabButtonBrowse {background:#ff0000;}'''
        self.assertEqual(self.detect(qss).color, "#c56a21")

    def test_direct_black_override_falls_back(self):
        result = self.detect(QSS + '#TabButtonPlay {background-color:black;}')
        self.assertEqual(result.color, GRAY)
        self.assertTrue(result.fallback)

    def test_transparent_inactive_override_retains_bar(self):
        result = self.detect(QSS + '#TabButtonPlay {background-color:rgba(0,0,0,0);}')
        self.assertEqual(result.color, "#488ab3")

    def test_alpha_composites_with_bar(self):
        result = self.detect(QSS + '#TabButtonPlay {background-color:rgba(0,0,0,0.5);}')
        self.assertEqual(result.color, "#244559")

    def test_comments_hover_and_pressed_ignored(self):
        qss = "/* #TabControls {background:black;} */\n" + QSS
        qss += '#TabControls:hover {background:black;} #TabButtonPlay:pressed {background:black;}'
        result = self.detect(qss)
        self.assertEqual(result.color, "#488ab3")
        self.assertEqual(result.line, 4)

    def test_invalid_colors_and_missing_rules_fall_back(self):
        for qss in ("", "QWidget { background:black; }", QSS + "{",
                    QSS + "/* unclosed", QSS.replace("#488ab3", "nonsense"),
                    QSS.replace("#488ab3", "rgb(999,0,0)"),
                    QSS.replace("#488ab3", "transparent"),
                    QSS.replace("#488ab3", "qlineargradient(x1:0,y1:0,x2:1,y2:1)")):
            with self.subTest(qss=qss):
                self.assertEqual(self.detect(qss).color, GRAY)

    def test_image_override_falls_back_instead_of_wrong_color(self):
        result = self.detect(QSS + '#TabButtonPlay { background-image:url(foo.png); }')
        self.assertEqual(result.color, GRAY)

    def test_missing_or_removed_file_does_not_reuse_old_color(self):
        self.assertFalse(theme.detect(self.conf).fallback)
        self.file.unlink()
        self.assertEqual(theme.detect(self.conf).color, GRAY)

    def test_bad_encoding_and_size_are_safe(self):
        self.file.write_bytes(b"\xff")
        self.assertTrue(theme.detect(self.conf).fallback)
        self.file.write_bytes(b"a" * (theme.MAX_THEME_BYTES + 1))
        self.assertTrue(theme.detect(self.conf).fallback)

    def test_permission_error_falls_back(self):
        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            self.assertEqual(theme.detect(self.conf).color, GRAY)

    def test_source_checkout_discovery(self):
        self.assertEqual(theme.detect({}, self.home).source, str(self.file))
        self.assertEqual(theme.detect({}, self.home).color, "#488ab3")

    def test_custom_executable_prefix_and_share_layouts(self):
        for share in ("bitedj", "mixxx"):
            prefix = self.home / ("installed-" + share)
            qss = prefix / f"share/{share}/skins/BiteDJ/style.qss"
            qss.parent.mkdir(parents=True)
            qss.write_text(QSS)
            result = theme.detect({"BITEDJ_APP": str(prefix / "bin/bitedj")}, self.home)
            self.assertEqual(result.source, str(qss))

    def test_explicit_path_wins_and_missing_override_is_not_ignored(self):
        self.assertEqual(theme.detect({"BITEDJ_THEME_FILE": "~/bitedj/res/skins/BiteDJ/style.qss"},
                                      self.home).color, "#488ab3")
        result = theme.detect({"BITEDJ_THEME_FILE": "~/absent.qss"}, self.home)
        self.assertTrue(result.fallback)

    def test_cli_same_result_as_startup_detector(self):
        conf = self.home / "djrig.conf"
        conf.write_text(f'BITEDJ_THEME_FILE="{self.file}"\n')
        result = subprocess.run([sys.executable, str(ROOT / "theme_sync.py"), "--json"],
                                env={**os.environ, "DJRIG_CONF": str(conf)},
                                check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["color"], "#488ab3")

    def test_installer_reads_as_user_after_installing_modules(self):
        script = (ROOT / "install.sh").read_text()
        command = "/usr/bin/python3 -B /opt/bitedj-launcher/theme_sync.py"
        self.assertIn("launcher_palette.py theme_sync.py; do", script)
        self.assertIn("\n" + command + "\n", script)
        self.assertGreater(script.index(command), script.index("sudo touch /etc/djrig.conf"))
        self.assertNotIn("sudo " + command, script)


    def test_each_new_window_reads_updated_theme(self):
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QImage, QPainter
        from PyQt6.QtWidgets import QApplication
        import bitedj_launcher as gui
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        args = argparse.Namespace(windowed=True, demo_controller=True, snapshot=None)
        colors = []
        for new_color in ("#488ab3", "#cc8800", "#000000"):
            self.file.write_text(QSS.replace("#488ab3", new_color))
            with patch.object(gui.Network, "refresh"), \
                    patch.object(gui.Desktop, "bitedj_pid", return_value=None):
                window = gui.Desktop(args, self.conf)
                colors.append(window.accent.name())
                window.resize(1280, 800)
                window.fade = 1.0
                frame = window.grab().toImage()
                button = window.button_rect()
                self.assertEqual(frame.pixelColor(int(button.center().x()),
                                                 int(button.bottom() - 10)).name(),
                                 window.accent.name())
                # Check the actual controller cable paint, not just the property.
                icon = QImage(220, 160, QImage.Format.Format_ARGB32)
                icon.fill(0)
                painter = QPainter(icon)
                window.paint_controller_icon(painter, QPointF(100, 100), 40, 0.5235987756)
                painter.end()
                self.assertTrue(any(icon.pixelColor(x, y).alpha() > 0 and
                                    icon.pixelColor(x, y).name() == window.accent.name()
                                    for x in range(95, 125) for y in range(40, 80)))
                for timer in (window.anim, window.poll, window.slow, window.sec,
                              window.clock_hold, window.power_timer):
                    timer.stop()
                window.close()
                window.deleteLater()
                app.processEvents()
        self.assertEqual(colors, ["#488ab3", "#cc8800", GRAY])
