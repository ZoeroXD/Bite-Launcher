#!/usr/bin/env python3
"""Only manage the Pi firmware rainbow setting; never touch cmdline or initramfs.

Run through sudo at install/uninstall time, not at launcher startup. An original
backup lives beside config.txt; a root-owned record protects reinstalls/restores.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

BEGIN = "# Bite Launcher: firmware rainbow off BEGIN"
END = "# Bite Launcher: firmware rainbow off END"
MAX_BYTES = 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_safe(path):
    """Reject symlinks and files writable by another user before root reads/writes."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError(f"Unsafe owner, permissions or file type: {path}")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"File too large: {path}")
    return data


def atomic_write(path, data, mode=0o600):
    """Same-directory replacement; retain an independent original backup."""
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # The replace is atomic; sync its directory as well where supported.
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def locate(root=Path("/"), mounted=os.path.ismount):
    """Fixture injection is internal only; CLI always uses the actual machine."""
    model = root / "proc/device-tree/model"
    if not model.exists():
        model = root / "sys/firmware/devicetree/base/model"
    if not model.exists() or b"Raspberry Pi" not in model.read_bytes():
        return None
    for relative in ("boot/firmware/config.txt", "boot/config.txt"):
        path = root / relative
        if path.exists() or path.is_symlink():
            if not mounted(path.parent) or path.parent.is_symlink():
                raise ValueError(f"Boot partition not verified mounted: {path.parent}")
            info = path.parent.stat()
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise ValueError(f"Unsafe boot-directory ownership or permissions: {path.parent}")
            read_safe(path)
            return path
    raise ValueError("Raspberry Pi detected but firmware config.txt was not found")


def rainbow_already_off(data):
    """Only reuse a final unconditional 1 with no later possible override."""
    text = data.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("Invalid NUL in config.txt")
    unconditional, known_off = True, False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("["):
            unconditional = line.lower() == "[all]"
        elif re.match(r"include\s", line):
            known_off = False  # Do not interpret or edit included files.
        elif re.match(r"disable_splash\s*=", line):
            value = line.split("=", 1)[1].strip()
            if unconditional and value == "1":
                known_off = True
            elif value != "1":
                known_off = False
    return known_off


def state_directory(path, create=False):
    if path.is_symlink():
        raise ValueError(f"State directory is a symlink: {path}")
    if not path.exists():
        if not create:
            return
        path.mkdir(mode=0o700)
    info = path.stat()
    if not path.is_dir() or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError(f"Unsafe state directory: {path}")


def load_record(state, config):
    state_directory(state.parent)
    if not state.exists() and not state.is_symlink():
        return None
    record = json.loads(read_safe(state))
    if record.get("config") != str(config) or record.get("version") != 1:
        raise ValueError("Rainbow record does not match the current firmware file")
    backup = record.get("backup", "")
    if not backup.startswith("config.txt.bite-rainbow-") or Path(backup).name != backup:
        raise ValueError("Invalid rainbow backup reference")
    for key in ("before", "after"):
        if not re.fullmatch(r"[0-9a-f]{64}", record.get(key, "")):
            raise ValueError("Invalid rainbow record hashes")
    return record


def plan(config, state):
    before = read_safe(config)
    record = load_record(state, config)
    if record:
        if digest(read_safe(config.parent / record["backup"])) != record["before"]:
            raise ValueError("Rainbow backup changed; review before retrying")
        if digest(before) != record["after"]:
            raise ValueError("config.txt changed since rainbow setup; preserve it and review "
                             f"before retrying. Backup: {config.parent / record['backup']}")
        return before, before, "Rainbow already off (managed); no changes"
    if BEGIN.encode() in before or END.encode() in before:
        raise ValueError("Existing rainbow block has no matching record; review it first")
    if rainbow_already_off(before):
        return before, before, "Rainbow already off; preserving the existing setting"
    newline = b"\r\n" if b"\r\n" in before else b"\n"
    separator = b"" if not before or before.endswith(b"\n") else newline
    block = newline.join(item.encode() for item in (BEGIN, "[all]", "disable_splash=1", END, ""))
    return before, before + separator + block, "Will disable only the firmware rainbow"


def apply(config, state):
    before, after, message = plan(config, state)
    if before == after:
        return message
    state_directory(state.parent, create=True)
    mode = stat.S_IMODE(config.stat().st_mode)
    # Unique, never-overwritten backup beside the real config on the boot partition.
    fd, name = tempfile.mkstemp(prefix="config.txt.bite-rainbow-", suffix=".bak", dir=config.parent)
    with os.fdopen(fd, "wb") as stream:
        stream.write(before)
        stream.flush()
        os.fsync(stream.fileno())
    backup = Path(name)
    record = {"version": 1, "config": str(config), "backup": backup.name,
              "before": digest(before), "after": digest(after)}
    atomic_write(state, (json.dumps(record, indent=2) + "\n").encode())
    if read_safe(config) != before:
        state.unlink()
        raise ValueError(f"config.txt changed while preparing the backup; left untouched. Backup: {backup}")
    try:
        atomic_write(config, after, mode)
        if read_safe(config) != after:
            raise OSError("Firmware setting verification failed")
    except Exception as error:
        try:
            atomic_write(config, before, mode)
            state.unlink()
        except Exception as rollback_error:
            raise RuntimeError(f"Rainbow write and rollback failed. Restore {backup} to {config}; "
                               f"original error: {error}; rollback: {rollback_error}") from error
        raise RuntimeError(f"Rainbow change failed; original restored. Backup: {backup}") from error
    return f"Rainbow disabled for next reboot. Original backup: {backup}"


def restore(config, state):
    record = load_record(state, config)
    if not record:
        return "No managed rainbow change to restore; existing setting preserved"
    current = read_safe(config)
    if digest(current) == record["before"]:
        state.unlink()
        return "Original config already present; cleared interrupted rainbow record"
    if digest(current) != record["after"]:
        raise ValueError("config.txt has later edits; refusing to overwrite them. "
                         f"Review backup: {config.parent / record['backup']}")
    backup = config.parent / record["backup"]
    before = read_safe(backup)
    if digest(before) != record["before"]:
        raise ValueError("Rainbow backup changed; refusing to restore")
    atomic_write(config, before, stat.S_IMODE(config.stat().st_mode))
    state.unlink()
    return f"Previous firmware configuration restored. Backup retained: {backup}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Read-only check; no files created")
    group.add_argument("--restore", action="store_true", help="Restore only our unchanged managed edit")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Run with sudo; the launcher itself must not run as root.\n")
    try:
        config = locate()
        if config is None:
            print("Rainbow setting skipped: not a verified Raspberry Pi")
            return
        state = Path("/var/lib/bite-launcher/rainbow.json")
        if args.check:
            print(plan(config, state)[2] + " (read-only)")
        elif args.restore:
            print(restore(config, state))
        else:
            print(apply(config, state))
    except (OSError, ValueError, RuntimeError, TypeError, AttributeError) as error:
        parser.exit(1, f"STOP: {error}\n")


if __name__ == "__main__":
    main()
