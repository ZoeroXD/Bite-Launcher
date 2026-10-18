"""Real labwc/Qt smoke check; headless only, never starts units or switches VTs.

Requires labwc. Uses two private headless compositors to verify that exiting the
Lite session client ends its labwc but leaves the other compositor alive.
Physical boot, DRM/logind and virtual-console activation still require a Pi test.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def client(output):
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication
    import bite_runtime as rt
    import bitedj_launcher as gui
    rt.lite_mode = lambda: True  # Fixture profile; no /opt installation in sandbox.
    app = QApplication([])
    args = argparse.Namespace(windowed=True, demo_controller=True, snapshot=None)
    window = gui.Desktop(args, {})
    window.resize(1280, 800)
    window.net_open = True
    window.show()

    def ready():
        handle = window.windowHandle()
        if handle and handle.isExposed():
            window.grab().save(str(output / "lite-menu.png"))
            (output / "ready.json").write_text(json.dumps(
                {"pid": os.getpid(), "compositor": int(os.environ["LABWC_PID"]),
                 "platform": app.platformName(), "lite": window.lite}))
        else:
            QTimer.singleShot(100, ready)
    QTimer.singleShot(500, ready)
    return app.exec()


def main():
    if sys.argv[1:2] == ["--client"]:
        raise SystemExit(client(Path(sys.argv[2])))
    binary = shutil.which("labwc")
    if not binary:
        raise SystemExit("Install labwc in the test environment to run this optional check")
    import configure_lite
    with tempfile.TemporaryDirectory(prefix="bite-lite-smoke-") as directory:
        root = Path(directory)
        config = root / "config"
        config.mkdir()
        for name, data in configure_lite.payload(pwd.getpwuid(os.getuid()), "lite").items():
            if name.startswith(configure_lite.CONFIG + "/"):
                (config / Path(name).name).write_text(data)
        # Static unit validation does not start or enable the service.
        unit = root / configure_lite.UNIT
        unit.write_text(configure_lite.payload(pwd.getpwuid(os.getuid()), "lite")[configure_lite.SERVICE])
        subprocess.run(["systemd-analyze", "verify", str(unit)], check=True, timeout=20)
        processes = []
        with (root / "labwc.log").open("wb") as log:
            try:
                for index, command in enumerate((
                        "/bin/sleep 45",
                        shlex.join([sys.executable, str(Path(__file__).resolve()), "--client", str(root)]))):
                    runtime = root / f"runtime-{index}"
                    runtime.mkdir(mode=0o700)
                    env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime),
                           "WLR_BACKENDS": "headless", "WLR_RENDERER": "pixman",
                           "WLR_HEADLESS_OUTPUTS": "1", "QT_QPA_PLATFORM": "wayland"}
                    env.pop("WAYLAND_DISPLAY", None)
                    env.pop("DISPLAY", None)
                    processes.append(subprocess.Popen(
                        [binary, "-C", str(config), "-S", command], env=env,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log))
                deadline = time.monotonic() + 15
                while not (root / "ready.json").exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                data = json.loads((root / "ready.json").read_text())
                assert data["platform"] == "wayland" and data["lite"]
                assert data["compositor"] == processes[1].pid
                print("PASS: actual labwc + Qt Wayland Lite menu is exposed and rendered")
                os.kill(data["pid"], signal.SIGTERM)
                processes[1].wait(timeout=10)
                assert processes[0].poll() is None
                print("PASS: exiting session client closes only its labwc")
                print("PASS: systemd unit syntax; physical VT/logind boot NOT tested")
            except BaseException:
                print((root / "labwc.log").read_text(errors="replace")[-5000:], file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
