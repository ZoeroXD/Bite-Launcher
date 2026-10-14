#!/usr/bin/env python3
"""One shared mode controller for boot, desktop icon, keyboard, clock, and FLX4."""
import os
from pathlib import Path
import signal
import sys
import time

import bite_runtime as rt


def save_mode(desktop):
    rt.STATE.mkdir(parents=True, exist_ok=True)
    if desktop:
        rt.OFF.touch()
    else:
        rt.OFF.unlink(missing_ok=True)


def desktop_available():
    return all(os.access("/usr/bin/" + x, os.X_OK) for x in ("wf-panel-pi", "pcmanfm-pi"))


def stop_desktop():
    rt.stop("desktop-respawn")
    for name in ("wf-panel-pi", "pcmanfm-pi", "desktop-child"):
        rt.stop(name)


def start_desktop():
    rt.stop("backdrop")
    for name in ("wf-panel-pi", "pcmanfm-pi"):
        running = rt.matching(name)
        if name == "pcmanfm-pi":
            running = running or rt.matching("desktop-child")
        respawning = any(any(Path(x).name == name for x in a[1:])
                         for a in rt.matching("desktop-respawn").values())
        if not running and not respawning:
            command = ["/usr/bin/" + name]
            if os.access("/usr/bin/lwrespawn", os.X_OK):
                command.insert(0, "/usr/bin/lwrespawn")
            rt.spawn(command)


def to_desktop():
    # Don't kill the interface or change saved mode on Lite with no desktop.
    if not desktop_available():
        raise RuntimeError("Pi desktop is not installed; mode was not changed")
    env = rt.session_env()  # capture before stopping graphical clients
    os.environ.update(env)
    save_mode(True)
    rt.stop("wrapper")             # FIRST: it must not restart the app
    rt.stop("bitedj", timeout=15)   # latest user specification: always close BiteDJ
    rt.stop("launcher")
    start_desktop()


def to_launcher():
    if not Path(rt.APP).is_file():
        raise RuntimeError("Bite Launcher is not installed")
    rt.check_display()
    token = rt.request_view()
    save_mode(False)
    new_backdrop = not rt.matching("backdrop")
    new_wrapper = not rt.matching("wrapper")
    try:
        if new_backdrop:
            rt.spawn(["swaybg", "-c", "#000000"])
        if new_wrapper:
            rt.spawn(["/usr/local/bin/bitedj-launcher"])
        # Only a fresh, matching acknowledgement after a visible paint can hide desktop.
        # Existing app instances also consume the request and present their window.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if rt.view_ready(token):
                stop_desktop()
                return
            time.sleep(0.1)
        raise RuntimeError("Launcher did not display its interface; desktop was not stopped. "
                           "Reboot after upgrading, then check ~/.cache/bite-launcher.log.")
    except (RuntimeError, OSError):
        # Cancel this unsuccessful startup, not an automatic change to the saved mode.
        # Never close BiteDJ or an existing launcher merely because acknowledgement failed.
        if new_wrapper:
            rt.stop("wrapper")
        if new_backdrop:
            rt.stop("backdrop")
        raise


def wrapper():
    with rt.lock("wrapper"):
        stopping = False

        def stop_wrapper(_sig, _frame):
            nonlocal stopping
            stopping = True

        signal.signal(signal.SIGTERM, stop_wrapper)
        signal.signal(signal.SIGINT, stop_wrapper)
        child = None
        while not stopping and not rt.OFF.exists():
            env = rt.session_env()
            compositor = env.get("LABWC_PID")
            if compositor:
                try:
                    os.kill(int(compositor), 0)
                except (OSError, ValueError):
                    break
            if not rt.matching("launcher"):
                if child is not None:
                    child.poll()  # reap child; don't accumulate zombies after restarts
                env["QT_QPA_PLATFORM"] = "wayland"
                child = rt.spawn(["/usr/bin/python3", rt.APP], env=env)
            # Poll stop/session state without another permanent worker.
            for _ in range(10):
                if stopping or rt.OFF.exists():
                    break
                time.sleep(0.1)
        # Do not kill the launcher child here: mode switch closes BiteDJ before the app.


def main(argv=None):
    command = (argv or sys.argv[1:] or ["toggle"])[0]
    if command == "wrapper":
        wrapper()
        return
    if command == "status":
        print("launcher" if rt.matching("launcher") else "desktop")
        return
    if command not in ("launcher", "desktop", "toggle", "boot", "start", "stop"):
        raise RuntimeError("usage: bite-mode launcher|desktop|toggle|boot|status|start|stop")
    with rt.lock("mode"):
        if command == "start":
            rt.start_bitedj()
        elif command == "stop":
            rt.stop("bitedj", timeout=15)
        elif command == "desktop" or (command == "toggle" and rt.matching("launcher")):
            to_desktop()
        elif command == "boot" and rt.OFF.exists():
            if desktop_available():
                start_desktop()
        else:
            try:
                to_launcher()
            except (RuntimeError, OSError):
                if command == "boot" and desktop_available():
                    # Desktop clients were intentionally not started on this boot.
                    # Recover visibly on failure without rewriting the saved mode.
                    rt.stop("wrapper")
                    rt.stop("launcher")
                    start_desktop()
                raise


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as exc:
        rt.report_error(str(exc))
        print(str(exc), file=sys.stderr)
        sys.exit(1)
