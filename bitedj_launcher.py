#!/usr/bin/env python3
"""BiteDJ Launcher - a minimal, black, touch-first home screen for the Raspberry Pi DJ rig.

* Hides every trace of the Raspberry Pi desktop: no panel, no menu, no wallpaper.
* Keeps a tiny system tray: Ethernet, Wi-Fi and the clock (tap it for network details).
* Asks for a BiteDJ-supported controller in the centre of the screen.
* A striped shark fin (Bite DJ logo colours) swims along the bottom edge, left->right->left.
* When a supported controller is plugged in: "Controller Recognized" + a purple Launch button.

Runs under labwc (Wayland). Requires only python3-pyqt6.
"""
import argparse
import fcntl
import math
import os
import re
import shlex
import socket
import struct
import sys
import time
import xml.etree.ElementTree as ET

from PyQt6.QtCore import QElapsedTimer, QPointF, QProcess, QProcessEnvironment, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QGuiApplication, QImage, QPainter,
                         QPainterPath, QPen, QPixmap)
from PyQt6.QtWidgets import QApplication, QWidget

# --------------------------------------------------------------------------- palette (from Bite-DJ-logo.png)
CHARCOAL = "#231f20"
GRAY = "#44403e"
RED = "#c8372d"
ORANGE = "#f15a22"
BLUE = "#27aae1"
PURPLE = "#9d55e2"
# Logo stripe order, top -> bottom, with relative band heights measured from the logo
STRIPES = [(CHARCOAL, 11), (GRAY, 15), (RED, 15), (ORANGE, 15), (BLUE, 15), (PURPLE, 15), (GRAY, 15), (CHARCOAL, 11)]

CONF_FILE = os.environ.get("DJRIG_CONF", "/etc/djrig.conf")
DEFAULT_CONTROLLER_DIRS = [
    "/opt/bitedj/share/mixxx/controllers",   # mixxxpi64-ng-bitedj image install prefix
    "/opt/bitedj/share/bitedj/controllers",
    "~/bitedj/res/controllers",               # source checkout (BiteDJ-On-Off-Macro layout)
    "~/.bitedj/controllers",                  # user mappings
    "~/.mixxx/controllers",
]
# Internal/virtual sound and MIDI devices that must never count as a DJ controller
IGNORE = re.compile(r"vc4hdmi|vc4-hdmi|bcm2835|headphones|midi through|^system$|timer|announce|pipewire|"
                    r"usb2\.0 hub|root hub|xhci|hub$", re.I)


# Mapping IDs that are only a brand or a generic word would match unsupported gear
GENERIC = {"numark", "reloop", "vestax", "gemini", "pioneer", "denon", "hercules", "behringer", "akai", "korg",
           "novation", "roland", "traktor", "mixer", "mixer1", "midi", "controller", "usbmidi", "usbaudio"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def load_conf(path=CONF_FILE):
    conf = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip()
                    try:
                        v = " ".join(shlex.split(v))
                    except ValueError:
                        pass
                    conf[k.strip()] = v
    except OSError:
        pass
    return conf


def controller_dirs(conf):
    """Mapping folders to search. Stock Mixxx mappings are added when DJRIG_APP=mixxx."""
    custom = os.environ.get("BITEDJ_CONTROLLER_DIRS") or conf.get("BITEDJ_CONTROLLER_DIRS")
    if custom:
        return custom.split(":")
    dirs = list(DEFAULT_CONTROLLER_DIRS)
    if conf.get("DJRIG_APP", "bitedj") == "mixxx":
        dirs += ["/usr/share/mixxx/controllers", "~/.mixxx/controllers"]
    return dirs


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


# --------------------------------------------------------------------------- controller detection
class ControllerCatalog:
    """Every controller BiteDJ has a mapping for, read from the installed *.midi.xml / *.hid.xml files."""

    # Used only if no mapping directory can be found (e.g. testing on a laptop)
    FALLBACK = [("Pioneer DDJ-FLX4", ["ddjflx4", "pioneerddjflx4"], [])]

    def __init__(self, dirs):
        self.entries = []          # (display name, [name tokens], [(vid, pid)])
        for d in dirs:
            d = os.path.expanduser(d)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if fn.endswith((".midi.xml", ".hid.xml", ".bulk.xml")):
                    self._load(os.path.join(d, fn))
        if not self.entries:
            self.entries = list(self.FALLBACK)

    def _load(self, path):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError):
            return
        name = (root.findtext("info/name") or "").strip()
        tokens = set()
        for raw in [name] + [c.get("id", "") for c in root.iter("controller")]:
            t = norm(raw)
            t = re.sub(r"(midi\d*|port\d*|\d+)$", lambda m: "" if m.group(0).startswith(("midi", "port")) else m.group(0), t)
            if len(t) >= 4 and t not in GENERIC:
                tokens.add(t)
        usb = set()
        for p in root.iter("product"):
            try:
                usb.add((int(p.get("vendor_id"), 16), int(p.get("product_id"), 16)))
            except (TypeError, ValueError):
                pass
        if tokens or usb:
            self.entries.append((name or os.path.basename(path).split(".")[0], sorted(tokens, key=len, reverse=True), sorted(usb)))

    def match(self, devices):
        """devices: list of (label, (vid, pid) or None). Returns (mapping name, device label) or None."""
        best = None
        for label, usbid in devices:
            if IGNORE.search(label or ""):
                continue
            n = norm(label)
            for name, tokens, usb in self.entries:
                score = 0
                if usbid and usbid in usb:
                    score = 1000
                else:
                    for t in tokens:
                        if t in n:
                            score = len(t)
                            break
                if score and (best is None or score > best[0]):
                    best = (score, name, label)
        return (best[1], best[2]) if best else None


