#!/usr/bin/env python3
"""Configure an isolated Lite kiosk, without autologin or modifying desktop files.

Root operations own only the generated unit, narrow start permission, labwc
configuration and installation profile. No live session is started at install.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import tempfile

UNIT = "bite-launcher-lite.service"
PROFILE = "/opt/bitedj-launcher/session.json"
STATE = "/var/lib/bite-launcher/lite-install.json"
CONFIG = "/etc/bite-launcher/labwc"
SERVICE = "/etc/systemd/system/" + UNIT
POLICY = "/etc/polkit-1/rules.d/49-bite-launcher-lite.rules"
PATHS = (SERVICE, POLICY, CONFIG + "/rc.xml", CONFIG + "/autostart",
         CONFIG + "/environment", CONFIG + "/menu.xml", PROFILE)


def detect(root=Path("/")):
    """Capability-based: Lite plus an installed Pi desktop is desktop-capable."""
    present = [os.access(root / ("usr/bin/" + name), os.X_OK)
               for name in ("wf-panel-pi", "pcmanfm-pi")]
    if all(present):
        return "desktop"
    if any(present):
        raise ValueError("Partial Raspberry Pi Desktop installation; review before installing")
    return "lite"


def account(name):
    user = pwd.getpwnam(name)
    if user.pw_uid == 0 or not re.fullmatch(r"[a-z_][a-z0-9_-]*", name):
        raise ValueError("A normal local user is required")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", user.pw_dir) or ".." in Path(user.pw_dir).parts:
        raise ValueError("Nonstandard home path requires review")
    return user


def payload(user, mode):
    profile = json.dumps({"kind": mode, "uid": user.pw_uid}, indent=2) + "\n"
    if mode == "desktop":
        return {PROFILE: profile}
    # Full PAM/logind session on tty7; tty1 remains an ordinary password-protected
    # getty. Only VT activation runs privileged; labwc and all DJ apps run as user.
    unit = f"""[Unit]
Description=Bite Launcher Lite graphical session
After=systemd-user-sessions.service systemd-logind.service getty@tty1.service
Wants=getty@tty1.service
ConditionPathExists=!{user.pw_dir}/.config/bite-launcher/autostart-off

[Service]
Type=simple
User={user.pw_name}
PAMName=login
WorkingDirectory={user.pw_dir}
Environment=HOME={user.pw_dir}
Environment=XDG_SESSION_TYPE=wayland
Environment=XDG_SESSION_CLASS=user
Environment=XDG_SEAT=seat0
Environment=XDG_VTNR=7
Environment=LIBSEAT_BACKEND=logind
TTYPath=/dev/tty7
StandardInput=tty-fail
StandardOutput=journal
StandardError=journal
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
UtmpIdentifier=tty7
UtmpMode=user
ExecStartPre=+/usr/bin/chvt 7
ExecStart=/usr/bin/labwc -C {CONFIG} -S "/usr/bin/python3 /opt/bitedj-launcher/bite_lite.py session"
ExecStopPost=+/usr/bin/chvt 1
Restart=no
KillMode=control-group
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
"""
    policy = f"""// Bite Launcher: only this account may start its fixed, root-owned kiosk unit.
polkit.addRule(function(action, subject) {{
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "{UNIT}" &&
        action.lookup("verb") == "start" && subject.user == "{user.pw_name}") {{
        return polkit.Result.YES;
    }}
}});
"""
    rc = """<?xml version="1.0"?>
<labwc_config><keyboard><default/>
<keybind key="C-A-d"><action name="Execute" command="/usr/local/bin/bite-mode terminal"/></keybind>
</keyboard></labwc_config>
"""
    return {SERVICE: unit, POLICY: policy, CONFIG + "/rc.xml": rc,
            CONFIG + "/autostart": "# Startup is owned by labwc --session, not shell respawn.\n",
            CONFIG + "/environment": "# Dedicated Lite session; no desktop environment overrides.\n",
            CONFIG + "/menu.xml": '<?xml version="1.0"?>\n<openbox_menu/>\n',
            PROFILE: profile}


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=45)


def command(args, runner=run):
    result = runner(args)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def secure(path, root=Path("/")):
    """All ancestors and existing files must remain root/current-executor owned."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f"Managed destination is a symlink: {item}")
        if item.exists():
            info = item.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError(f"Unsafe managed destination: {item}")
        if item == root:
            break


