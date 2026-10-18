"""Shared settings and narrowly targeted process operations. No background service."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import secrets
import subprocess
import time

HOME = Path.home()
INSTALL = Path("/opt/bitedj-launcher")
STATE = HOME / ".config/bite-launcher"
OFF = STATE / "autostart-off"
RUN = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "bite-launcher"
APP = str(INSTALL / "bitedj_launcher.py")
SESSION = str(INSTALL / "bite_session.py")
PROFILE = INSTALL / "session.json"
VIEW_PROTOCOL = 2


def lite_mode():
    try:
        value = json.loads(PROFILE.read_text())
        return value.get("kind") == "lite" and value.get("uid") == os.getuid()
    except (OSError, ValueError, AttributeError):
        return False


def config():
    values = {}
    try:
        for line in Path(os.environ.get("DJRIG_CONF", "/etc/djrig.conf")).read_text().splitlines():
            parts = shlex.split(line, comments=True)
            if parts and "=" in parts[0]:
                key, value = " ".join(parts).split("=", 1)
                values[key] = value
    except (OSError, ValueError):
        pass
    return values


def app_path():
    # Never silently prefer another Mixxx installation over the user's build.
    return os.path.expanduser(config().get("BITEDJ_APP", "~/bitedj/build/mixxx"))


def ensure_runtime():
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    return RUN


def write_runtime(name, payload):
    path = ensure_runtime() / name
    temporary = path.with_name(path.name + f".{os.getpid()}.new")
    temporary.write_text(json.dumps(payload))
    temporary.replace(path)


def request_view():
    token = secrets.token_hex(16)
    write_runtime("view-request.json", {"protocol": VIEW_PROTOCOL, "token": token})
    return token


def read_runtime(name):
    try:
        value = json.loads((RUN / name).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def view_ready(token):
    value = read_runtime("view-ready.json")
    return (value.get("protocol") == VIEW_PROTOCOL and value.get("token") == token
            and value.get("painted") is True and value.get("pid") in matching("launcher"))


def check_display():
    """Probe the real Qt backend before creating a backdrop or hiding the desktop."""
    env = session_env()
    env["QT_QPA_PLATFORM"] = "wayland"
    try:
        probe = subprocess.run(
            ["/usr/bin/python3", "-c",
             "from PyQt6.QtWidgets import QApplication; app=QApplication([])"],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot open the graphical session: {exc}") from exc
    if probe.returncode:
        raise RuntimeError("Qt Wayland could not start; desktop left unchanged.\n"
                           + probe.stderr[-3000:])


def report_error(message, notification="Could not open the launcher. See ~/.cache/bite-launcher.log."):
    log = HOME / ".cache/bite-launcher.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as output:
        output.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    try:
        subprocess.run(["notify-send", "--app-name=Bite Launcher", "Bite Launcher",
                        notification],
                       env=session_env(), timeout=3, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        pass


@contextlib.contextmanager
def lock(name):
    path = ensure_runtime() / (name + ".lock")
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"{name}: already running or switching") from None
        yield


def processes():
    """Only this user's processes, parsed as argv, never broad pkill patterns."""
    result = {}
    for item in Path("/proc").iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            if item.stat().st_uid != os.getuid():
                continue
            argv = item.joinpath("cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            if argv and argv[0]:
                result[int(item.name)] = [x.decode(errors="replace") for x in argv]
        except (OSError, ProcessLookupError):
            continue
    return result


def role(argv, name):
    exe = Path(argv[0]).name
    script = argv[1] if len(argv) > 1 else ""
    if name == "launcher":
        return exe.startswith("python") and script == APP
    if name == "lite-session":
        return (exe.startswith("python") and script == str(INSTALL / "bite_lite.py")
                and argv[2:] == ["session"])
    if name == "wrapper":
        return ((exe.startswith("python") and script == SESSION and argv[2:3] == ["wrapper"])
                or argv[0] == "/usr/local/bin/bitedj-launcher"
                or (exe in ("sh", "bash", "dash") and script == "/usr/local/bin/bitedj-launcher"))
    if name == "bitedj":
        return os.path.realpath(argv[0]) == os.path.realpath(app_path())
    if name == "backdrop":
        return exe == "swaybg" and "-c" in argv and argv[argv.index("-c") + 1:][:1] == ["#000000"]
    if name == "desktop-child":
        return ((exe == "pcmanfm" and "--desktop" in argv)
                or (exe == "swaybg" and "-m" in argv))
    if name == "desktop-respawn":
        # lwrespawn is commonly a shell script; only its desktop/panel instances.
        is_respawn = exe == "lwrespawn" or Path(script).name == "lwrespawn"
        return is_respawn and any(Path(x).name in ("pcmanfm-pi", "wf-panel-pi") for x in argv[1:])
    if name == "legacy-listener":
        return exe.startswith("python") and script == str(HOME / "bin/bitedj-hold.py")
    if name == "flx4-reader":
        return exe == "aseqdump" and argv[1:] == ["-p", "DDJ-FLX4"]
    return exe == name or (exe in ("sh", "bash", "dash") and Path(script).name == name)


def matching(name):
    return {pid: argv for pid, argv in processes().items() if role(argv, name)}


def stop(name, timeout=3):
    targets = matching(name)
    for pid, argv in targets.items():
        if processes().get(pid) == argv:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        current = processes()
        if not any(current.get(pid) == argv for pid, argv in targets.items()):
            return
        time.sleep(0.1)
    # Only force the exact original processes still present.
    current = processes()
    for pid, argv in targets.items():
        if current.get(pid) == argv:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def session_env():
    env = dict(os.environ)
    # Import the active compositor client's session, not a hard-coded wayland-0.
    owned = processes()
    for name in ("wf-panel-pi", "launcher", "desktop-child", "labwc"):
        pid = next((p for p, a in owned.items() if role(a, name)), None)
        if pid:
            try:
                for raw in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
                    key, sep, value = raw.partition(b"=")
                    k = key.decode(errors="replace")
                    if sep and (k in ("WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS",
                                      "XDG_RUNTIME_DIR", "LABWC_PID", "XDG_CURRENT_DESKTOP")
                                or k.startswith(("XCURSOR_", "GTK_"))):
                        env[k] = value.decode(errors="replace")
                if env.get("WAYLAND_DISPLAY"):
                    break
            except OSError:
                pass
    runtime = Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    env["XDG_RUNTIME_DIR"] = str(runtime)
    display = env.get("WAYLAND_DISPLAY", "")
    socket = Path(display) if display.startswith("/") else runtime / display
    if not display or not socket.is_socket():
        sockets = sorted(p for p in runtime.glob("wayland-*") if p.is_socket())
        if sockets:
            env["WAYLAND_DISPLAY"] = sockets[0].name
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env


def spawn(argv, env=None, cwd=None):
    log = HOME / ".cache/bite-launcher.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > 1_000_000:
        log.replace(log.with_suffix(".log.previous"))
    with log.open("ab") as output:
        return subprocess.Popen(argv, env=env or session_env(), cwd=cwd,
                                stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                start_new_session=True, close_fds=True)


def start_bitedj():
    with lock("bitedj-start"):
        if matching("bitedj"):
            return None
        path = app_path()
        if not os.path.isabs(path) or not os.access(path, os.X_OK):
            raise RuntimeError(f"BiteDJ executable not found: {path}")
        env = session_env()
        env.pop("QT_QPA_PLATFORM", None)
        settings = config()
        if settings.get("DJRIG_QPA"):
            env["QT_QPA_PLATFORM"] = settings["DJRIG_QPA"]
        if settings.get("DJRIG_BITEDJ_SCALE"):
            env["QT_SCALE_FACTOR"] = settings["DJRIG_BITEDJ_SCALE"]
        return spawn([path, "--fullScreen"], env=env, cwd=str(Path(path).parent))
