"""1.3.4 installer compatibility tests. Never change real boot files or run power actions."""
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install_preflight as preflight
import bite_runtime as runtime
import bite_session as session


def put(path, text="", mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "home/dj"
        self.home.mkdir(parents=True)
        put(self.root / "etc/os-release", 'ID=debian\nVERSION_CODENAME=trixie\n')
        for command in ("labwc", "wf-panel-pi", "pcmanfm-pi"):
            put(self.root / ("usr/bin/" + command), "#!/bin/sh\n", 0o755)
        put(self.root / "etc/xdg/labwc/rc.xml", "<labwc_config><keyboard/></labwc_config>")
        put(self.root / "etc/xdg/labwc/autostart", "wf-panel-pi &\npcmanfm-pi &\n")

    def tearDown(self):
        self.temp.cleanup()

    def report(self, run=None):
        run = run or (lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
        return preflight.inspect(root=self.root, home=self.home, env={}, run=run)

    def test_clean_trixie_has_no_launcher_conflict(self):
        self.assertEqual(self.report().conflicts, [])

    def test_other_os_is_not_silently_installed(self):
        put(self.root / "etc/os-release", "ID=debian\nVERSION_CODENAME=bookworm\n")
        self.assertTrue(self.report().conflicts)

    def test_edited_desktop_guard_stops_before_install(self):
        path = self.root / "etc/xdg/labwc/autostart"
        from configure_autostart import guard
        path.write_text(guard("wf-panel-pi &\n").replace("if [", "if false; # [", 1))
        self.assertTrue(self.report().conflicts)

    def test_existing_unrelated_shortcut_is_preserved(self):
        file = self.home / ".config/labwc/rc.xml"
        text = '<labwc_config><keyboard><keybind key="C-A-d"><action name="Execute" command="my-tool"/></keybind></keyboard></labwc_config>'
        put(file, text)
        self.assertTrue(self.report().conflicts)
        self.assertEqual(file.read_text(), text)

    def test_pi_openbox_rc_root_is_accepted(self):
        text = ('<?xml version="1.0" encoding="UTF-8"?>\n<openbox_config xmlns="http://openbox.org/3.4/rc">'
                '<theme><name>PiXtrix</name></theme><keyboard><keybind key="C-A-d">'
                '<action name="Execute" command="%s"/></keybind></keyboard></openbox_config>' % preflight.COMMAND)
        file = self.home / ".config/labwc/rc.xml"
        put(file, text)
        self.assertEqual(self.report().conflicts, [])
        self.assertEqual(file.read_text(), text)
        put(file, text.replace(preflight.COMMAND, "my-tool"))
        self.assertTrue(self.report().conflicts)
        put(file, "<something_else/>")
        self.assertTrue(self.report().conflicts)

    def test_malformed_xml_and_installer_record_stop(self):
        put(self.home / ".config/labwc/rc.xml", "<broken")
        put(self.home / ".config/bite-launcher/installer.json", "[1]")
        self.assertGreaterEqual(len(self.report().conflicts), 2)

    def test_pending_packages_stop_without_repair(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout="pending package" if args[0] == "dpkg" else "", stderr="")
        self.assertTrue(self.report(run).conflicts)
        self.assertFalse(any("apt" in part for call in calls for part in call))

    def test_installer_stops_before_sudo_when_preflight_fails(self):
        bin_dir = self.root / "bin"
        log = self.root / "calls"
        put(bin_dir / "id", "#!/bin/sh\ncase \"$1\" in -u) echo 1000;; -un) echo dj;; esac\n", 0o755)
        put(bin_dir / "python3", f"#!/bin/sh\nprintf 'preflight\\n' >> '{log}'\nexit 2\n", 0o755)
        put(bin_dir / "sudo", f"#!/bin/sh\nprintf 'SUDO\\n' >> '{log}'\nexit 99\n", 0o755)
        script = Path(__file__).resolve().parents[1] / "install.sh"
        result = subprocess.run(["bash", str(script)], env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(log.read_text(), "preflight\n")


class PreservationTests(unittest.TestCase):
    def test_preserved_core_ui_matches_v32(self):
        current = Path(__file__).resolve().parents[1] / "bitedj_launcher.py"
        self.assertEqual(hashlib.sha256(current.read_bytes()).hexdigest(),
                         "03bff55583dc943708260255686f0bf50da5b17390644c6a95844cbf047dfee8")

    def test_mode_controller_saves_marker(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(runtime, "OFF", Path(folder) / "off"):
            session.save_mode(True)
            self.assertTrue(runtime.OFF.exists())
            session.save_mode(False)
            self.assertFalse(runtime.OFF.exists())


class DesktopShortcutTests(unittest.TestCase):
    """The Pi desktop (libfm) asks "Execute / Execute in Terminal / Open" for a plain
    .desktop file on the desktop, whatever its permissions. A Type=Link entry whose URL
    is the absolute path of the installed menu entry under /usr/share/applications opens
    directly, like the Pi menu's "Add to Desktop"."""

    ROOT = Path(__file__).resolve().parents[1]

    def entry(self, name):
        import configparser
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser.read(self.ROOT / name)
        return parser["Desktop Entry"]

    def test_shortcut_links_to_installed_menu_entry(self):
        link = self.entry("bite-launcher-shortcut.desktop")
        self.assertEqual(link["Type"], "Link")
        self.assertEqual(link["URL"], "/usr/share/applications/bite-launcher.desktop")
        self.assertEqual(link["Name"], "Bite Launcher")
        self.assertEqual(link["Icon"], self.entry("bite-launcher.desktop")["Icon"])

    def test_installer_installs_link_without_execute_bit(self):
        script = (self.ROOT / "install.sh").read_text()
        self.assertIn("install -m 644 bite-launcher.desktop /usr/share/applications/bite-launcher.desktop", script)
        self.assertIn('install -m 644 bite-launcher-shortcut.desktop "$DESKTOP_DIR/bite-launcher.desktop"', script)
        self.assertNotIn("metadata::trust", script)
