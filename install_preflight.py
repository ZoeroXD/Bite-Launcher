#!/usr/bin/env python3
"""Read-only installer checks. No boot, package, or mode changes."""
import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shlex
import subprocess
import xml.etree.ElementTree as ET

from configure_autostart import BEGIN, END, desktop_command, guard, unguard
from configure_session import COMMAND, local
from configure_lite import detect


@dataclass
class Report:
    conflicts: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def probe(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=15, **kwargs)


def read_os(path):
    values = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        words = shlex.split(value)
        values[key] = words[0] if words else ""
    return values


def safe_read(path, report):
    try:
        if path.is_symlink():
            report.conflicts.append(f"{path}: symlink at a configuration destination; preserving it.")
            return None
        return path.read_text() if path.exists() else None
    except (OSError, UnicodeError) as exc:
        report.conflicts.append(f"{path}: cannot inspect safely ({exc}).")
        return None


def check_autostart(path, report, run, removing):
    text = safe_read(path, report)
    if text is None:
        return
    try:
        if text.count(BEGIN) != text.count(END):
            raise ValueError("unbalanced managed desktop guard markers")
        lines = text.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.strip() == BEGIN:
                if index + 4 >= len(lines):
                    raise ValueError("incomplete managed desktop guard")
                expected = guard(lines[index + 2])
                actual = "".join(lines[index:index + 5])
                if not expected.startswith(BEGIN) or expected != actual:
                    raise ValueError("managed desktop guard has been edited")
        plain = unguard(text)
        result = run(["/bin/sh", "-n"], input=plain)
        if result.returncode:
            raise ValueError("autostart shell syntax is invalid")
        # Preserve complex custom desktop startup rather than partly rewriting it.
        if not removing:
            for number, line in enumerate(plain.splitlines(), 1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
                lexer.whitespace_split = True
                words = list(lexer)
                if not words:
                    continue
                simple_print = words[0] in ("echo", "printf") and not any(
                    w in (";", "&&", "||", "&", "|") for w in words)
                if simple_print:
                    continue
                clients = {"wf-panel-pi", "pcmanfm-pi"}
                has_desktop = any(Path(word).name in clients for word in words)
                has_desktop |= any(Path(word).name == "pcmanfm" for word in words) and "--desktop" in words
                has_desktop |= any(Path(word).name == "swaybg" for word in words) and any(
                    word in ("-i", "--image") for word in words)
                if has_desktop and not desktop_command(line):
                    raise ValueError(f"line {number}: custom desktop startup needs review")
                # configure_session migrates these entries. Do not mistake a
                # similarly named custom splash script for a launcher entry.
                migrated = ("bitedj-launcher" in line or "bite-mode boot" in line
                            or "/usr/local/bin/bite-launcher-open boot" in line
                            or "# bite-launcher: managed startup" in line)
                if migrated:
                    tokens = words[:-1] if words[-1] == "&" else words
                    name = Path(tokens[0]).name
                    allowed = (
                        (name == "bitedj-launcher" and len(tokens) == 1)
                        or (name in ("bite-mode", "bite-launcher-open") and tokens[1:] == ["boot"])
                    )
                    if not allowed:
                        raise ValueError(f"line {number}: unrecognized launcher/splash startup entry")
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        report.conflicts.append(f"{path}: {exc}; no edits made.")


def inspect(root=Path("/"), home=None, run=probe, env=None, removing=False):
    """Root/home injection is for fixture tests; the CLI always checks this machine."""
    root = Path(root)
    home = Path.home() if home is None else Path(home)
    env = os.environ if env is None else env
    report = Report()
    system = lambda name: root / name.lstrip("/")
    try:
        kind = detect(root)
    except ValueError as exc:
        kind = "desktop"
        if not removing:
            report.conflicts.append(str(exc))

    if not removing:
        try:
            release = read_os(system("/etc/os-release"))
            family = {release.get("ID", ""), *release.get("ID_LIKE", "").split()}
            if release.get("VERSION_CODENAME") != "trixie" or not family & {"debian", "raspbian"}:
                report.conflicts.append("This installer targets Trixie with Raspberry Pi Desktop or Raspberry Pi OS Lite.")
            else:
                report.notes.append("Trixie detected; no ARM bitness is assumed.")
        except (OSError, ValueError, UnicodeError) as exc:
            report.conflicts.append(f"Cannot identify the installed OS: {exc}")
        required = ("labwc", "wf-panel-pi", "pcmanfm-pi") if kind == "desktop" else ()
        for name in required:
            if not os.access(system("/usr/bin/" + name), os.X_OK):
                report.conflicts.append(f"Required desktop component missing: /usr/bin/{name}. Desktop will not be installed automatically.")
        if kind == "lite":
            if (home / ".config/bite-launcher/managed-boot").exists():
                report.conflicts.append("Desktop integration is already installed; uninstall it before switching to Lite.")
            model = system("/proc/device-tree/model")
            if not model.exists():
                model = system("/sys/firmware/devicetree/base/model")
            if not model.exists() or b"Raspberry Pi" not in model.read_bytes():
                report.conflicts.append("Lite automatic console startup requires verified Raspberry Pi hardware.")
            else:
                report.notes.append("Lite detected: install minimal labwc session, no Pi Desktop; terminal return on tty1.")
        try:
            audit = run(["dpkg", "--audit"])
            if audit.returncode or audit.stdout.strip():
                report.conflicts.append("dpkg reports pending/broken package configuration. Resolve it before installation; no automatic repair attempted.")
        except (OSError, subprocess.SubprocessError) as exc:
            report.conflicts.append(f"Cannot check package state: {exc}")
        try:
            session = run(["systemctl", "--user", "show-environment"])
            if session.returncode:
                report.conflicts.append("User systemd manager is unavailable. Log in as the normal Pi user before installing.")
        except (OSError, subprocess.SubprocessError) as exc:
            report.conflicts.append(f"Cannot check user session: {exc}")

    config_home = env.get("XDG_CONFIG_HOME")
    if config_home and Path(config_home) != home / ".config":
        report.conflicts.append("Custom XDG_CONFIG_HOME requires review; this installer manages ~/.config only.")
    config_dirs = env.get("XDG_CONFIG_DIRS", "/etc/xdg").split(":")
    for directory in config_dirs:
        if directory and directory != "/etc/xdg" and system(directory + "/labwc/autostart").exists():
            report.conflicts.append(f"Additional labwc autostart at {directory}/labwc/autostart requires review.")

    for path in (home / ".config/labwc/autostart", system("/etc/xdg/labwc/autostart")):
        check_autostart(path, report, run, removing)

    user_rc = home / ".config/labwc/rc.xml"
    rc = user_rc if user_rc.exists() or user_rc.is_symlink() else system("/etc/xdg/labwc/rc.xml")
    text = safe_read(rc, report)
    if text is not None:
        try:
            tree = ET.fromstring(text)
            # Raspberry Pi OS ships labwc's rc.xml with the Openbox-compatible root.
            if local(tree.tag) not in ("labwc_config", "openbox_config"):
                raise ValueError("unexpected XML root")
            if not removing:
                for node in tree.iter():
                    if local(node.tag) != "keybind" or node.get("key", "").lower() != "c-a-d":
                        continue
                    actions = [a for a in node if local(a.tag) == "action"]
                    if len(actions) != 1 or actions[0].get("command") != COMMAND:
                        report.conflicts.append(f"{rc}: Ctrl+Alt+D is assigned to a different action; preserving it.")
        except (ET.ParseError, ValueError) as exc:
            report.conflicts.append(f"{rc}: invalid labwc configuration ({exc}).")

    record = home / ".config/bite-launcher/installer.json"
    text = safe_read(record, report)
    if text is not None:
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("installer record is not an object")
            for key in ("old_ctrl_alt_d", "removed_autostart"):
                if key in data and (not isinstance(data[key], list)
                                    or not all(isinstance(x, str) for x in data[key])):
                    raise ValueError(f"invalid {key} record")
            for binding in data.get("old_ctrl_alt_d", []):
                ET.fromstring(binding)
        except (ValueError, ET.ParseError) as exc:
            report.conflicts.append(f"{record}: invalid saved installer record ({exc}).")

    destinations = [
        home / ".config/labwc", home / ".config/bite-launcher",
        home / ".config/systemd/user/bitedj-hold.service", home / "bin/bite-mode",
        home / "bin/bitedj-hold.py", system("/opt/bitedj-launcher"),
        system("/usr/local/bin/bite-mode"), system("/usr/local/bin/bitedj-launcher"),
        system("/usr/local/bin/bite-launcher-open"), system("/etc/xdg/labwc"),
        system("/etc/djrig.conf"),
    ]
    for destination in destinations:
        if destination.is_symlink():
            report.conflicts.append(f"{destination}: linked installation destination requires review.")

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    report = inspect(removing=args.uninstall)
    for note in report.notes:
        print("INFO:", note)
    if report.conflicts:
        print("\nSTOP: compatibility conflict(s); installation has not started.")
        for conflict in report.conflicts:
            print(" -", conflict)
        print("Preserve the current configuration and review these conflicts before retrying.")
        return 2
    print("Launcher preflight passed. These checks made no changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