def connected_devices():
    """USB devices, ALSA cards and ALSA sequencer (MIDI) clients currently present."""
    devs = []
    base = "/sys/bus/usb/devices"
    if os.path.isdir(base):
        for d in os.listdir(base):
            p = os.path.join(base, d)
            vid, pid = read(p + "/idVendor"), read(p + "/idProduct")
            if not vid or read(p + "/bDeviceClass") == "09":   # skip hubs
                continue
            try:
                usbid = (int(vid, 16), int(pid, 16))
            except ValueError:
                usbid = None
            label = (read(p + "/manufacturer") + " " + read(p + "/product")).strip()
            devs.append((label or f"{vid}:{pid}", usbid))
    lines = read("/proc/asound/cards").splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"\s*\d+\s+\[(.+?)\s*\]:\s*\S+\s+-\s+(.*)", line)
        if m:
            longname = lines[i + 1].strip() if i + 1 < len(lines) else ""
            devs.append((f"{m.group(2)} {longname} {m.group(1)}", None))
    for m in re.finditer(r'Client\s+\d+\s*:\s*"(.+?)"\s*\[Kernel\]', read("/proc/asound/seq/clients")):
        devs.append((m.group(1), None))
    return devs


# --------------------------------------------------------------------------- network status
def iface_ip(ifname):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, struct.pack("256s", ifname[:15].encode()))[20:24])
    except OSError:
        return ""
    finally:
        s.close()


class Network:
    def __init__(self):
        self.wifi_iface = self.eth_iface = None
        self.wifi_up = self.eth_up = False
        self.wifi_signal = 0          # 0..100
        self.ssid = ""
        self.wifi_ip = self.eth_ip = ""
        self._nm = QProcess()
        self._nm.finished.connect(self._nm_done)

    def refresh(self):
        self.wifi_iface = self.eth_iface = None
        for n in sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else []:
            if os.path.isdir(f"/sys/class/net/{n}/wireless") and not self.wifi_iface:
                self.wifi_iface = n
            elif n.startswith(("eth", "en")) and not self.eth_iface:
                self.eth_iface = n
        self.eth_up = bool(self.eth_iface) and read(f"/sys/class/net/{self.eth_iface}/carrier") == "1"
        self.eth_ip = iface_ip(self.eth_iface) if self.eth_up else ""
        self.wifi_up, self.wifi_signal, self.wifi_ip = False, 0, ""
        if self.wifi_iface:
            for line in read("/proc/net/wireless").splitlines()[2:]:
                parts = line.split()
                if parts and parts[0].rstrip(":") == self.wifi_iface:
                    try:
                        self.wifi_signal = max(0, min(100, int(float(parts[2]) / 70 * 100)))
                    except (IndexError, ValueError):
                        pass
            self.wifi_ip = iface_ip(self.wifi_iface)
            self.wifi_up = read(f"/sys/class/net/{self.wifi_iface}/operstate") == "up" and bool(self.wifi_ip)
        if not self.wifi_up:
            self.ssid = ""
        elif self._nm.state() == QProcess.ProcessState.NotRunning:
            # Ask NetworkManager for the SSID without blocking the UI
            self._nm.start("nmcli", ["-t", "-f", "ACTIVE,SSID", "device", "wifi", "list", "--rescan", "no"])

    def _nm_done(self):
        out = bytes(self._nm.readAllStandardOutput()).decode(errors="ignore")
        for line in out.splitlines():
            if line.startswith("yes:"):
                self.ssid = line[4:].replace("\\:", ":")
                return


