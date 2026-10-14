"""Optional real-backend smoke test in an isolated headless Weston compositor.

Requires weston and a working PyQt6 Wayland plugin. Does not install the app,
switch the real desktop, or start real BiteDJ. Run: python3 tests/wayland_smoke.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bite_runtime as rt


def until(predicate, seconds=12):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.1)
    raise AssertionError("Timed out waiting for compositor or painted launcher")


def main():
    weston = shutil.which("weston")
    if not weston:
        raise SystemExit("Install weston to run this optional isolated display test")
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="bite-wayland-") as directory:
        temp = Path(directory)
        runtime = temp / "runtime"
        runtime.mkdir(mode=0o700)
        env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime),
               "WAYLAND_DISPLAY": "bite-smoke", "QT_QPA_PLATFORM": "wayland"}
        config = temp / "djrig.conf"
        fake = temp / "fake-mixxx"
        config.write_text(f"BITEDJ_APP={fake}\nBITEDJ_FPS=30\n")
        env["DJRIG_CONF"] = str(config)
        log = temp / "smoke.log"
        clients = []
        with log.open("wb") as output:
            compositor = subprocess.Popen(
                [weston, "--backend=headless", "--renderer=pixman", "--socket=bite-smoke",
                 "--width=1280", "--height=800", "--idle-time=0", "--no-config"],
                env=env, stdout=output, stderr=output)
            try:
                until(lambda: (runtime / "bite-smoke").is_socket())
                with patch.object(rt, "RUN", runtime / "bite-launcher"), \
                     patch.object(rt, "APP", str(root / "bitedj_launcher.py")), \
                     patch.object(rt, "session_env", return_value=env):
                    rt.check_display()
                    print("PASS: actual Qt Wayland backend probe", flush=True)
                    for scenario in ("waiting", "recognized", "bitedj-process-present"):
                        dj = None
                        if scenario == "bitedj-process-present":
                            # A unique, disposable argv[0] matches the configured DJ path.
                            dj = subprocess.Popen([str(fake), "-c", "import time;time.sleep(60)"],
                                                  executable=sys.executable)
                            clients.append(dj)
                        token = rt.request_view()
                        args = [sys.executable, str(root / "bitedj_launcher.py")]
                        if scenario != "waiting":
                            args.append("--demo-controller")
                        gui = subprocess.Popen(args, env=env, cwd=root, stdout=output, stderr=output)
                        clients.append(gui)
                        until(lambda: rt.view_ready(token))
                        assert gui.poll() is None
                        print(f"PASS: {scenario}: live process + exposed, painted window acknowledgement",
                              flush=True)
                        again = rt.request_view()
                        until(lambda: rt.view_ready(again))
                        assert rt.read_runtime("view-ready.json")["pid"] == gui.pid
                        print(f"PASS: {scenario}: repeated icon request reused the visible instance",
                              flush=True)
                        gui.terminate()
                        gui.wait(timeout=5)
                        if dj:
                            dj.terminate()
                            dj.wait(timeout=5)
            except BaseException:
                print(log.read_text(errors="replace")[-6000:], file=sys.stderr)
                raise
            finally:
                for proc in reversed(clients + [compositor]):
                    if proc.poll() is None:
                        proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()


if __name__ == "__main__":
    main()
