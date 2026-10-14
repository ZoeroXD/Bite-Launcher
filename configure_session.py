#!/usr/bin/env python3
"""Install/remove only Bite Launcher's labwc entries, preserving unrelated settings."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from configure_autostart import guard

COMMAND = "/usr/local/bin/bite-launcher-open toggle"
BOOT = "/usr/local/bin/bite-mode boot &   # bite-launcher: managed startup"


def local(tag):
    return tag.split("}")[-1]


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".bite-new")
    temporary.write_text(content)
    if path.exists():
        temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)


def configure(home, remove=False, system_rc=Path("/etc/xdg/labwc/rc.xml")):
    folder = home / ".config/labwc"
    folder.mkdir(parents=True, exist_ok=True)
    record = home / ".config/bite-launcher/installer.json"
    data = json.loads(record.read_text()) if record.exists() else {}
    rc = folder / "rc.xml"
    auto = folder / "autostart"
    # Validate existing XML before editing either configuration file.
    if rc.exists():
        original = rc.read_text()
    elif remove:
        original = None
    elif system_rc.exists():
        original = system_rc.read_text()
    else:
        original = "<labwc_config><keyboard><default/></keyboard></labwc_config>"
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(original, parser=parser) if original is not None else None
    text = guard(auto.read_text(), remove=True) if auto.exists() else ""
    lines = text.splitlines()
    owned = lambda line: (
        "# bite-launcher: managed startup" in line
        or ("bitedj-launcher" in line and not line.lstrip().startswith("#"))
        or ("/usr/local/bin/bite-launcher-open boot" in line)
        or ("bite-mode boot" in line)
    )
    if not remove:
        for path in (rc, auto):
            backup = path.with_name(path.name + ".pre-bite-unified")
            if path.exists() and not backup.exists():
                backup.write_bytes(path.read_bytes())
        if "removed_autostart" not in data:
            data["removed_autostart"] = [line for line in lines if owned(line)]
        cleaned = []
        for line in lines:
            if owned(line):
                continue
            if line.strip().startswith("/usr/local/bin/djrig-launch"):
                line = "# bite-launcher-disabled: " + line
            cleaned.append(line)
        # Gate desktop clients at their source, including a user's copied autostart.
        write(auto, guard("\n".join([BOOT] + cleaned) + "\n"))
    else:
        cleaned = [line.removeprefix("# bite-launcher-disabled: ") for line in lines if not owned(line)]
        # Old launcher and macro startup entries are intentionally not restored:
        # their executables are being uninstalled. Other user lines remain.
        write(auto, "\n".join(cleaned) + "\n")

    if root is None:
        if remove:
            record.unlink(missing_ok=True)
            (record.parent / "managed-boot").unlink(missing_ok=True)
        return
    ns = root.tag.split("}")[0] + "}" if "}" in root.tag else ""
    if ns:
        ET.register_namespace("", ns[1:-1])
    keyboards = [node for node in root if isinstance(node.tag, str) and local(node.tag) == "keyboard"]
    keyboard = keyboards[0] if keyboards else ET.SubElement(root, ns + "keyboard")
    is_binding = lambda node: isinstance(node.tag, str) and local(node.tag) == "keybind"
    # Preserve any user's C-A-d binding on first install; replacement is deliberate.
    if not remove and "old_ctrl_alt_d" not in data:
        data["old_ctrl_alt_d"] = [
            ET.tostring(node, encoding="unicode") for kb in keyboards for node in kb
            if is_binding(node) and node.get("key", "").lower() == "c-a-d"
            and not any(a.get("command") == COMMAND for a in node)
        ]
    for kb in keyboards or [keyboard]:
        for node in list(kb):
            if not is_binding(node):
                continue
            ours = any(a.get("command") == COMMAND for a in node)
            if ours or (not remove and node.get("key", "").lower() == "c-a-d"):
                kb.remove(node)
    if remove:
        existing = any(is_binding(node) and node.get("key", "").lower() == "c-a-d" for kb in keyboards for node in kb)
        if not existing:
            for old in data.get("old_ctrl_alt_d", []):
                keyboard.append(ET.fromstring(old))
    else:
        bind = ET.SubElement(keyboard, ns + "keybind", {"key": "C-A-d"})
        ET.SubElement(bind, ns + "action", {"name": "Execute", "command": COMMAND})
    ET.indent(root)
    write(rc, '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n")
    if not remove:
        write(record, json.dumps(data, indent=2) + "\n")
        (record.parent / "managed-boot").touch()
    else:
        record.unlink(missing_ok=True)
        (record.parent / "managed-boot").unlink(missing_ok=True)


if __name__ == "__main__":
    configure(Path.home(), remove="--remove" in sys.argv)