# --------------------------------------------------------------------------- shark fin artwork
# Shape constants measured from the "D" in Bite-DJ-logo.png
D_EXPONENT = 2.9          # best fit to the D's outer bowl (superellipse: squarer than a circle, rounder than a box)
D_ASPECT = 1.41           # the D's bowl is 1.41x as wide (84 px) as its half-height (59.5 px)
TIP_X = 0.05              # sharp top-rear corner (the D's top-left corner)
TAIL_X = 0.30             # rear end of the base


def fin_path(w, h):
    """Dorsal fin facing right. The leading edge is the D's bowl curve; the rear edge carries the D's bite."""
    p = QPainterPath()
    cx, a, b = w * TIP_X, w * (1 - TIP_X), h
    p.moveTo(cx, 0)
    steps = 64
    for i in range(1, steps + 1):                       # quarter superellipse: tip -> front base
        th = (math.pi / 2) * (1 - i / steps)
        x = cx + a * math.cos(th) ** (2 / D_EXPONENT)
        y = h - b * math.sin(th) ** (2 / D_EXPONENT)
        p.lineTo(x, y)
    p.lineTo(w * TAIL_X, h)                             # base
    rear = (w * TAIL_X, h, w * 0.20, h * 0.60, w * 0.10, h * 0.28, cx, 0)
    p.cubicTo(*rear[2:])                                # rear edge, slightly concave
    p.closeSubpath()

    def rear_x(t):                                      # x on the rear edge at curve parameter t
        x0, _, x1, _, x2, _, x3, _ = rear
        return (1 - t) ** 3 * x0 + 3 * t * (1 - t) ** 2 * x1 + 3 * t * t * (1 - t) * x2 + t ** 3 * x3

    def rear_y(t):
        _, y0, _, y1, _, y2, _, y3 = rear
        return (1 - t) ** 3 * y0 + 3 * t * (1 - t) ** 2 * y1 + 3 * t * t * (1 - t) * y2 + t ** 3 * y3
    # One big bite made of three overlapping round lobes centred on the rear edge, like the D
    bite = QPainterPath()
    for t, r in ((0.30, 0.14), (0.48, 0.16), (0.66, 0.14)):
        lobe = QPainterPath()
        lobe.addEllipse(QPointF(rear_x(t) - h * 0.07, rear_y(t)), h * r, h * r)
        bite = bite.united(lobe)
    return p.subtracted(bite).simplified()


