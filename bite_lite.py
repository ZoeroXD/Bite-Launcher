"""User-side lifecycle of the dedicated labwc Lite session; no sudo or broad kills."""
import os
import signal
import subprocess
import sys
import time

import bite_runtime as rt

UNIT = "bite-launcher-lite.service"
CONFIG = "/etc/bite-launcher/labwc"


def managed_compositor(argv):
    return (argv and argv[0] == "/usr/bin/labwc"
            and "-C" in argv and argv[argv.index("-C") + 1:][:1] == [CONFIG])


def valid_session():
    record = rt.read_runtime("lite-session.json")
    owned = rt.processes()
    return (rt.lite_mode() and record.get("uid") == os.getuid()
            and record.get("pid") in rt.matching("lite-session")
            and managed_compositor(owned.get(record.get("compositor"), [])))


def start():
    if not rt.lite_mode():
        raise RuntimeError("Lite session is not configured for this user")
    if valid_session():
        return
    if rt.matching("labwc"):
        raise RuntimeError("Another graphical session is running; it was left untouched")
    # The root-owned policy authorizes only starting this fixed unit for its owner.
    result = subprocess.run(["systemctl", "--no-ask-password", "start", UNIT],
                            capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise RuntimeError("Could not start the Lite session: " + result.stderr.strip())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        token = rt.read_runtime("view-request.json").get("token")
        if valid_session() and token and rt.view_ready(token):
            return
        time.sleep(0.2)
    raise RuntimeError("Lite session did not paint its launcher; check journalctl -u " + UNIT)


def to_terminal():
    if not valid_session():
        raise RuntimeError("No verified launcher-owned Lite session; nothing was stopped")
    import bite_session
    bite_session.save_mode(True)
    rt.stop("wrapper")
    rt.stop("bitedj", timeout=15)  # An intentional mode exit, not a controller disconnect.
    rt.stop("launcher")
    rt.stop("backdrop")
    rt.stop("lite-session", timeout=20)  # --session exits labwc; unit returns to tty1.


def session():
    if not rt.lite_mode():
        raise RuntimeError("Lite session profile does not belong to this user")
    pid = int(os.environ.get("LABWC_PID", "0"))
    if not managed_compositor(rt.processes().get(pid, [])):
        raise RuntimeError("Not running in the dedicated Bite Launcher compositor")
    stopping = False

    def stop(_sig, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    rt.write_runtime("lite-session.json", {"uid": os.getuid(), "pid": os.getpid(), "compositor": pid})
    import bite_session
    try:
        # This process is labwc's session client; exiting it closes only this labwc.
        with rt.lock("mode"):
            if rt.OFF.exists():
                return
            bite_session.to_launcher()
        while not stopping:
            if not rt.matching("wrapper") and not rt.matching("launcher"):
                raise RuntimeError("Launcher session exited; returning to text console")
            time.sleep(0.25)
    finally:
        rt.stop("wrapper")
        rt.stop("bitedj", timeout=15)
        rt.stop("launcher")
        rt.stop("backdrop")
        (rt.RUN / "lite-session.json").unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["session"]:
            raise RuntimeError("Use bite-mode launcher or bite-mode terminal")
        session()
    except (RuntimeError, OSError, ValueError) as error:
        rt.report_error(str(error))
        raise SystemExit(str(error))
