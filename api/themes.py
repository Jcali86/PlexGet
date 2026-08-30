"""Looks the owner makes for the household.

The shipped looks live in CSS. These are the owner's own: named in the app,
built from a handful of colours and an optional wallpaper, stored under
data/ where nothing is ever committed, and offered to everyone in the same
picker as the built-ins. Only the owner can make or remove one - the guard
handles that for free, since the writing endpoints sit in the owner tier -
but anybody in the household can wear one.

Six colours are asked for; the rest are derived. A theme needs a dozen
variables to look right, and a form with a dozen wells is how nobody makes
a theme at all.
"""

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
STORE = DATA / "themes.json"
ART = DATA / "themes"

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# The six the owner picks; everything else is arithmetic.
ASKED = ("bg", "surface", "border", "text", "accent", "red")


def _mix(a, b, t):
    """Hex colour a moved toward hex colour b by t."""
    av = [int(a[i:i+2], 16) for i in (1, 3, 5)]
    bv = [int(b[i:i+2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(av, bv))


def _luma(c):
    r, g, b = (int(c[i:i+2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def derive(colors):
    """The full variable set, from the six that were chosen."""
    c = dict(colors)
    c["surface2"] = _mix(c["surface"], c["text"], 0.06)
    c["dim"] = _mix(c["text"], c["bg"], 0.42)
    c["accent_dark"] = _mix(c["accent"], "#000000", 0.18)
    c["on_accent"] = "#101010" if _luma(c["accent"]) > 140 else "#ffffff"
    c["top"] = _mix(c["bg"], "#000000", 0.35)
    c["green"] = "#4ade80"
    return c


def _load():
    try:
        raw = json.loads(STORE.read_text())
    except (OSError, ValueError):
        return []
    return raw if isinstance(raw, list) else []


def themes():
    """Every owner-made look, colours fully derived, ready for the page."""
    out = []
    for entry in _load():
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug", "")
        colors = entry.get("colors", {})
        if not (SLUG.match(str(slug)) and isinstance(colors, dict)
                and all(HEX.match(str(colors.get(k, ""))) for k in ASKED)):
            continue
        out.append({
            "slug": slug,
            "name": entry.get("name") or slug,
            "colors": derive({k: colors[k].lower() for k in ASKED}),
            "wallpaper": f"/themes/art/{slug}.png" if (ART / f"{slug}.png").is_file() else "",
        })
    return out


def save(name, colors, wallpaper_png=None):
    """Create or replace a look. Returns its slug, or raises ValueError."""
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("give the look a name")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
    if not SLUG.match(slug):
        raise ValueError("that name does not make a usable identifier")
    if not (isinstance(colors, dict)
            and all(HEX.match(str(colors.get(k, ""))) for k in ASKED)):
        raise ValueError("six colours are needed, each as #rrggbb")

    DATA.mkdir(parents=True, exist_ok=True)
    kept = [e for e in _load() if e.get("slug") != slug]
    kept.append({"slug": slug, "name": name,
                 "colors": {k: colors[k].lower() for k in ASKED}})
    STORE.write_text(json.dumps(kept, indent=1))

    if wallpaper_png:
        ART.mkdir(parents=True, exist_ok=True)
        (ART / f"{slug}.png").write_bytes(wallpaper_png)
    return slug


def remove(slug):
    """Take a look away, wallpaper and all."""
    if not SLUG.match(str(slug)):
        return False
    kept = [e for e in _load() if e.get("slug") != slug]
    changed = len(kept) != len(_load())
    if changed:
        STORE.write_text(json.dumps(kept, indent=1))
    art = ART / f"{slug}.png"
    if art.is_file():
        art.unlink()
    return changed


def art_path(slug):
    """The wallpaper file for a slug, or None - never a path from outside ART."""
    if not SLUG.match(str(slug)):
        return None
    p = ART / f"{slug}.png"
    return p if p.is_file() else None