def render_fin(w, h, dpr, facing_right=True):
    img = QImage(int(w * dpr), int(h * dpr), QImage.Format.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(dpr)
    img.fill(Qt.GlobalColor.transparent)
    qp = QPainter(img)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not facing_right:
        qp.translate(w, 0)
        qp.scale(-1, 1)
    line = 2.0                                          # logo keyline: ~2 px of black
    white = 2.5                                         # logo ground: 2-3 px of white around the black
    pad = line + white + 1
    path = fin_path(w - 2 * pad, h - pad).translated(pad, pad)
    qp.setBrush(Qt.BrushStyle.NoBrush)
    pen = QPen(QColor("#ffffff"), line + 2 * white)     # white ring first...
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    qp.setPen(pen)
    qp.drawPath(path)
    qp.save()                                           # ...then the stripes cover its inner half...
    qp.setClipPath(path)
    total = sum(s for _, s in STRIPES)
    y = pad
    for color, size in STRIPES:
        bh = (h - pad) * size / total
        qp.fillRect(QRectF(0, y, w, bh + 1), QColor(color))
        y += bh
    qp.restore()
    pen.setColor(QColor("#000000"))                     # ...and the black keyline sits on the edge
    pen.setWidthF(line)
    qp.setPen(pen)
    qp.drawPath(path)
    qp.end()
    return QPixmap.fromImage(img)


# --------------------------------------------------------------------------- main screen
WAITING, RECOGNIZED, RUNNING = "waiting", "recognized", "running"


class Desktop(QWidget):
    def __init__(self, args, conf):
        super().__init__()
        self.args, self.conf = args, conf
        self.setWindowTitle("Bite Launcher")
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.CursorShape.BlankCursor if not args.windowed else Qt.CursorShape.ArrowCursor)

        dirs = controller_dirs(conf)
        self.catalog = ControllerCatalog(dirs)
        self.controller = None          # (mapping name, device label)
        self.state = WAITING
        self.prev_state, self.fade = WAITING, 1.0
        self.message = ""
        self.net = Network()
        self.net_open = False
        self.pressed = False
        self.proc = None
        self.launch_started = 0.0
        self.external_running = False

        self.clock = QElapsedTimer()
        self.clock.start()
        self.fin_cache = {}
        self.fin_rect = QRect()

        self.anim = QTimer(self, interval=16, timerType=Qt.TimerType.PreciseTimer)
        self.anim.timeout.connect(self.tick)
        self.anim.start()
        self.poll = QTimer(self, interval=1500)
        self.poll.timeout.connect(self.scan)
        self.poll.start()
        self.slow = QTimer(self, interval=4000)
        self.slow.timeout.connect(self.refresh_net)
        self.slow.start()
        self.sec = QTimer(self, interval=1000)
        self.sec.timeout.connect(lambda: self.update(self.tray_rect().adjusted(-300, 0, 0, 0)))
        self.sec.start()
        if not args.demo_controller:
            self.scan()
        else:
            self.set_controller(("Pioneer DDJ-FLX4", "Pioneer DDJ-FLX4"))
        self.refresh_net()

    # ---------------------------------------------------------------- geometry helpers
    @property
    def u(self):
        """Layout unit: 1% of screen height (8 px on the 1280x800 panel)."""
        return max(4.0, self.height() / 100.0)

    def font(self, size, weight=QFont.Weight.Normal, spacing=0.0):
        f = QFont()
        f.setFamilies(["Inter", "Open Sans", "DejaVu Sans", "Sans Serif"])
        f.setPixelSize(max(8, int(size)))
        f.setWeight(weight)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        return f

    def tray_rect(self):
        u = self.u
        return QRect(int(self.width() - 34 * u), 0, int(34 * u), int(6 * u))

    def button_rect(self):
        u = self.u
        w, h = 30 * u, 9 * u
        return QRectF(self.width() / 2 - w / 2, self.height() * 0.5 + 7.5 * u, w, h)

    def center_rect(self):
        u = self.u
        return QRect(0, int(self.height() * 0.5 - 20 * u), self.width(), int(40 * u))

    # ---------------------------------------------------------------- state
    def set_state(self, s):
        if s != self.state:
            self.prev_state, self.state, self.fade = self.state, s, 0.0
            if s == RUNNING:
                self.net_open = False
            self.update()

    def set_controller(self, c):
        changed = (c or (None,))[0] != (self.controller or (None,))[0]
        self.controller = c
        if self.state != RUNNING:
            self.set_state(RECOGNIZED if c else WAITING)
        if changed:
            self.update()
            if c and self.conf.get("BITEDJ_LAUNCHER_AUTOLAUNCH", "0") == "1" and self.state == RECOGNIZED:
                QTimer.singleShot(1200, self.launch)

    def scan(self):
        if not self.args.demo_controller:
            self.set_controller(self.catalog.match(connected_devices()))
        # BiteDJ may also be started by the FLX4 hold-button macro or over SSH
        ext = self.bitedj_pid() is not None and self.proc is None
        if ext != self.external_running:
            self.external_running = ext
            if ext:
                self.set_state(RUNNING)
            elif self.proc is None:
                self.set_state(RECOGNIZED if self.controller else WAITING)

    def refresh_net(self):
        if self.state != RUNNING:
            self.net.refresh()
            self.update(self.tray_rect().adjusted(-300, 0, 0, 0))
            if self.net_open:
                self.update(self.net_panel_rect().toAlignedRect().adjusted(-4, -4, 4, 4))

    def app_cmd(self):
        app = self.conf.get("DJRIG_APP", "bitedj")
        for cand in (app, "/usr/local/bin/bitedj", "/opt/bitedj/bin/bitedj", "/opt/bitedj/bin/mixxx",
                     os.path.expanduser("~/bitedj/build/mixxx"), "mixxx"):
            path = cand if os.path.isabs(cand) else next((os.path.join(d, cand) for d in os.environ.get("PATH", "").split(":")
                                                         if os.access(os.path.join(d, cand), os.X_OK)), None)
            if path and os.access(path, os.X_OK):
                return path
        return None

    def bitedj_pid(self):
        cmd = self.app_cmd()
        if not cmd:
            return None
        targets = {os.path.realpath(cmd), cmd}
        for pid in os.listdir("/proc"):
            if pid.isdigit() and int(pid) != os.getpid():
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        argv0 = f.read().split(b"\0", 1)[0].decode(errors="ignore")
                except OSError:
                    continue
                if argv0 and (argv0 in targets or os.path.realpath(argv0) in targets):
                    return int(pid)
        return None

    def launch(self):
        if self.state == RUNNING or self.proc is not None:
            return
        cmd = self.app_cmd()
        if not cmd:
            self.message = "BiteDJ is not installed"
            self.update()
            return
        env = QProcessEnvironment.systemEnvironment()
        env.insert("QT_QPA_PLATFORM", self.conf.get("DJRIG_QPA", "wayland"))
        if "mixxx" not in os.path.basename(os.path.realpath(cmd)) or "bitedj" in cmd:
            env.insert("QT_SCALE_FACTOR", self.conf.get("DJRIG_BITEDJ_SCALE", "1.6"))
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(os.path.dirname(os.path.realpath(cmd)))
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        self.proc.finished.connect(self.on_finished)
        self.proc.errorOccurred.connect(lambda e: e == QProcess.ProcessError.FailedToStart and self.on_finished(-1, None))
        self.message = ""
        self.launch_started = time.monotonic()
        self.proc.start(cmd, ["--fullScreen"])
        self.set_state(RUNNING)

    def on_finished(self, code, _status):
        quick = time.monotonic() - self.launch_started < 20
        self.proc = None
        name = "Mixxx" if self.conf.get("DJRIG_APP") == "mixxx" else "BiteDJ"
        self.message = f"{name} closed unexpectedly. Tap Launch to try again." if (quick and code != 0) else ""
        self.set_state(RECOGNIZED if self.controller else WAITING)
        self.scan()

    # ---------------------------------------------------------------- animation
    def tick(self):
        if self.state == RUNNING and self.fade >= 1.0:
            return       # BiteDJ is on screen: spend zero CPU here
        if self.fade < 1.0:
            self.fade = min(1.0, self.fade + 0.016 / 0.35)
        self.update(self.center_rect())
        new = self.fin_geometry()[0]
        self.update(self.fin_rect.united(new).adjusted(-2, -2, 2, 2))
        self.fin_rect = new

    def fin_geometry(self):
        """Fin rect + direction. Swims left->right, disappears, then right->left, and repeats."""
        W, H = self.width(), self.height()
        fh = H * 0.15
        fw = fh * D_ASPECT / (1 - TIP_X)          # keep the D's exact width-to-height ratio
        swim = float(self.conf.get("BITEDJ_FIN_SECONDS", "30"))
        pause = 4.0
        span = W + fw * 2
        t = self.clock.elapsed() / 1000.0
        cycle = (swim + pause) * 2
        ph = t % cycle
        right = ph < swim + pause
        p = min(1.0, (ph if right else ph - swim - pause) / swim)
        x = -fw + p * span - fw if right else W + fw - p * span
        sink = fh * 0.06                                        # base sits just below the bottom edge
        y = H - fh + sink                                      # steady glide, no bobbing
        return QRect(int(x), int(y), int(fw + 1), int(fh + 1)), right, fw, fh

    # ---------------------------------------------------------------- painting
    def paintEvent(self, ev):
        qp = QPainter(self)
        qp.setRenderHint(QPainter.RenderHint.Antialiasing)
        qp.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        qp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        qp.fillRect(ev.rect(), Qt.GlobalColor.black)
        if self.state == RUNNING and self.fade >= 1.0:
            return
        self.paint_tray(qp)
        t = self.clock.elapsed() / 1000.0
        if self.fade < 1.0:
            self.paint_center(qp, self.prev_state, 1.0 - self.fade, t)
        self.paint_center(qp, self.state, self.fade, t)
        self.paint_fin(qp)
        if self.net_open:
            self.paint_net_panel(qp)

    def paint_fin(self, qp):
        rect, right, fw, fh = self.fin_geometry()
        dpr = self.devicePixelRatioF()
        key = (int(fw), int(fh), right, dpr)
        if key not in self.fin_cache:
            self.fin_cache = {k: v for k, v in self.fin_cache.items() if k[:2] == key[:2]}
            self.fin_cache[key] = render_fin(fw, fh, dpr, right)
        qp.drawPixmap(rect.topLeft(), self.fin_cache[key])

    def paint_center(self, qp, state, alpha, t):
        if alpha <= 0.01 or state == RUNNING:
            return
        u, W, H = self.u, self.width(), self.height()
        cy = H * 0.5
        qp.save()
        qp.setOpacity(alpha)
        white, dim = QColor(240, 238, 236), QColor(150, 145, 142)
        if state == WAITING:
            self.paint_controller_icon(qp, QPointF(W / 2, cy - 11 * u), 7 * u, t)
            qp.setPen(white)
            qp.setFont(self.font(4.6 * u, QFont.Weight.DemiBold))
            qp.drawText(QRectF(0, cy - 3 * u, W, 7 * u), Qt.AlignmentFlag.AlignCenter, "No Controller Recognized")
            qp.setPen(dim)
            qp.setFont(self.font(2.4 * u))
            qp.drawText(QRectF(0, cy + 4 * u, W, 4 * u), Qt.AlignmentFlag.AlignCenter, "Plug in a DJ controller supported by BiteDJ")
            # three breathing dots
            for i in range(3):
                a = 0.25 + 0.75 * max(0.0, math.sin(t * 3.0 - i * 0.7))
                qp.setBrush(QColor(157, 85, 226, int(255 * a)))
                qp.setPen(Qt.PenStyle.NoPen)
                qp.drawEllipse(QPointF(W / 2 + (i - 1) * 3 * u, cy + 11 * u), 0.7 * u, 0.7 * u)
        else:
            name = self.controller[0] if self.controller else ""
            self.paint_check(qp, QPointF(W / 2, cy - 12 * u), 4 * u)
            qp.setPen(white)
            qp.setFont(self.font(4.6 * u, QFont.Weight.DemiBold))
            qp.drawText(QRectF(0, cy - 6 * u, W, 7 * u), Qt.AlignmentFlag.AlignCenter, "Controller Recognized")
            qp.setPen(dim)
            qp.setFont(self.font(2.6 * u))
            qp.drawText(QRectF(0, cy + 0.5 * u, W, 4 * u), Qt.AlignmentFlag.AlignCenter, name)
            r = self.button_rect()
            glow = 0.5 + 0.5 * math.sin(t * 2.2)
            for i in range(4, 0, -1):                       # soft purple glow
                qp.setPen(Qt.PenStyle.NoPen)
                qp.setBrush(QColor(157, 85, 226, int(14 * glow * (5 - i))))
                qp.drawRoundedRect(r.adjusted(-i * 0.6 * u, -i * 0.6 * u, i * 0.6 * u, i * 0.6 * u), r.height() / 2 + i, r.height() / 2 + i)
            qp.setBrush(QColor("#7b3fc0") if self.pressed else QColor(PURPLE))
            qp.drawRoundedRect(r, r.height() / 2, r.height() / 2)
            qp.setPen(QColor("white"))
            qp.setFont(self.font(3.4 * u, QFont.Weight.Bold, 0.5))
            qp.drawText(r, Qt.AlignmentFlag.AlignCenter, "Launch")
        if self.message:
            qp.setPen(QColor(ORANGE))
            qp.setFont(self.font(2.2 * u))
            qp.drawText(QRectF(0, cy + 18.5 * u, W, 4 * u), Qt.AlignmentFlag.AlignCenter, self.message)
        qp.restore()

    def paint_controller_icon(self, qp, c, s, t):
        """Simple two-platter DJ controller outline with a pulsing USB lead."""
        qp.save()
        pen = QPen(QColor(120, 115, 112), max(2.0, s * 0.07))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        qp.setPen(pen)
        qp.setBrush(Qt.BrushStyle.NoBrush)
        body = QRectF(c.x() - s * 1.1, c.y() - s * 0.45, s * 2.2, s * 0.9)
        qp.drawRoundedRect(body, s * 0.14, s * 0.14)
        for dx in (-0.62, 0.62):
            qp.drawEllipse(QPointF(c.x() + dx * s, c.y()), s * 0.28, s * 0.28)
        qp.drawLine(QPointF(c.x(), c.y() - s * 0.25), QPointF(c.x(), c.y() + s * 0.25))
        a = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(t * 3.0))
        pen.setColor(QColor(157, 85, 226, int(255 * a)))
        qp.setPen(pen)
        path = QPainterPath(QPointF(c.x(), body.top()))
        path.cubicTo(c.x(), body.top() - s * 0.35, c.x() + s * 0.5, body.top() - s * 0.25, c.x() + s * 0.5, body.top() - s * 0.6)
        qp.drawPath(path)
        qp.restore()

    def paint_check(self, qp, c, r):
        qp.save()
        qp.setPen(QPen(QColor(BLUE), r * 0.14))
        qp.setBrush(Qt.BrushStyle.NoBrush)
        qp.drawEllipse(c, r, r)
        pen = QPen(QColor(BLUE), r * 0.18)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        qp.setPen(pen)
        p = QPainterPath(QPointF(c.x() - r * 0.45, c.y() + r * 0.02))
        p.lineTo(c.x() - r * 0.12, c.y() + r * 0.35)
        p.lineTo(c.x() + r * 0.48, c.y() - r * 0.32)
        qp.drawPath(p)
        qp.restore()

    def paint_tray(self, qp):
        """Right-to-left: clock, Wi-Fi, Ethernet. Every item is centred on one line with equal gaps."""
        u = self.u
        fg, off = QColor(225, 222, 220), QColor(95, 90, 88)
        mid = 3.0 * u                     # shared vertical centre line
        gap = 2.0 * u                     # space between items
        icon = 2.6 * u                    # icon box (square)
        x = self.width() - 2.4 * u        # right margin
        qp.save()
        qp.setFont(self.font(2.3 * u, QFont.Weight.Medium))
        now = time.strftime(self.conf.get("BITEDJ_CLOCK_FORMAT", "%-I:%M %p"))
        tw = QFontMetrics(qp.font()).horizontalAdvance(now)
        qp.setPen(fg)
        qp.drawText(QRectF(x - tw, mid - 2 * u, tw, 4 * u), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, now)
        x -= tw + gap

        # Wi-Fi: three arcs plus a dot, lit by signal strength, red cross when disconnected
        box = QRectF(x - icon, mid - icon / 2, icon, icon)
        n = self.net
        bars = 0 if not n.wifi_up else 1 + (n.wifi_signal > 30) + (n.wifi_signal > 55) + (n.wifi_signal > 75)
        pen = QPen(off, max(1.6, 0.26 * u))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        cx, cy = box.center().x(), box.bottom() - icon * 0.12
        for i in range(1, 4):
            pen.setColor(fg if bars > i else off)
            qp.setPen(pen)
            rr = icon * 0.28 * i
            qp.drawArc(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr), 45 * 16, 90 * 16)
        qp.setPen(Qt.PenStyle.NoPen)
        qp.setBrush(fg if bars else off)
        qp.drawEllipse(QPointF(cx, cy - icon * 0.02), icon * 0.08, icon * 0.08)
        if n.wifi_iface and not n.wifi_up:
            pen.setColor(QColor(RED))
            qp.setPen(pen)
            k = icon * 0.16
            c = QPointF(box.right() - k, box.bottom() - k)
            qp.drawLine(c + QPointF(-k, -k), c + QPointF(k, k))
            qp.drawLine(c + QPointF(k, -k), c + QPointF(-k, k))
        x -= icon + gap

        # Ethernet: one box on top linked to two below, lit when a cable is connected
        box = QRectF(x - icon, mid - icon / 2, icon, icon)
        pen = QPen(fg if n.eth_up else off, max(1.4, 0.2 * u))
        qp.setPen(pen)
        qp.setBrush(Qt.BrushStyle.NoBrush)
        bw, bh = icon * 0.34, icon * 0.26
        top = QRectF(box.center().x() - bw / 2, box.top() + icon * 0.06, bw, bh)
        left = QRectF(box.left() + icon * 0.04, box.bottom() - bh - icon * 0.06, bw, bh)
        right = QRectF(box.right() - bw - icon * 0.04, left.top(), bw, bh)
        for r in (top, left, right):
            qp.drawRoundedRect(r, 1.5, 1.5)
        bus = box.center().y() + icon * 0.04
        qp.drawLine(QPointF(top.center().x(), top.bottom()), QPointF(top.center().x(), bus))
        qp.drawLine(QPointF(left.center().x(), bus), QPointF(right.center().x(), bus))
        for r in (left, right):
            qp.drawLine(QPointF(r.center().x(), bus), QPointF(r.center().x(), r.top()))
        qp.restore()

    def net_panel_rect(self):
        u = self.u
        w, h = 40 * u, 21 * u
        return QRectF(self.width() - w - 1.5 * u, 6.5 * u, w, h)

    def paint_net_panel(self, qp):
        u = self.u
        r = self.net_panel_rect()
        qp.save()
        qp.setPen(QPen(QColor(60, 56, 54), 1))
        qp.setBrush(QColor(18, 16, 17, 245))
        qp.drawRoundedRect(r, 1.4 * u, 1.4 * u)
        n = self.net
        wifi = (f"{n.ssid or 'Connected'}  \u00b7  {n.wifi_signal}%" if n.wifi_up else ("Not connected" if n.wifi_iface else "No Wi-Fi adapter"))
        rows = [("Wi-Fi", wifi), ("Ethernet", "Connected" if n.eth_up else "Unplugged"),
                ("IP address", n.wifi_ip or n.eth_ip or "\u2014"), ("Hostname", socket.gethostname())]
        y = r.top() + 2 * u
        for k, v in rows:
            qp.setFont(self.font(1.9 * u))
            qp.setPen(QColor(140, 135, 132))
            qp.drawText(QRectF(r.left() + 2.2 * u, y, 14 * u, 4 * u), Qt.AlignmentFlag.AlignVCenter, k)
            qp.setFont(self.font(2.1 * u, QFont.Weight.Medium))
            qp.setPen(QColor(235, 232, 230))
            qp.drawText(QRectF(r.left() + 15 * u, y, r.width() - 17 * u, 4 * u), Qt.AlignmentFlag.AlignVCenter, v)
            y += 4.3 * u
        qp.restore()

    # ---------------------------------------------------------------- input (touch arrives as mouse events)
    def mousePressEvent(self, e):
        p = e.position()
        if self.state == RECOGNIZED and self.button_rect().contains(p):
            self.pressed = True
            self.update(self.button_rect().toAlignedRect().adjusted(-40, -40, 40, 40))

    def mouseReleaseEvent(self, e):
        p = e.position()
        was = self.pressed
        self.pressed = False
        if was:
            self.update(self.button_rect().toAlignedRect().adjusted(-40, -40, 40, 40))
            if self.button_rect().contains(p):
                self.launch()
            return
        if self.tray_rect().contains(p.toPoint()):
            self.net_open = not self.net_open
            self.refresh_net()
        elif self.net_open:
            self.net_open = False
        self.update()

    def keyPressEvent(self, e):
        mods = e.modifiers()
        if e.key() == Qt.Key.Key_Q and mods & Qt.KeyboardModifier.ControlModifier and mods & Qt.KeyboardModifier.AltModifier:
            QApplication.quit()
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.state == RECOGNIZED:
            self.launch()