def write(path, data, root=Path("/")):
    secure(path, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".bite-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record(root, user):
    path = root / STATE.lstrip("/")
    secure(path, root)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        raise ValueError("Invalid Lite ownership record")
    if data.get("uid") != user.pw_uid or data.get("version") != 1:
        raise ValueError("Lite installation belongs to another user or unknown version")
    if set(data.get("files", {})) != set(PATHS):
        raise ValueError("Invalid Lite ownership record")
    for name, digest in data["files"].items():
        target = root / name.lstrip("/")
        secure(target, root)
        if not target.is_file() or sha(target.read_bytes()) != digest:
            raise ValueError(f"Managed file was edited or removed; preserve and review: {target}")
    return data


def check(root, user, mode, runner=run):
    data = record(root, user)
    if data and mode != "lite":
        raise ValueError("Uninstall the managed Lite session before changing to Desktop")
    for name, text in payload(user, mode).items():
        path = root / name.lstrip("/")
        secure(path, root)
        if path.exists() and not data:
            if name == PROFILE and path.read_text() == text:
                continue
            raise ValueError(f"Existing unowned destination; preserving: {path}")
    if mode == "lite":
        # Do not shadow another unit or silently inherit administrator overrides.
        for folder in ("etc/systemd/system", "run/systemd/system", "usr/lib/systemd/system",
                       "usr/local/lib/systemd/system", "lib/systemd/system"):
            directory = root / folder
            override = directory / (UNIT + ".d")
            if override.exists() or override.is_symlink():
                raise ValueError(f"Existing Lite service overrides require review: {override}")
            unit = directory / UNIT
            if folder != "etc/systemd/system" and (unit.exists() or unit.is_symlink()):
                raise ValueError(f"Existing Lite service requires review: {unit}")
        display_manager = root / "etc/systemd/system/display-manager.service"
        if display_manager.exists() or display_manager.is_symlink():
            raise ValueError("An existing display manager requires review; not replacing it")
        for name in ("etc/systemd/system/getty@tty1.service",
                     "etc/systemd/system/getty@.service"):
            path = root / name
            if path.is_symlink() and os.readlink(path) == "/dev/null":
                raise ValueError("Console getty is masked; a terminal fallback is required")
        if not data:
            if runner(["systemctl", "is-active", "getty@tty7.service"]).returncode == 0:
                raise ValueError("tty7 already runs a getty; choose/review a free virtual console")
            sessions = root / "run/systemd/sessions"
            if sessions.exists() and any("TTY=tty7" in p.read_text(errors="replace").splitlines()
                                         for p in sessions.iterdir() if p.is_file()):
                raise ValueError("tty7 is already in use; preserving the existing session")
    return data


def install(root, user, mode, runner=run):
    previous = check(root, user, mode, runner)
    files = {name: text.encode() for name, text in payload(user, mode).items()}
    state = root / STATE.lstrip("/")
    originals = {name: (root / name.lstrip("/")).read_bytes()
                 if (root / name.lstrip("/")).exists() else None for name in files}
    old_record = state.read_bytes() if state.exists() else None
    try:
        for name, data in files.items():
            write(root / name.lstrip("/"), data, root)
        if mode == "lite":
            write(state, (json.dumps({"version": 1, "uid": user.pw_uid,
                                      "files": {name: sha(data) for name, data in files.items()}},
                                     indent=2) + "\n").encode(), root)
            command(["systemctl", "daemon-reload"], runner)
            command(["systemctl", "enable", UNIT], runner)  # Never --now during install.
    except Exception:
        for name, data in originals.items():
            path = root / name.lstrip("/")
            if data is None:
                path.unlink(missing_ok=True)
            else:
                write(path, data, root)
        if old_record is None:
            state.unlink(missing_ok=True)
        else:
            write(state, old_record, root)
        if mode == "lite":
            if not previous:
                runner(["systemctl", "disable", UNIT])
            runner(["systemctl", "daemon-reload"])
        raise
    return f"{mode.title()} profile installed. Reboot to activate; no live session was started."


def check_remove(root, user):
    """Validate ownership, not install suitability: removal must allow recovery."""
    data = record(root, user)
    profile = root / PROFILE.lstrip("/")
    secure(profile, root)
    if profile.exists():
        value = json.loads(profile.read_text())
        if (not isinstance(value, dict) or value.get("uid") != user.pw_uid
                or value.get("kind") not in ("desktop", "lite")):
            raise ValueError("Session profile belongs to another user or is invalid")
    return data


def remove(root, user, runner=run):
    data = check_remove(root, user)
    if not data:
        return "No managed Lite session to remove"
    # Run uninstall over SSH or at the text console, not inside the kiosk.
    command(["systemctl", "disable", "--now", UNIT], runner)
    for name in data["files"]:
        # Keep the profile until uninstall.sh removes /opt. A retry must still
        # know not to edit the user's Desktop/labwc configuration.
        if name != PROFILE:
            (root / name.lstrip("/")).unlink()
    (root / STATE.lstrip("/")).unlink()
    command(["systemctl", "daemon-reload"], runner)
    return "Lite kiosk removed; the normal text login console remains"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detect", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("--mode", choices=("desktop", "lite"))
    parser.add_argument("--user")
    args = parser.parse_args()
    try:
        if args.detect:
            print(detect())
            return
        if os.geteuid() != 0 or not args.user:
            raise ValueError("Use sudo and specify the installing --user")
        user = account(args.user)
        if args.remove:
            if args.check:
                check_remove(Path("/"), user)
                print("Session removal precheck passed; no changes")
            else:
                print(remove(Path("/"), user))
        elif args.check:
            check(Path("/"), user, args.mode or detect())
            print("Session configuration precheck passed; no changes")
        else:
            print(install(Path("/"), user, args.mode or detect()))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(1, f"STOP: {error}\n")


if __name__ == "__main__":
    main()
