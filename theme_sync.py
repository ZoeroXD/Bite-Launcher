"""Read BiteDJ's inactive Play-tab background locally, without editing its files.

Used once by the installer and once per launcher process. No daemon, network
request, polling, or persistent color cache. Unsupported QSS fails to GRAY.
"""
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import stat

from launcher_palette import GRAY

NEAR_BLACK_MAX = 16
MAX_THEME_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Theme:
    color: str
    source: str = ""
    line: int = 0
    detected: str = ""
    fallback: bool = True
    reason: str = ""

    def summary(self):
        origin = f"{self.source}:{self.line}" if self.source else "no theme file"
        return f"Bite Launcher accent: {self.color} ({origin}; {self.reason})"


def rgba(value):
    """Return an RGBA tuple for supported solid QSS colors; never evaluate text."""
    value = value.strip().lower()
    names = {"black": "#000000", "white": "#ffffff", "gray": "#808080",
             "grey": "#808080", "transparent": "#00000000"}
    value = names.get(value, value)
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5)) + (255,)
    if re.fullmatch(r"#[0-9a-f]{8}", value):
        # Qt's eight-digit notation is #AARRGGBB.
        return tuple(int(value[i:i + 2], 16) for i in (3, 5, 7, 1))
    match = re.fullmatch(r"(rgb|rgba)\(([^()]*)\)", value)
    if match:
        fields = [part.strip() for part in match[2].split(",")]
        if len(fields) != (4 if match[1] == "rgba" else 3):
            raise ValueError("invalid RGB color")
        rgb = []
        for field in fields[:3]:
            number = float(field[:-1]) * 255 / 100 if field.endswith("%") else float(field)
            if not 0 <= number <= 255:
                raise ValueError("RGB channel out of range")
            rgb.append(round(number))
        alpha = 1.0
        if len(fields) == 4:
            alpha = float(fields[3][:-1]) / 100 if fields[3].endswith("%") else float(fields[3])
        if not 0 <= alpha <= 1:
            raise ValueError("alpha out of range")
        return tuple(rgb) + (round(alpha * 255),)
    raise ValueError("unsupported solid background color")


def selector_rank(selector, parent=False):
    """Conservative subset for the default, unselected Play tab (not hover).

    Account for common direct overrides as well as the transparent button on
    #TabControls. Never select [value="1"], :pressed, :hover, or other tabs.
    """
    selector = re.sub(r"\s+", " ", selector.strip()).replace("'", '"')
    if parent:
        return (1, 0, 0) if selector == "#TabControls" else None
    selector = re.sub(r'\[\s*value\s*=\s*"?0"?\s*\]', '[value="0"]', selector)
    allowed = {
        "*", "WPushButton", 'WPushButton[value="0"]',
        "#TabControls WPushButton", '#TabControls WPushButton[value="0"]',
        "#TabButtonPlay", '#TabButtonPlay[value="0"]',
        "WPushButton#TabButtonPlay", 'WPushButton#TabButtonPlay[value="0"]',
        "#TabControls #TabButtonPlay", '#TabControls #TabButtonPlay[value="0"]',
        "#TabControls WPushButton#TabButtonPlay",
        '#TabControls WPushButton#TabButtonPlay[value="0"]',
    }
    if selector not in allowed:
        return None
    return (selector.count("#"), selector.count("["), int("WPushButton" in selector))


def read_accent(text):
    """Resolve supported QSS cascade; return visible RGB hex and definition line."""
    # Preserve positions so diagnostics still refer to the original file.
    text = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m[0]), text, flags=re.S)
    if text.count("{") != text.count("}") or "/*" in text:
        raise ValueError("malformed stylesheet")
    winners = {}
    offset = 0
    while offset < len(text):
        opening = text.find("{", offset)
        if opening < 0:
            if text[offset:].strip():
                raise ValueError("unsupported stylesheet content")
            break
        closing = text.find("}", opening + 1)
        selectors = text[offset:opening]
        body = text[opening + 1:closing]
        if "{" in body or "}" in selectors:
            raise ValueError("nested or malformed stylesheet rules")
        # Explicit scanning avoids regex backtracking on a large malformed file.
        offset = closing + 1
        for selector in selectors.split(","):
            for parent in (True, False):
                rank = selector_rank(selector, parent)
                if rank is None:
                    continue
                for prop in re.finditer(r"(?:^|;)\s*(background(?:-color|-image)?)\s*:\s*([^;]+)",
                                        body):
                    # A gradient/image on a relevant default selector is not a solid color.
                    value = prop[2].strip()
                    if prop[1] == "background-image" and value.lower() == "none":
                        continue
                    position = opening + 1 + prop.start(2)
                    key = (rank, position)
                    if parent not in winners or key >= winners[parent][0]:
                        winners[parent] = (key, value, text.count("\n", 0, position) + 1)
    if True not in winners:
        raise ValueError("no supported #TabControls background definition")
    _, base, line = winners[True]
    rgb = rgba(base)
    if rgb[3] != 255:
        raise ValueError("tab bar background is not opaque")
    if False in winners:
        _, value, foreground_line = winners[False]
        foreground = rgba(value)
        if foreground[3]:
            alpha = foreground[3] / 255
            rgb = tuple(round(foreground[i] * alpha + rgb[i] * (1 - alpha)) for i in range(3))
            line = foreground_line
    return "#" + "".join(f"{c:02x}" for c in rgb[:3]), line


def candidates(conf, home=None):
    home = Path(home or Path.home())

    def expand(value):
        if value == "~" or value.startswith("~/"):
            return home / value[2:] if value != "~" else home
        return Path(value)

    explicit = conf.get("BITEDJ_THEME_FILE", "").strip()
    if explicit:
        return [expand(explicit)]
    app = expand(conf.get("BITEDJ_APP", "~/bitedj/build/mixxx")).resolve()
    result = []
    # Stay associated with the configured executable, not another Mixxx install.
    for base in list(app.parents)[:3]:
        for relative in ("res/skins/BiteDJ/style.qss", "share/bitedj/skins/BiteDJ/style.qss",
                         "share/mixxx/skins/BiteDJ/style.qss"):
            path = base / relative
            if path not in result:
                result.append(path)
    return result


def detect(conf, home=None):
    try:
        paths = candidates(conf, home)
    except (OSError, ValueError, RuntimeError) as error:
        return Theme(GRAY, reason=f"invalid theme path: {error}; using existing GRAY")
    for path in paths:
        try:
            if not path.exists():
                continue
            if not stat.S_ISREG(path.stat().st_mode):
                raise ValueError("theme is not a regular file")
            with path.open("rb") as stream:
                data = stream.read(MAX_THEME_BYTES + 1)
            if len(data) > MAX_THEME_BYTES:
                raise ValueError("theme exceeds the 1 MiB limit")
            color, line = read_accent(data.decode("utf-8-sig"))
            channels = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
            if max(channels) <= NEAR_BLACK_MAX:
                return Theme(GRAY, str(path), line, color, True,
                             "black/near-black theme; using existing GRAY")
            return Theme(color, str(path), line, color, False, "inactive Play-tab background")
        except (OSError, ValueError) as error:
            return Theme(GRAY, str(path), reason=f"{error}; using existing GRAY")
    return Theme(GRAY, str(paths[0]) if paths else "",
                 reason="BiteDJ theme not found; using existing GRAY")


if __name__ == "__main__":
    import argparse
    import bite_runtime
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable detection result")
    args = parser.parse_args()
    theme = detect(bite_runtime.config())
    print(json.dumps(asdict(theme), indent=2) if args.json else theme.summary())