def render_icon(path, size=256):
    """App icon: the striped fin on a black rounded tile."""
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    qp = QPainter(img)
    qp.setRenderHint(QPainter.RenderHint.Antialiasing)
    qp.setBrush(QColor("#000000"))
    qp.setPen(QPen(QColor(60, 56, 54), size * 0.012))
    qp.drawRoundedRect(QRectF(size * 0.03, size * 0.03, size * 0.94, size * 0.94), size * 0.2, size * 0.2)
    fh, fw = size * 0.62, size * 0.62 * 1.3
    qp.setClipRect(QRectF(0, 0, size, size * 0.80))
    qp.drawPixmap(QPointF((size - fw) / 2, size * 0.80 - fh * 0.94), render_fin(fw, fh, 1.0, True))
    qp.setClipping(False)
    qp.setPen(QPen(QColor(PURPLE), size * 0.025, cap=Qt.PenCapStyle.RoundCap))
    qp.drawLine(QPointF(size * 0.18, size * 0.80), QPointF(size * 0.82, size * 0.80))
    qp.end()
    img.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--windowed", action="store_true", help="run in a 1280x800 window (testing on a PC)")
    ap.add_argument("--demo-controller", action="store_true", help="pretend a DDJ-FLX4 is connected")
    ap.add_argument("--snapshot", metavar="PNG", help="render one frame to a PNG and exit")
    ap.add_argument("--at", type=float, default=4.0, help="animation time (s) for --snapshot")
    ap.add_argument("--size", default="1280x800", help="window/snapshot size")
    ap.add_argument("--icon", metavar="PNG", help="write the app icon to a PNG and exit")
    ap.add_argument("--list-controllers", action="store_true", help="print supported controllers and what is plugged in")
    args = ap.parse_args()
    conf = load_conf()

    if args.icon:
        app = QGuiApplication(sys.argv)  # noqa: F841 (needed for QPainter)
        render_icon(args.icon)
        return

    if args.list_controllers:
        dirs = controller_dirs(conf)
        cat = ControllerCatalog(dirs)
        print(f"{len(cat.entries)} BiteDJ mappings loaded")
        devs = connected_devices()
        for label, usb in devs:
            print("  device:", label, "" if not usb else "%04x:%04x" % usb)
        print("match:", cat.match(devs))
        return

    if not args.snapshot:
        lock = open(f"/tmp/bitedj-launcher-{os.getuid()}.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("Bite Launcher is already running")
            return
        main.lock = lock          # keep the lock for the life of the process

    QGuiApplication.setDesktopFileName("bitedj-launcher")      # Wayland app_id for labwc window rules
    app = QApplication(sys.argv)
    app.setApplicationName("Bite Launcher")
    w = Desktop(args, conf)
    sw, sh = (int(v) for v in args.size.lower().split("x"))
    if args.snapshot:
        w.resize(sw, sh)
        w.clock.elapsed = lambda: int(args.at * 1000)
        w.fade = 1.0
        if os.environ.get("BITEDJ_SNAPSHOT_NET"):
            w.net.wifi_iface, w.net.wifi_up, w.net.wifi_signal, w.net.ssid = "wlan0", True, 78, "StudioNet"
            w.net.eth_iface, w.net.eth_up, w.net.wifi_ip = "eth0", False, "192.168.1.42"
            w.net_open = os.environ.get("BITEDJ_SNAPSHOT_NET") == "open"
        w.grab().save(args.snapshot)
        return
    if args.windowed:
        w.resize(sw, sh)
        w.show()
    else:
        # labwc's rc.xml rule fullscreens every new window; asking again would toggle it back.
        w.showFullScreen() if "labwc" not in os.environ.get("XDG_CURRENT_DESKTOP", "").lower() else w.show()
        QTimer.singleShot(1500, lambda: w.isFullScreen() or w.showFullScreen())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
