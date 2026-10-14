#!/usr/bin/env python3
"""Bundled FLX4 listener: both LOOP IN 3 s; LOOP OUT / 4 BEAT EXIT 8 s."""
import os
import re
import selectors
import signal
import subprocess
import time

import bite_runtime as rt

PATTERN = re.compile(r"Note (on|off)\s+(\d+), note (\d+)(?:, velocity (\d+))?")
ACTIONS = {16: (3.0, "start"), 17: (8.0, "stop"), 77: (8.0, "toggle")}


class Holds:
    """Pure gesture state: releasing either deck cancels; one action per hold."""
    def __init__(self):
        self.held = set()
        self.armed = {}

    def event(self, channel, note, on):
        if channel not in (0, 1) or note not in ACTIONS:
            return
        if on:
            self.held.add((channel, note))
        else:
            self.held.discard((channel, note))
            self.armed.pop(note, None)

    def tick(self, now):
        result = []
        for note, (duration, action) in ACTIONS.items():
            if not all((channel, note) in self.held for channel in (0, 1)):
                self.armed.pop(note, None)
            elif note not in self.armed:
                self.armed[note] = now + duration
            elif now >= self.armed[note]:
                result.append(action)
                self.armed[note] = float("inf")
        return result


def watch():
    holds = Holds()
    proc = subprocess.Popen(["stdbuf", "-oL", "aseqdump", "-p", "DDJ-FLX4"], stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0)
    children = []
    buffer = b""
    try:
        os.set_blocking(proc.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while proc.poll() is None:
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 65536)
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        match = PATTERN.search(line.decode(errors="replace"))
                        if match:
                            kind, channel, note, velocity = match.groups()
                            holds.event(int(channel), int(note), kind == "on" and velocity != "0")
                for action in holds.tick(time.monotonic()):
                    children.append(rt.spawn(["/usr/local/bin/bite-mode", action]))
                children = [c for c in children if c.poll() is None]
    finally:
        if proc.poll() is None:
            proc.terminate()
        proc.wait()
        proc.stdout.close()


def main():
    def shutdown(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    with rt.lock("flx4-listener"):
        while True:
            try:
                watch()
            except (OSError, ValueError) as exc:
                print(f"FLX4 listener: {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
