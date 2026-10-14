#!/usr/bin/env python3
"""Guard stock Pi desktop autostart commands before they can display a desktop."""
import argparse
from pathlib import Path
import shlex

LAUNCHER = "/usr/local/bin/bitedj-launcher"
BEGIN = "# bite-launcher: begin desktop guard"
END = "# bite-launcher: end desktop guard"


def unguard(text):
    lines = text.splitlines(keepends=True)
    result = []
    index = 0
    while index < len(lines):
        if lines[index].strip() != BEGIN:
            result.append(lines[index])
            index += 1
            continue
        # Our block has exactly five lines. Never discard an edited/malformed block.
        if (index + 4 >= len(lines) or not lines[index + 1].startswith("if ")
                or lines[index + 3].strip() != "fi"
                or lines[index + 4].strip() != END):
            raise ValueError("Edited Bite Launcher desktop guard; restore the backup before reinstalling")
        result.append(lines[index + 2])
        index += 5
    return "".join(result)


def desktop_command(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return False
    if not words:
        return False
    if words[-1] == "&":
        words.pop()
    # Do not accidentally suppress another command joined on the same line.
    if any(word in ("&", "&&", "||", ";", "|") for word in words):
        return False
    if words and Path(words[0]).name == "exec":
        words.pop(0)
    if words and Path(words[0]).name == "lwrespawn":
        words.pop(0)
    if not words:
        return False
    executable = Path(words[0]).name
    return (executable in ("wf-panel-pi", "pcmanfm-pi")
            or (executable == "pcmanfm" and "--desktop" in words)
            or (executable == "swaybg" and any(x in words for x in ("-i", "--image"))))


def guard(text, remove=False):
    text = unguard(text)
    if remove:
        return text
    condition = (f'if [ ! -x {shlex.quote(LAUNCHER)} ] || '
                 '[ ! -e "$HOME/.config/bite-launcher/managed-boot" ] || '
                 '[ -e "$HOME/.config/bite-launcher/autostart-off" ]; then\n')
    result = []
    for line in text.splitlines(keepends=True):
        if desktop_command(line):
            result.extend([BEGIN + "\n", condition, line.rstrip("\n") + "\n", "fi\n", END + "\n"])
        else:
            result.append(line)
    return "".join(result)


def configure_system(path, remove=False):
    if not path.exists():
        return
    original = path.read_text()
    changed = guard(original, remove=remove)
    if changed == original:
        return
    backup = path.with_name(path.name + ".pre-bite-v32")
    if not remove and not backup.exists():
        backup.write_bytes(path.read_bytes())
    temporary = path.with_name(path.name + ".bite-new")
    temporary.write_text(changed)
    temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true")
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("/etc/xdg/labwc/autostart")])
    args = parser.parse_args()
    for file in args.paths:
        configure_system(file, remove=args.remove)
